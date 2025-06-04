from __future__ import annotations
from aiofiles.os import path as aiopath, rename as aiorename, makedirs
from aioshutil import copy
from asyncio import sleep, gather
from logging import getLogger
from natsort import natsorted
from os import path as ospath, walk
from PIL import Image
from pyrogram.errors import FloodWait, RPCError
from pyrogram.types import InputMediaVideo, InputMediaDocument, InputMediaPhoto, Message
from re import match as re_match, search, compile as re_compile, IGNORECASE, escape
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type, RetryError
from time import time

from bot import bot, bot_dict, bot_lock, config_dict, DEFAULT_SPLIT_SIZE, LOGGER
from bot.helper.ext_utils.bot_utils import sync_to_async, default_button
from bot.helper.ext_utils.files_utils import clean_unwanted, clean_target, get_path_size, is_archive, get_base_name
from bot.helper.ext_utils.status_utils import get_readable_file_size, get_readable_time
from bot.helper.ext_utils.media_utils import create_thumbnail, take_ss, get_document_type, get_media_info, get_audio_thumb, post_media_info, GenSS
from bot.helper.ext_utils.shortenurl import short_url
from bot.helper.listeners import tasks_listener as task
from bot.helper.stream_utils.file_properties import gen_link
from bot.helper.telegram_helper.button_build import ButtonMaker
from bot.helper.telegram_helper.message_utils import deleteMessage, handle_message


LOGGER = getLogger(__name__)


class TgUploader:
    def __init__(self, listener: task.TaskListener, path: str, size: int):
        self._last_uploaded = 0
        self._processed_bytes = 0
        self._listener = listener
        self._path = path
        self._start_time = time()
        self._is_cancelled = False
        self._thumb = self._listener.thumb or ospath.join('thumbnails', f'{self._listener.user_id}.jpg')
        self._msgs_dict = {}
        self._is_corrupted = False
        self._size = size
        self._media_dict = {'videos': {}, 'documents': {}}
        self._last_msg_in_group = False
        self._client = None
        self._send_msg = None
        self._up_path = ''
        self._leech_log = config_dict['LEECH_LOG']
        self._file_metadata = {}

    async def _upload_progress(self, current, _):
        if self._is_cancelled:
            self._client.stop_transmission()
        chunk_size = current - self._last_uploaded
        self._last_uploaded = current
        self._processed_bytes += chunk_size

    async def upload(self, o_files, m_size):
        await self._user_settings()
        await self._msg_to_reply()
        corrupted_files = total_files = 0
        for dirpath, _, files in sorted(await sync_to_async(walk, self._path)):
            if dirpath.endswith('/yt-dlp-thumb'):
                continue
            for file_ in natsorted(files):
                self._up_path = ospath.join(dirpath, file_)
                if file_.lower().endswith(tuple(self._listener.extensionFilter)) or file_.startswith('Thumb'):
                    if not file_.startswith('Thumb'):
                        await clean_target(self._up_path)
                    continue
                try:
                    f_size = await get_path_size(self._up_path)
                    if self._listener.seed and file_ in o_files and f_size in m_size:
                        continue
                    if f_size == 0:
                        corrupted_files += 1
                        LOGGER.error('%s size is zero, telegram don\'t upload zero size files', self._up_path)
                        continue
                    if self._is_cancelled:
                        return
                    caption = await self._prepare_file(file_, dirpath)
                    if self._last_msg_in_group:
                        group_lists = [x for v in self._media_dict.values() for x in v.keys()]
                        match = re_match(r'.+(?=\.0*\d+$)|.+(?=\.part\d+\..+$)', self._up_path)
                        if not match or match and match.group(0) not in group_lists:
                            for key, value in list(self._media_dict.items()):
                                for subkey, msgs in list(value.items()):
                                    if len(msgs) > 1:
                                        await self._send_media_group(msgs, subkey, key)
                    self._last_msg_in_group = False
                    self._last_uploaded = 0
                    await self._upload_file(caption, file_)
                    total_files += 1
                    if self._is_cancelled:
                        return
                    if not self._is_corrupted and (self._listener.isSuperChat or self._leech_log):
                        self._msgs_dict[self._send_msg.link] = file_
                    await sleep(3)
                except Exception as err:
                    if isinstance(err, RetryError):
                        LOGGER.info('Total Attempts: %s', err.last_attempt.attempt_number, exc_info=True)
                        corrupted_files += 1
                        self._is_corrupted = True
                        err = err.last_attempt.exception()
                    LOGGER.error('%s. Path: %s', err, self._up_path)
                    corrupted_files += 1
                    if self._is_cancelled:
                        return
                    continue
                finally:
                    if not self._is_cancelled and await aiopath.exists(self._up_path) and (not self._listener.seed or self._listener.newDir or
                        dirpath.endswith('/splited_files_mltb') or '/copied_mltb/' in self._up_path):
                        await clean_target(self._up_path)

        for key, value in list(self._media_dict.items()):
            for subkey, msgs in list(value.items()):
                if len(msgs) > 1:
                    await self._send_media_group(msgs, subkey, key)
        if self._is_cancelled:
            return
        if self._listener.seed and not self._listener.newDir:
            await clean_unwanted(self._path)
        if total_files == 0:
            await self._listener.onUploadError(f"No files to upload or in blocked list ({', '.join(self._listener.extensionFilter[2:])})!")
            return
        if total_files <= corrupted_files:
            await self._listener.onUploadError('Files Corrupted or unable to upload. Check logs!')
            return
        LOGGER.info('Leech Completed: %s', self._listener.name)
        await self._listener.onUploadComplete(None, self._size, self._msgs_dict, total_files, corrupted_files)

    @retry(wait=wait_exponential(multiplier=2, min=4, max=8), stop=stop_after_attempt(4), retry=retry_if_exception_type(Exception))
    async def _upload_file(self, caption, file, force_document=False):
        if self._thumb and not await aiopath.exists(self._thumb):
            self._thumb = None
        thumb, ss_image = self._thumb, None
        if self._is_cancelled:
            return
        try:
            async with bot_lock:
                self._client = (bot_dict['USERBOT'] if bot_dict['IS_PREMIUM'] and await get_path_size(self._up_path) > DEFAULT_SPLIT_SIZE
                                or bot_dict['USERBOT'] and config_dict['USERBOT_LEECH'] else bot)
            is_video, is_audio, is_image = await get_document_type(self._up_path)
            if not is_image and thumb is None:
                file_name = ospath.splitext(file)[0]
                thumb_path = ospath.join(self._path, 'yt-dlp-thumb', f'{file_name}.jpg')
                if await aiopath.isfile(thumb_path):
                    thumb = thumb_path
                elif is_audio and not is_video:
                    thumb = await get_audio_thumb(self._up_path)
            if is_video:
                duration = (await get_media_info(self._up_path))[0]
                ss_image = await self._gen_ss(self._up_path)
                if self._listener.screenShots:
                    await self._send_screenshots()
                if not thumb:
                    thumb = await create_thumbnail(self._up_path, duration)

            if self._listener.as_doc or force_document or (not is_video and not is_audio and not is_image):
                key = 'documents'
                if self._is_cancelled:
                    return
                self._send_msg = await self._client.send_document(chat_id=self._send_msg.chat.id,
                                                                  document=self._up_path,
                                                                  thumb=thumb,
                                                                  caption=caption,
                                                                  disable_notification=True,
                                                                  progress=self._upload_progress,
                                                                  reply_to_message_id=self._send_msg.id)
            elif is_video:
                key = 'videos'
                if thumb:
                    with Image.open(thumb) as img:
                        width, height = img.size
                else:
                    width, height = 480, 320
                if not self._up_path.upper().endswith(('.MKV', '.MP4')):
                    dirpath, file_ = ospath.split(self._up_path)
                    if self._listener.seed and not self._listener.newDir and not dirpath.endswith('/splited_files_mltb'):
                        dirpath = ospath.join(dirpath, 'copied_mltb')
                        await makedirs(dirpath, exist_ok=True)
                        new_path = ospath.join(dirpath, f'{ospath.splitext(file_)[0]}.mp4')
                        self._up_path = await copy(self._up_path, new_path)
                    else:
                        new_path = f'{ospath.splitext(self._up_path)[0]}.mp4'
                        await aiorename(self._up_path, new_path)
                        self._up_path = new_path
                if self._is_cancelled:
                    return
                self._send_msg = await self._client.send_video(chat_id=self._send_msg.chat.id,
                                                               video=self._up_path,
                                                               caption=caption,
                                                               duration=duration,
                                                               width=width,
                                                               height=height,
                                                               thumb=thumb,
                                                               supports_streaming=True,
                                                               disable_notification=True,
                                                               progress=self._upload_progress,
                                                               reply_to_message_id=self._send_msg.id)
            elif is_audio:
                key = 'audios'
                duration, artist, title = await get_media_info(self._up_path)
                if self._is_cancelled:
                    return
                self._send_msg = await self._client.send_audio(chat_id=self._send_msg.chat.id,
                                                               audio=self._up_path,
                                                               caption=caption,
                                                               duration=duration,
                                                               performer=artist,
                                                               title=title,
                                                               thumb=thumb,
                                                               disable_notification=True,
                                                               progress=self._upload_progress,
                                                               reply_to_message_id=self._send_msg.id)
            else:
                key = 'photos'
                if self._is_cancelled:
                    return
                self._send_msg = await bot.send_photo(chat_id=self._send_msg.chat.id,
                                                      photo=self._up_path,
                                                      caption=caption,
                                                      disable_notification=True,
                                                      progress=self._upload_progress,
                                                      reply_to_message_id=self._send_msg.id)
            if self._is_cancelled:
                return
            await self._final_message(ss_image, bool(is_video or is_audio))
            if self._send_pm:
                await self._copy_Leech(self._listener.user_id, self._send_msg)
            if self._listener.upDest:
                await self._copy_Leech(self._listener.upDest, self._send_msg)

            if not self._is_cancelled and self._media_group and (self._send_msg.video or self._send_msg.document):
                if match := re_match(r'.+(?=\.0*\d+$)|.+(?=\.part\d+\..+$)', self._up_path):
                    subkey = match.group(0)
                    if subkey in self._media_dict[key].keys():
                        self._media_dict[key][subkey].append(self._send_msg)
                    else:
                        self._media_dict[key][subkey] = [self._send_msg]
                    msgs = self._media_dict[key][subkey]
                    if len(msgs) == 10:
                        await self._send_media_group(msgs, subkey, key)
                    else:
                        self._last_msg_in_group = True

            if not self._thumb and thumb:
                await clean_target(thumb)
        except FloodWait as f:
            LOGGER.warning(f, exc_info=True)
            await sleep(f.value * 1.2)
        except Exception as err:
            if not self._thumb and thumb:
                await clean_target(thumb)
            err_type = 'RPCError: ' if isinstance(err, RPCError) else ''
            LOGGER.error('%s%s. Path: %s', err_type, err, self._up_path)
            if 'Telegram says: [400' in str(err) and key != 'documents':
                LOGGER.error('Retrying As Document. Path: %s', self._up_path, exc_info=True)
                return await self._upload_file(caption, file, True)
            raise err

    async def _user_settings(self):
        self._media_group = self._listener.user_dict.get('media_group', False) or ('media_group' not in self._listener.user_dict and config_dict['MEDIA_GROUP'])
        self._cap_mode = self._listener.user_dict.get('caption_style', 'mono')
        self._log_title = self._listener.user_dict.get('log_title', False)
        self._send_pm = True
        self._enable_ss = self._listener.user_dict.get('enable_ss', False)
        self._user_caption = self._listener.user_dict.get('captions', False)
        self._user_fnamecap = self._listener.user_dict.get('fnamecap', True)
        if config_dict['AUTO_THUMBNAIL']:
            for dirpath, _, files in await sync_to_async(walk, self._path):
                for file in files:
                    filepath = ospath.join(dirpath, file)
                    if file.startswith('Thumb') and (await get_document_type(filepath))[-1]:
                        self._thumb = filepath
                        break

    @property
    def speed(self):
        try:
            return self._processed_bytes / (time() - self._start_time)
        except:
            return 0

    @property
    def processed_bytes(self):
        return self._processed_bytes

    async def cancel_task(self):
        self._is_cancelled = True
        LOGGER.info('Cancelling Upload: %s', self._listener.name)
        await self._listener.onUploadError('Upload stopped by user!')

    # ================================================== UTILS ==================================================
    async def _prepare_file(self, file_, dirpath):
        if self._user_caption and any(pattern in self._user_caption for pattern in [
            "{BL}", "{file_name}", "{file_size}", "{file_caption}", 
            "{languages}", "{subtitles}", "{duration}", "{ott}", "{resolution}", 
            "{name}", "{year}", "{quality}", "{season}", "{episode}", 
            "{audio}", "{lib}", "{extension}", "{shortsub}"
        ]):
            self._file_metadata = await self._extract_media_info(self._up_path)

        caption = await self._caption_mode(file_)
        if len(file_) > 60:
            if is_archive(file_):
                name = get_base_name(file_)
                ext = file_.split(name, 1)[1]
            elif match := re_match(r'.+(?=\..+\.0*\d+$)|.+(?=\.part\d+\..+$)', file_):
                name = match.group(0)
                ext = file_.split(name, 1)[1]
            elif len(fsplit := ospath.splitext(file_)) > 1:
                name, ext = fsplit[0], fsplit[1]
            else:
                name, ext = file_, ''
            name = name[:60 - len(ext)]
            if self._listener.seed and not self._listener.newDir and not dirpath.endswith('/splited_files_mltb'):
                dirpath = ospath.join(dirpath, 'copied_mltb')
                await makedirs(dirpath, exist_ok=True)
                new_path = ospath.join(dirpath, f'{name}{ext}')
                self._up_path = await copy(self._up_path, new_path)
            else:
                new_path = ospath.join(dirpath, f'{name}{ext}')
                await aiorename(self._up_path, new_path)
                self._up_path = new_path
        
        return caption

    async def _caption_mode(self, file):
        match self._cap_mode:
            case 'italic':
                caption = f'<i>{file}</i>'
            case 'bold':
                caption = f'<b>{file}</b>'
            case 'normal':
                caption = file
            case 'mono':
                caption = f'<code>{file}</code>'
        if self._user_caption:
            formatted_caption = self._user_caption
            # Process template placeholders
            if any(pattern in formatted_caption for pattern in ["{BL}", "{file_name}", "{file_size}", "{file_caption}",
                "{languages}", "{subtitles}", "{duration}", "{ott}", "{resolution}", "{name}", "{year}", 
                "{quality}", "{season}", "{episode}", "{audio}", "{lib}", "{extension}", "{shortsub}"]):

                import re
                from bot.helper.ext_utils.status_utils import get_readable_file_size

                # Basic replacements
                formatted_caption = formatted_caption.replace("{BL}", file)
                formatted_caption = formatted_caption.replace("{file_name}", file)
                formatted_caption = formatted_caption.replace("{file_caption}", file)

                # File size
                if "{file_size}" in formatted_caption and hasattr(self, '_size'):
                    formatted_caption = formatted_caption.replace("{file_size}", get_readable_file_size(self._size))

                # Extract components from filename using regex patterns
                # Try to identify year, season, episode numbers
                year_match = re.search(r'(?:^|\s|\(|\.|\[)(\d{4})(?:\)|\]|\s|$|\.)', file)
                season_match = re.search(r'(?:^|\s|\.|_)S(\d{1,2})(?:E\d{1,2})?(?:\s|\.|_|$)', file, re.IGNORECASE)
                episode_match = re.search(r'(?:^|\s|\.|_)S\d{1,2}E(\d{1,2})(?:\s|\.|_|$)', file, re.IGNORECASE)
                
                # Get clean name by removing known patterns
                clean_name = file
                # Remove extension
                clean_name = re.sub(r'\.\w+$', '', clean_name)
                # Remove year if found
                if year_match:
                    year = year_match.group(1)
                    clean_name = re.sub(r'(?:^|\s|\(|\.|\[)' + re.escape(year) + r'(?:\)|\]|\s|$|\.)', ' ', clean_name)
                else:
                    year = ""
                
                # Remove season/episode if found
                if season_match or episode_match:
                    if season_match:
                        season = f"S{int(season_match.group(1)):02d}"
                        clean_name = re.sub(r'(?:^|\s|\.|_)S\d{1,2}(?:E\d{1,2})?(?:\s|\.|_|$)', ' ', clean_name, flags=re.IGNORECASE)
                    else:
                        season = ""
                    
                    if episode_match:
                        episode = f"E{int(episode_match.group(1)):02d}"
                        # Already removed by season pattern
                    else:
                        episode = ""
                else:
                    season = ""
                    episode = ""
                
                # Remove common quality and resolution patterns
                clean_name = re.sub(r'\b\d{3,4}[pP]\b', '', clean_name)
                clean_name = re.sub(r'\b(?:WEB-?DL|WEB-?Rip|BluRay|BRRip|HDRip|DVDRip)\b', '', clean_name, flags=re.IGNORECASE)
                clean_name = re.sub(r'\b(?:[xh]26[45]|AAC|AC3|DTS|MA|ATMOS|EAC3|MP3|FLAC|OPUS|PCM)\b', '', clean_name, flags=re.IGNORECASE)
                clean_name = re.sub(r'\b(?:[EM]Sub|Soft[ -]?Subs|[28]CH|6CH)\b', '', clean_name, flags=re.IGNORECASE)
                
                # Clean up remaining brackets and multiple spaces
                clean_name = re.sub(r'[\[\(\{].*?[\]\)\}]', '', clean_name)
                clean_name = re.sub(r'\s+', ' ', clean_name).strip()
                
                # Use the clean name for {name}
                name = clean_name if clean_name else file
                
                formatted_caption = formatted_caption.replace("{name}", name)
                formatted_caption = formatted_caption.replace("{year}", year)
                formatted_caption = formatted_caption.replace("{season}", season)
                formatted_caption = formatted_caption.replace("{episode}", episode)

                # Extract resolution
                resolution_match = re.search(r'(\d{3,4}[pP])', file)
                resolution = resolution_match.group(1) if resolution_match else ""
                formatted_caption = formatted_caption.replace("{resolution}", resolution)

                # Extract quality (WEB-DL, BluRay, etc.)
                quality_match = re.search(r'(WEB-?DL|WEB-?Rip|BluRay|BRRip|HDRip|DVDRip)', file, re.IGNORECASE)
                quality = quality_match.group(1) if quality_match else ""
                formatted_caption = formatted_caption.replace("{quality}", quality)

                # Extract codec/lib information
                codec_match = re.search(r'([xh]26[45])', file, re.IGNORECASE)
                codec = codec_match.group(1) if codec_match else ""
                formatted_caption = formatted_caption.replace("{lib}", codec)

                # Extract audio information
                audio_match = re.search(r'(AAC|AC3|DTS|DDP|DD|MA|ATMOS|EAC3|MP3|FLAC|OPUS|PCM|WAV|6CH|[28]CH)', file, re.IGNORECASE)
                audio = audio_match.group(1) if audio_match else ""
                formatted_caption = formatted_caption.replace("{audio}", audio)

                # Extract subtitle information
                sub_match = re.search(r'([EM]Sub|Soft[ -]?Subs)', file, re.IGNORECASE)
                shortsub = sub_match.group(1) if sub_match else ""
                formatted_caption = formatted_caption.replace("{shortsub}", shortsub)
                formatted_caption = formatted_caption.replace("{subtitles}", shortsub)  # For now, use same as shortsub

                # Extract extension
                ext_match = re.search(r'(\.\w+)$', file)
                extension = ext_match.group(1) if ext_match else ""
                formatted_caption = formatted_caption.replace("{extension}", extension)

                # Try to detect OTT platform
                ott_patterns = {
                    'AMZN': r'\bAM[AZ]N\b|\bAMAZON\b',
                    'NF': r'\bNF\b|\bNETFLIX\b',
                    'DSNP': r'\bDSNP\b|\bDISNEY\+?\b',
                    'HMAX': r'\bHMAX\b|\bHBO\s*MAX\b',
                    'HULU': r'\bHULU\b',
                    'APLP': r'\bAPLP\b|\bAPPLE\s*(?:TV)?\+?\b',
                    'PCOK': r'\bPEACOCK\b|\bPCOK\b',
                    'PMNT': r'\bPARAMOUNT\+?\b|\bP\+\b',
                    'ZEE5': r'\bZEE5\b|\bZ5\b',
                    'HOTSTAR': r'\bHOTSTAR\b|\bHS\b'
                }
                
                ott = ""
                for platform, pattern in ott_patterns.items():
                    if re.search(pattern, file, re.IGNORECASE):
                        ott = platform
                        break
                
                formatted_caption = formatted_caption.replace("{ott}", ott)

                # Use file metadata for more accurate information if available
                if hasattr(self, '_file_metadata') and self._file_metadata:
                    # Duration from metadata
                    if 'duration' in self._file_metadata and "{duration}" in formatted_caption:
                        formatted_caption = formatted_caption.replace("{duration}", self._file_metadata['duration'])
                    
                    # Subtitles from metadata
                    if 'subtitles' in self._file_metadata and "{subtitles}" in formatted_caption:
                        formatted_caption = formatted_caption.replace("{subtitles}", self._file_metadata['subtitles'])
                    
                    # Languages from metadata
                    if 'languages' in self._file_metadata and "{languages}" in formatted_caption:
                        formatted_caption = formatted_caption.replace("{languages}", self._file_metadata['languages'])
                    
                    # Resolution from metadata (if available and more accurate)
                    if 'resolution' in self._file_metadata and "{resolution}" in formatted_caption and not resolution:
                        formatted_caption = formatted_caption.replace("{resolution}", 
                                                                    self._file_metadata.get('quality_name', 
                                                                                          self._file_metadata['resolution']))
                else:
                    # If no metadata available, handle these placeholders
                    if "{duration}" in formatted_caption:
                        # Try to get duration from the file directly if not already in metadata
                        duration = ""
                        if hasattr(self, '_up_path') and await aiopath.exists(self._up_path):
                            from bot.helper.ext_utils.media_utils import get_media_info
                            try:
                                media_duration, _, _ = await get_media_info(self._up_path)
                                if media_duration > 0:
                                    hours, remainder = divmod(media_duration, 3600)
                                    minutes, seconds = divmod(remainder, 60)
                                    duration = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
                            except Exception as e:
                                pass
                        formatted_caption = formatted_caption.replace("{duration}", duration)
                    
                    # Empty values for language if not already set
                    if "{languages}" in formatted_caption:
                        formatted_caption = formatted_caption.replace("{languages}", "")

            should_include_filename = self._user_fnamecap and not any(tag in self._user_caption for tag in ["{BL}", "{file_name}", "{file_caption}"])
            caption = f'''{caption}\n\n{formatted_caption}''' if should_include_filename else formatted_caption
        return caption

    async def _gen_ss(self, vid_path):
        if not self._enable_ss or self._is_cancelled:
            return
        ss = GenSS(self._listener.message, vid_path)
        await ss.file_ss()
        if ss.error:
            return
        return ss.rimage

    async def _extract_media_info(self, file_path):
        """Extract detailed media information for template processing"""
        if not await aiopath.exists(file_path):
            return {}
            
        from bot.helper.ext_utils.media_utils import get_media_info, get_document_type
        
        metadata = {}
        is_video, is_audio, is_image = await get_document_type(file_path)
        
        if is_video or is_audio:
            try:
                duration, artist, title = await get_media_info(file_path)
                if duration > 0:
                    hours, remainder = divmod(duration, 3600)
                    minutes, seconds = divmod(remainder, 60)
                    metadata['duration'] = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
                    metadata['duration_seconds'] = duration
                if artist:
                    metadata['artist'] = artist
                if title:
                    metadata['title'] = title
                    
                # Try to run ffprobe to get more info
                try:
                    from asyncio import create_subprocess_exec
                    from asyncio.subprocess import PIPE
                    
                    cmd = ['ffprobe', '-hide_banner', '-loglevel', 'error', '-print_format', 'json', '-show_format', '-show_streams', file_path]
                    process = await create_subprocess_exec(*cmd, stdout=PIPE, stderr=PIPE)
                    stdout, _ = await process.communicate()
                    
                    if process.returncode == 0 and stdout:
                        import json
                        result = json.loads(stdout)
                        
                        # Extract languages
                        languages = []
                        for stream in result.get('streams', []):
                            if stream.get('tags', {}).get('language'):
                                lang = stream['tags']['language']
                                if lang not in languages:
                                    languages.append(lang)
                        
                        if languages:
                            metadata['languages'] = ", ".join(languages)
                            
                        # Extract subtitle info
                        subtitles = []
                        for stream in result.get('streams', []):
                            if stream.get('codec_type') == 'subtitle':
                                if lang := stream.get('tags', {}).get('language'):
                                    subtitles.append(lang)
                        
                        if subtitles:
                            metadata['subtitles'] = ", ".join(subtitles)
                            
                        # Get video resolution
                        for stream in result.get('streams', []):
                            if stream.get('codec_type') == 'video':
                                if width := stream.get('width'):
                                    if height := stream.get('height'):
                                        metadata['resolution'] = f"{width}x{height}"
                                        if height <= 480:
                                            metadata['quality_name'] = '480p'
                                        elif height <= 720:
                                            metadata['quality_name'] = '720p'
                                        elif height <= 1080:
                                            metadata['quality_name'] = '1080p'
                                        elif height <= 1440:
                                            metadata['quality_name'] = '2K'
                                        elif height <= 2160:
                                            metadata['quality_name'] = '4K'
                                        else:
                                            metadata['quality_name'] = f"{height}p"
                                        break
                except Exception as e:
                    LOGGER.debug(f"Error extracting detailed media info: {e}")
            except Exception as e:
                LOGGER.debug(f"Error extracting media info: {e}")
                
        return metadata

    # ===========================================================================================================

    # ================================================= MESSAGE =================================================
    @handle_message
    async def _msg_to_reply(self):
        if self._leech_log and self._leech_log != self._listener.message.chat.id:
            caption = f'<b>▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n{self._listener.name}\n▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬</b>'
            if self._thumb and await aiopath.exists(self._thumb):
                self._send_msg: Message = await bot.send_photo(self._leech_log, photo=self._thumb, caption=caption)
            else:
                self._send_msg: Message = await bot.send_message(self._leech_log, caption, disable_web_page_preview=True)
            if config_dict['LEECH_INFO_PIN']:
                await self._send_msg.pin(both_sides=True)
        else:
            self._send_msg: Message = await bot.get_messages(self._listener.message.chat.id, self._listener.mid)
            if not self._send_msg or not self._send_msg.chat:
                self._send_msg = self._listener.message
        LOGGER.info(f"Send Msg Debug -> {self._send_msg}")
        if self._send_msg and self._log_title and self._listener.upDest:
            await self._copy_Leech(self._listener.upDest, self._send_msg)

    @handle_message
    async def _send_media_group(self, msgs: list[Message], subkey: str, key: str):
        msgs_list = await msgs[0].reply_to_message.reply_media_group(media=await self._get_input_media(subkey, key),
                                                                     quote=True, disable_notification=True)
        if self._send_pm:
            await self._copy_media_group(self._listener.user_id, msgs_list)
        if self._listener.upDest:
            await self._copy_media_group(self._listener.upDest, msgs_list)
        for msg in msgs:
            self._msgs_dict.pop(msg.link, None)
            await deleteMessage(msg)
        del self._media_dict[key][subkey]
        if self._listener.isSuperChat or self._leech_log:
            for m in msgs_list:
                self._msgs_dict[m.link] = m.caption.split('\n')[0] + ' ~ (Grouped)'
        self._send_msg = msgs_list[-1]

    @handle_message
    async def _send_screenshots(self):
        if isinstance(self._listener.screenShots, str):
            ss_nb = int(self._listener.screenShots)
        else:
            ss_nb = 10
        outputs = await take_ss(self._up_path, ss_nb)
        inputs = []
        if outputs:
            for m in outputs:
                if await aiopath.exists(m):
                    cap = m.rsplit('/', 1)[-1]
                    inputs.append(InputMediaPhoto(m, cap))
                else:
                    outputs.remove(m)
        if outputs:
            msgs_list = await self._send_msg.reply_media_group(media=inputs, quote=True, disable_notification=True)
            if self._send_pm:
                await self._copy_media_group(self._listener.user_id, msgs_list)
            if self._listener.upDest:
                await self._copy_media_group(self._listener.upDest, msgs_list)
            self._send_msg = msgs_list[-1]
            await gather(*[clean_target(m) for m in outputs])

    @handle_message
    async def _copy_media_group(self, chat_id: int, msgs: list[Message]):
        caption_tasks = [self._caption_mode(msg.caption.split('\n')[0]) for msg in msgs]
        captions = await gather(*caption_tasks)
        await bot.copy_media_group(chat_id=chat_id, from_chat_id=msgs[0].chat.id, message_id=msgs[0].id, captions=captions)

    @handle_message
    async def _copy_Leech(self, chat_id: int, message: Message):
        reply_markup = await default_button(message) if config_dict['SAVE_MESSAGE'] and self._listener.isSuperChat else message.reply_markup
        return await message.copy(chat_id, disable_notification=True, reply_markup=reply_markup,
                                  reply_to_message_id=message.reply_to_message.id if chat_id == message.chat.id else None)

    @handle_message
    async def _final_message(self, ss_image, media_info: bool=False):
        self._buttons = ButtonMaker()
        media_result = await post_media_info(self._up_path, self._size, ss_image) if media_info else None
        await clean_target(ss_image)
        if media_result:
            self._buttons.button_link('Media Info', media_result)
        if config_dict['SAVE_MESSAGE'] and self._listener.isSuperChat:
            self._buttons.button_data('Save Message', 'save', 'footer')
        # Only show stream button if user setting is enabled
        show_stream = self._listener.user_dict.get('show_stream_link', True)
        for mode, link in zip(['Stream', 'Download'], await gen_link(self._send_msg)):
            if link and (mode != 'Stream' or show_stream):
                self._buttons.button_link(mode, await sync_to_async(short_url, link, self._listener.user_id), 'header')
        self._send_msg = await bot.get_messages(self._send_msg.chat.id, self._send_msg.id)
        if (buttons := self._buttons.build_menu(2)) and (cmsg := await self._send_msg.edit_reply_markup(buttons)):
            self._send_msg = cmsg

    async def _get_input_media(self, subkey: str, key: str):
        imlist = []
        for msg in self._media_dict[key][subkey]:
            caption = await self._caption_mode(msg.caption.split('\n')[0])
            if key == 'videos':
                input_media = InputMediaVideo(media=msg.video.file_id, caption=caption)
            else:
                input_media = InputMediaDocument(media=msg.document.file_id, caption=caption)
            imlist.append(input_media)
        return imlist
    # ===========================================================================================================