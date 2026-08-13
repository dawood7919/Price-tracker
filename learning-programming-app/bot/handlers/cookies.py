"""Accept a cookies.txt from the bot owner via Telegram.

This is a convenience alternative to setting YTDLP_COOKIES_CONTENT and
redeploying. It is deliberately restricted to a single fixed owner ID
(TELEGRAM_OWNER_ID) — a cookies file is a live login session, and this bot
has no other per-user auth, so accepting one from *any* user would let a
stranger overwrite the shared session used for every download.
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from ..config import Config
from ..utils import write_cookies_file

logger = logging.getLogger(__name__)

MAX_COOKIES_BYTES = 256 * 1024  # a real cookies.txt is a few KB at most


def _is_owner(config: Config, update: Update) -> bool:
    return (
        config.telegram_owner_id is not None
        and update.effective_user is not None
        and update.effective_user.id == config.telegram_owner_id
    )


async def setcookies_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    config: Config = context.bot_data["config"]
    if not _is_owner(config, update):
        return  # silently ignore — don't advertise the feature to non-owners
    context.user_data["awaiting_cookies"] = True
    await update.message.reply_text(
        "📎 ابعتلي دلوقتي ملف cookies.txt (كملف)، أو الصق محتواه كرسالة نصية عادية."
    )


async def handle_cookies_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """If the owner is mid /setcookies, consume this message as the cookies
    payload and return True. Callers (message/document handlers) must skip
    their normal processing when this returns True."""
    message = update.message
    if message is None:
        return False
    config: Config = context.bot_data["config"]
    if not context.user_data.get("awaiting_cookies") or not _is_owner(config, update):
        return False

    context.user_data["awaiting_cookies"] = False

    content: str | None = None
    if message.document is not None:
        if message.document.file_size and message.document.file_size > MAX_COOKIES_BYTES:
            await message.reply_text("⛔ الملف كبير أوي — مش شكله ملف كوكيز.")
            return True
        tg_file = await message.document.get_file()
        raw = await tg_file.download_as_bytearray()
        try:
            content = bytes(raw).decode("utf-8")
        except UnicodeDecodeError:
            await message.reply_text("⛔ مقدرتش أقرأ الملف — لازم يكون نص عادي (UTF-8).")
            return True
    elif message.text:
        content = message.text

    if not content or not content.strip():
        await message.reply_text("⛔ مستلمتش أي محتوى.")
        return True

    path = write_cookies_file(config.download_dir, content)
    config.ytdlp_cookies_file = str(path)
    logger.info("Owner uploaded cookies via Telegram, saved to %s", path)
    await message.reply_text(
        "✅ اتحفظت الكوكيز وهتتستخدم من دلوقتي.\n"
        "⚠️ الديسك مؤقت في الخطة المجانية على Render — لو السيرفر عمل ريستارت "
        "هتحتاج تبعتها تاني بـ /setcookies."
    )
    return True


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await handle_cookies_upload(update, context):
        return
    if update.message is not None:
        await update.message.reply_text("مش قادر أتعامل مع الملفات دي — ابعتلي لينك فيديو بس.")
