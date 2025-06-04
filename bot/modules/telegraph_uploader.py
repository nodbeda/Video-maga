import re
import os
import asyncio
import subprocess

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup 

from bot import OWNER_ID, bot, LOGGER
from bot.helper.gk import get_media_from_message
from bot.helper.telegram_helper.message_utils import sendMessage
from bot.helper.telegram_helper.bot_commands import BotCommands



async def upload_to_cloud_function(client, message):
 try:
    msg = await sendMessage("Kindly wait...", message)
    if replym := message.reply_to_message:
       if media := get_media_from_message(replym):
          await msg.edit("Downloading Media...")
          dlpath = await client.download_media(message=replym)
          file_size = os.path.getsize(dlpath)  # Get file size in bytes
          file_size_mb = file_size / (1024 * 1024)  # Convert to MB
          if file_size_mb > 10 and message.from_user.id not in OWNER_ID:
             return await msg.edit('Media size is too large! SEND MEDIA UNDER 10 MB...')
             os.remove(dlpath)
             
          result = curl_upload(f"file=@{dlpath}")
          os.remove(dlpath)
       elif replym.text:
          text = replym.text
          result  = curl_upload(f"url={text}")
       else:
          return await msg.edit('Only reply to { Photo, Video, or Link } Under 10 MB ...')
    else:
       if len(message.command) == 2:
          link = message.command[1]
          result = curl_upload(f"url={link}")
       else:
          return await msg.edit(f'Wrong Format 🚫,\n\nHᴏᴡ Tᴏ Usᴇ ❓\n•reply to  Photo, Video   \n\n•or, /{message.command[0]} Link\n\n⚠️ NOTE! : SEND MEDIA UNDER 10 MB!')            
    if is_valid_url(result):
       LOGGER.info(f"Result - {result}")
       markup = [
          [InlineKeyboardButton("Open", url=result),
           InlineKeyboardButton("Share", url=f'https://telegram.me/share/url?url={result}')],
          [InlineKeyboardButton("✗", callback_data="gk close")]
       ]
       await msg.edit_text(text=result, reply_markup=InlineKeyboardMarkup(markup))
    else:
       await msg.edit("ERROR 🚫: Reply To Valid [Photo, Video, Document Or Link] To Upload On Cloud...")
 except Exception as e:
    LOGGER.error("upload", exc_info=True)

def curl_upload(path):
    curl_command = [
        "curl",
        "-F", path,
        "-F", "secret=",
        "https://envs.sh"
    ]
    result = subprocess.run(curl_command, capture_output=True, text=True)
    stdout_output = result.stdout.strip()  # Remove newlines/spaces here
    stderr_output = result.stderr
    return stdout_output  # Return the cleaned output

    
def is_valid_url(url):
    url_pattern = re.compile(
        r'^(https?:\/\/)?(www\.)?([a-zA-Z0-9-]+(\.[a-zA-Z]{2,})+)(\/[^\s]*)?$'
    )
    return bool(url_pattern.match(url))