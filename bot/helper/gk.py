from typing import Any
from pyrogram.types import Message, InputMediaVideo, InputMediaPhoto, InputMediaDocument, InputMediaAudio, InputMediaAnimation

from bot import LOGGER
from bot.helper.telegram_helper.links import extract_details_tglink




def get_media_from_message(message: "Message") -> Any:
    media_types = (
        "audio",
        "document",
        "photo",
        "sticker",
        "animation",
        "video",
        "voice",
        "video_note",
    )
    for attr in media_types:
        media = getattr(message, attr, None)
        if media:
            return media

async def mr_funct(client, message):
    if not ' ' in message.text:
        return await message.reply(f"**Wrong Format.\n\nCorrect Format:-** `/{message.command[0]} POST_LINK`")
    if not (reply_msg := message.reply_to_message):
        return await message.reply("Kindly Reply To A Message Which Contains Any (Video, Audio, Document, Photo, Animation) To Replace...")
    sent_msg = await message.reply("**Trying To Replace..**.")
    link = message.text.split()[1]
    data = extract_details_tglink(link)
    try:
        old_msg = await client.get_messages(chat_id=data.chat_id, message_ids=data.message_id)
    except Exception as e:
        await sent_msg.edit(e)
        LOGGER.error('replacw', exc_info=True)
        return
    new_media = get_media_from_message(reply_msg)
    if reply_msg.photo:
        MyInput = InputMediaPhoto
    elif reply_msg.document:
        MyInput = InputMediaDocument
    elif reply_msg.video:
        MyInput = InputMediaVideo
    elif reply_msg.audio:
        MyInput = InputMediaAudio
    elif reply_msg.animation:
        MyInput = InputMediaAnimation
    #elif reply_msg.text or reply_msg.caption:
    #    MyInput = None
    else:
        await sent_msg.edit("**Kindly Reply To Video, Audio, Document or Image Only To Replace it**")
        return 
    reply_markup = reply_msg.reply_markup
    caption = reply_msg.caption.html if reply_msg.caption else ''
    if MyInput:
        await old_msg.edit_media(MyInput(new_media.file_id, caption=caption), reply_markup=reply_markup)
    else:
        await old_msg.edit(reply_msg.text or reply_msg.caption, reply_markup=reply_markup)
    await sent_msg.edit("**Succefully Replaced Media...✅**")