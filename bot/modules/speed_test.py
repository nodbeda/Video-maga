from asyncio import gather
from pyrogram.filters import command
from pyrogram.handlers import MessageHandler
from pyrogram.types import Message
from speedtest import Speedtest

from bot import bot, LOGGER
from bot.helper.ext_utils.bot_utils import sync_to_async, new_task
from bot.helper.ext_utils.status_utils import get_readable_file_size
from bot.helper.telegram_helper.bot_commands import BotCommands
from bot.helper.telegram_helper.filters import CustomFilters
from bot.helper.telegram_helper.message_utils import auto_delete_message, sendMessage, deleteMessage, sendPhoto, editMessage


@new_task
async def speedtest(_, message: Message):
    msg = await sendMessage('<i>Running speed test...</i>', message)
    try:
        test = Speedtest()
        await sync_to_async(test.get_best_server)
        await sync_to_async(test.download)
        await sync_to_async(test.upload)
        await sync_to_async(test.results.share)
        result = await sync_to_async(test.results.dict)
        caption = f"""
➲ <b><i>SPEEDTEST INFO</i></b>
╭ <b>Upload:</b> <code>{get_readable_file_size(result['upload'] / 8)}/s</code>
├ <b>Download:</b>  <code>{get_readable_file_size(result['download'] / 8)}/s</code>
├ <b>Ping:</b> <code>{result['ping']} ms</code>
├ <b>Time:</b> <code>{result['timestamp']}</code>
├ <b>Data Sent:</b> <code>{get_readable_file_size(int(result['bytes_sent']))}</code>
╰ <b>Data Received:</b> <code>{get_readable_file_size(int(result['bytes_received']))}</code>

➲ <b><i>SPEEDTEST SERVER</i></b>
╭ <b>Name:</b> <code>{result['server']['name']}</code>
├ <b>Country:</b> <code>{result['server']['country']}, {result['server']['cc']}</code>
├ <b>Sponsor:</b> <code>{result['server']['sponsor']}</code>
├ <b>Latency:</b> <code>{result['server']['latency']}</code>
├ <b>Latitude:</b> <code>{result['server']['lat']}</code>
╰ <b>Longitude:</b> <code>{result['server']['lon']}</code>
"""
        await gather(deleteMessage(msg), sendPhoto(caption, message, result['share']))
    except Exception as e:
        LOGGER.error(e)
        await gather(editMessage(f'Failed running speedtest {e}', msg), auto_delete_message(message, msg))


bot.add_handler(MessageHandler(speedtest, filters=command(BotCommands.SpeedCommand) & CustomFilters.sudo))
