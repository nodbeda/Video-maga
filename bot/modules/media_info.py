from pyrogram.filters import command
from pyrogram.handlers import MessageHandler
from pyrogram.types import Message
from os import getcwd
import aiohttp
from bot.helper.ext_utils.bot_utils import cmd_exec
from os import path as ospath
from re import search as re_search
from aiohttp import ClientSession
from re import search as re_search
from shlex import split as ssplit
from aiofiles import open as aiopen
from aiofiles.os import remove as aioremove, path as aiopath, mkdir
from os import path as ospath, getcwd
from shlex import split as ssplit
from bot.helper.ext_utils.telegraph_helper import telegraph
from bot import bot, config_dict
from bot.helper.ext_utils.bot_utils import new_task
from bot.helper.ext_utils.links_utils import is_url, get_url_name, get_link, is_media
from bot.helper.ext_utils.status_utils import get_readable_file_size
from bot.helper.stream_utils.file_properties import gen_link
from bot.helper.telegram_helper.bot_commands import BotCommands
from bot.helper.telegram_helper.button_build import ButtonMaker
from bot.helper.telegram_helper.filters import CustomFilters
from bot.helper.telegram_helper.message_utils import sendMessage, sendPhoto, editMessage, copyMessage, deleteMessage
from bot.helper.video_utils.executor import get_metavideo

section_dict = {"General", "Video", "Audio", "Text", "Image"}
@new_task
async def gen_mediainfo(message:Message, link=None, media=None, mmsg=None):
    temp_send = await sendMessage('<i>Generating MediaInfo...</i>', message)
    try:
        path = "Mediainfo/"
        if not await aiopath.isdir(path):
            await mkdir(path)
        if link:
            filename = re_search(".+/(.+)", link).group(1)
            des_path = ospath.join(path, filename)
            headers = {"user-agent":"Mozilla/5.0 (Linux; Android 12; 2201116PI) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/107.0.0.0 Mobile Safari/537.36"}
            async with ClientSession() as session:
                async with session.get(link, headers=headers) as response:
                    async with aiopen(des_path, "wb") as f:
                        async for chunk in response.content.iter_chunked(10000000):
                            await f.write(chunk)
                            break
        elif media:
            des_path = ospath.join(path, media.file_name)
            if media.file_size <= 50000000:
                await mmsg.download(ospath.join(getcwd(), des_path))
            else:
                async for chunk in bot.stream_media(media, limit=5):
                    async with aiopen(des_path, "ab") as f:
                        await f.write(chunk)
        stdout, _, _ = await cmd_exec(ssplit(f'mediainfo "{des_path}"'))
        tc = f"<h4>📌 {ospath.basename(des_path)}</h4><br><br>"
        if len(stdout) != 0:
            tc += parseinfo(stdout)
    except Exception as e:
        LOGGER.error(e)
        await editMessage(temp_send, f"MediaInfo Stopped due to {str(e)}")
    finally:
        await aioremove(des_path)
    link_id = (await telegraph.create_page(title='MediaInfo X', content=tc))["path"]
    await editMessage(f"<b>MediaInfo:</b>\n\nLink: <b>Link :</b> https://graph.org/{link_id}", temp_send)


section_dict = {'General': '🗒', 'Video': '🎞', 'Audio': '🔊', 'Text': '🔠', 'Menu': '🗃'}
def parseinfo(out):
    tc = ''
    trigger = False
    for line in out.split('\n'):
        for section, emoji in section_dict.items():
            if line.startswith(section):
                trigger = True
                if not line.startswith('General'):
                    tc += '</pre><br>'
                tc += f"<h4>{emoji} {line.replace('Text', 'Subtitle')}</h4>"
                break
        if trigger:
            tc += '<br><pre>'
            trigger = False
        else:
            tc += line + '\n'
    tc += '</pre><br>'
    return tc


async def mediainfo(_, message:Message):
    rply = message.reply_to_message
    help_msg = "<b>Send Command By replying to media:</b>"
    if len(message.command) > 1 or rply and rply.text:
        link = rply.text if rply else message.command[1]
        return await gen_mediainfo(message, link)
    elif rply:
        if file := next(
            (
                i
                for i in [
                    rply.document,
                    rply.video,
                    rply.audio,
                    rply.voice,
                    rply.animation,
                    rply.video_note,
                ]
                if i is not None
            ),
            None,
        ):
            return await gen_mediainfo(message, None, file, rply)
        else:
            return await sendMessage(message, help_msg)
    else:
        return await sendMessage(message, help_msg)


bot.add_handler(MessageHandler(mediainfo, command(BotCommands.MediaInfoCommand) & CustomFilters.authorized))