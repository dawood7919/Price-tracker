import logging
from pathlib import Path
from typing import Optional

import aiohttp
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from utils.validators import human_readable_size

logger = logging.getLogger(__name__)

AUDIO_SUFFIXES = {".mp3", ".m4a", ".opus"}


async def _fetch_thumbnail(url: Optional[str]) -> Optional[bytes]:
    if not url:
        return None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status == 200:
                    return await response.read()
    except aiohttp.ClientError:
        logger.warning("Failed to fetch thumbnail from %s", url)
    return None


async def send_video_result(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    file_path: Path,
    title: str,
    resolution: str,
    original_url: str,
    thumbnail_url: Optional[str],
) -> None:
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO)

    caption = (
        f"📺 {title}\n"
        f"🎥 Resolution: {resolution}\n"
        f"📦 Size: {human_readable_size(file_path.stat().st_size)}\n"
        f"🔗 {original_url}"
    )

    with open(file_path, "rb") as media_file:
        if file_path.suffix.lower() in AUDIO_SUFFIXES:
            await context.bot.send_audio(
                chat_id=chat_id,
                audio=media_file,
                filename=file_path.name,
                caption=caption,
                title=title,
            )
            return

        thumbnail_bytes = await _fetch_thumbnail(thumbnail_url)
        await context.bot.send_video(
            chat_id=chat_id,
            video=media_file,
            filename=file_path.name,
            caption=caption,
            thumbnail=thumbnail_bytes,
            supports_streaming=True,
        )
