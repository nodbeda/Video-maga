import re
    
    
class TelegramLink:
    def __init__(self, chat_id=None, topic_id=None, message_id=None, is_private=None):
        self.chat_id = chat_id
        self.topic_id = topic_id
        self.message_id = message_id
        self.is_private = is_private

def extract_details_tglink(link):
    pattern = re.compile(
        r"^(https?://)?(t\.me|telegram\.me|telegram\.dog)/(c/)?([a-zA-Z0-9_]+)/(\d+(?:-\d+)?)(?:/(\d+))?$"
    )
    match = pattern.match(link.strip())

    if not match:
        return TelegramLink()

    is_private = bool(match.group(3))
    chat_id = match.group(4)
    first = match.group(5)
    second = match.group(6)

    if is_private:
        chat_id = int("-100" + chat_id)

    if '-' in first:
        start_id, end_id = map(int, first.split('-'))
        topic_id = None
        message_id = (start_id, end_id)
    else:
        first = int(first)
        if is_private:
            topic_id = None
            message_id = first
        else:
            if second:
                topic_id = first
                message_id = int(second)
            else:
                topic_id = None
                message_id = first

    return TelegramLink(chat_id, topic_id, message_id, is_private)