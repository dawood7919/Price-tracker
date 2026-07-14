from telegram import InlineKeyboardButton, InlineKeyboardMarkup

_FORMAT_MAP = {
    "best": "bestvideo+bestaudio/best",
    "1080": "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
    "720": "bestvideo[height<=720]+bestaudio/best[height<=720]",
    "audio": "audio",
}


def resolve_format(code: str) -> str:
    return _FORMAT_MAP[code]


def quality_keyboard(request_id: str) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("Best Quality", callback_data=f"dl:{request_id}:best")],
        [InlineKeyboardButton("1080p", callback_data=f"dl:{request_id}:1080")],
        [InlineKeyboardButton("720p", callback_data=f"dl:{request_id}:720")],
        [InlineKeyboardButton("Audio Only", callback_data=f"dl:{request_id}:audio")],
        [InlineKeyboardButton("Cancel", callback_data=f"cancel:{request_id}:_")],
    ]
    return InlineKeyboardMarkup(buttons)
