"""Ingestion: receive message → validate fast → extract info → offer choices."""

from __future__ import annotations

import asyncio
import logging
import uuid

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from ..config import Config
from ..downloader import (
    AUDIO_FORMAT_KEYS,
    FORMAT_LABELS,
    DownloadError,
    YTDLPDownloader,
    available_qualities,
)
from ..utils import error_message, format_duration, truncate_text
from ..validators import URLValidator
from .cookies import handle_cookies_upload

logger = logging.getLogger(__name__)

MAX_PENDING_PER_USER = 20


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await handle_cookies_upload(update, context):
        return

    message = update.message
    if message is None or not message.text or update.effective_user is None:
        return

    # Magnet links go to the full torrent engine.
    from .torrent import handle_magnet_message
    if await handle_magnet_message(update, context):
        return

    url = URLValidator.extract_url(message.text)
    if url is None:
        await message.reply_text(
            "ابعتلي لينك فيديو يبدأ بـ http أو https 🙂\n"
            "أو رابط magnet:? أو ملف .torrent"
        )
        return

    ok, err = URLValidator.validate(url)
    if not ok:
        await message.reply_text(f"⛔ {err}")
        return

    status = await message.reply_text("🔍 استلمت طلبك، جاري فحص اللينك...")

    config: Config = context.bot_data["config"]
    downloader: YTDLPDownloader = context.bot_data["downloader"]

    try:
        info = await asyncio.wait_for(
            asyncio.to_thread(downloader.extract_info, url),
            timeout=config.extract_timeout_seconds,
        )
    except asyncio.TimeoutError:
        await status.edit_text("⛔ فحص اللينك خد وقت طويل — جرب تاني بعد شوية.")
        return
    except DownloadError as exc:
        await status.edit_text(f"❌ {error_message(exc)}")
        return
    except Exception as exc:
        logger.exception("extract_info failed for %s", url)
        await status.edit_text(f"❌ حصل خطأ أثناء فحص اللينك: {error_message(exc)}")
        return

    pending: dict = context.user_data.setdefault("pending", {})
    if len(pending) >= MAX_PENDING_PER_USER:
        pending.clear()
    key = uuid.uuid4().hex[:8]
    pending[key] = {"url": url, "title": info.title, "is_playlist": info.is_playlist}

    if info.is_playlist:
        count = len(info.entries)
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        f"⬇️ حمّل {count} فيديو (أفضل جودة)",
                        callback_data=f"pl:{key}:best",
                    )
                ],
                [InlineKeyboardButton("🎵 حمّلهم MP3", callback_data=f"pl:{key}:mp3")],
                [InlineKeyboardButton("❌ إلغاء", callback_data=f"drop:{key}")],
            ]
        )
        caption = (
            f"📃 بلايليست: {truncate_text(info.title, 60)}\n"
            f"عدد الفيديوهات: {count} (حد أقصى {config.max_playlist_items})\n"
            "كل فيديو لوحده — لو واحد فشل الباقي بيكمل."
        )
        await _send_preview(message, status, info.thumbnail, caption, keyboard)
        return

    duration = format_duration(info.duration)
    qualities = available_qualities(info.formats)

    rows = [[InlineKeyboardButton(FORMAT_LABELS["best"], callback_data=f"dl:{key}:best")]]
    resolution_tiers = [q for q in qualities if q != "best" and q not in AUDIO_FORMAT_KEYS]
    for i in range(0, len(resolution_tiers), 2):
        pair = resolution_tiers[i : i + 2]
        rows.append(
            [InlineKeyboardButton(FORMAT_LABELS[q], callback_data=f"dl:{key}:{q}") for q in pair]
        )
    audio_tiers = [q for q in qualities if q in AUDIO_FORMAT_KEYS]
    for i in range(0, len(audio_tiers), 2):
        pair = audio_tiers[i : i + 2]
        rows.append(
            [InlineKeyboardButton(FORMAT_LABELS[q], callback_data=f"dl:{key}:{q}") for q in pair]
        )
    rows.append([InlineKeyboardButton("❌ إلغاء", callback_data=f"drop:{key}")])

    uploader_line = f"👤 {truncate_text(info.uploader, 60)}\n" if info.uploader else ""
    caption = (
        f"🎬 {truncate_text(info.title, 80)}\n"
        f"{uploader_line}"
        f"⏱ المدة: {duration}\n\n"
        "اختار الصيغة:"
    )
    await _send_preview(message, status, info.thumbnail, caption, InlineKeyboardMarkup(rows))


async def _send_preview(message, status, thumbnail_url, caption, keyboard) -> None:
    if thumbnail_url:
        try:
            await message.reply_photo(photo=thumbnail_url)
        except Exception:
            logger.warning("Failed to send thumbnail preview")
    await status.edit_text(caption, reply_markup=keyboard)
