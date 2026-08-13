"""Inline-button callbacks: start jobs, playlist runs, cancellation."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from ..downloader import DownloadError
from ..jobs import StatusReporter, run_download_job
from ..manager import DownloadManager
from ..utils import error_message, truncate_text
from .scan import handle_scan_callback
from .secret import handle_secret_callback
from .settings import handle_settings_callback

logger = logging.getLogger(__name__)


def _cancel_markup(job_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🚫 إلغاء", callback_data=f"cancel:{job_id}")]]
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None or update.effective_user is None:
        return

    parts = query.data.split(":")
    action = parts[0]

    if action == "setsite":
        await handle_settings_callback(update, context)
        return

    if action in ("scanrun", "scandrop"):
        await query.answer()
        await handle_scan_callback(update, context)
        return

    if action.startswith("secret"):
        await handle_secret_callback(update, context)
        return

    await query.answer()

    if action == "drop" and len(parts) == 2:
        context.user_data.get("pending", {}).pop(parts[1], None)
        if query.message is not None:
            await query.message.edit_text("👌 اتلغى الطلب.")
        return

    if action == "cancel" and len(parts) == 2:
        manager: DownloadManager = context.bot_data["manager"]
        if not manager.cancel(parts[1]):
            if query.message is not None:
                await query.message.edit_text("الطلب ده خلص أو مش موجود.")
        return

    if action in ("dl", "pl") and len(parts) == 3:
        key, format_key = parts[1], parts[2]
        pending = context.user_data.get("pending", {})
        item = pending.pop(key, None)
        if item is None:
            if query.message is not None:
                await query.message.edit_text("الطلب ده قديم — ابعت اللينك تاني.")
            return
        try:
            if action == "dl":
                await _start_single(update, context, item["url"], format_key, item["title"])
            else:
                await _start_playlist(update, context, item["url"], format_key, item["title"])
        except Exception as exc:
            logger.exception("Failed to start job")
            if query.message is not None:
                with contextlib.suppress(Exception):
                    await query.message.edit_text(f"❌ حصل خطأ: {error_message(exc)}")


def _deps(context: ContextTypes.DEFAULT_TYPE):
    return (
        context.bot_data["config"],
        context.bot_data["manager"],
        context.bot_data["downloader"],
        context.bot_data["uploader"],
    )


async def _start_single(update, context, url, format_key, title) -> None:
    query = update.callback_query
    assert query is not None and update.effective_user is not None
    if query.message is None:
        return
    config, manager, downloader, uploader = _deps(context)
    job_id = uuid.uuid4().hex[:8]
    status_msg = query.message
    await status_msg.edit_text(
        f"🕐 في الطابور: {truncate_text(title, 60)}", reply_markup=_cancel_markup(job_id)
    )
    status = StatusReporter(
        status_msg, config.edit_throttle_seconds, markup=_cancel_markup(job_id)
    )
    context.application.create_task(
        run_download_job(
            config=config,
            manager=manager,
            downloader=downloader,
            uploader=uploader,
            status=status,
            chat_id=status_msg.chat_id,
            user_id=update.effective_user.id,
            url=url,
            format_key=format_key,
            title_hint=title,
            job_id=job_id,
        )
    )


async def _start_playlist(update, context, url, format_key, title) -> None:
    query = update.callback_query
    assert query is not None and update.effective_user is not None
    if query.message is None:
        return
    config, manager, downloader, uploader = _deps(context)
    user_id = update.effective_user.id
    chat_id = query.message.chat_id
    await query.message.edit_text(f"📃 بدء تحميل البلايليست: {truncate_text(title, 60)}")

    async def run() -> None:
        try:
            info = await asyncio.to_thread(downloader.extract_info, url)
        except DownloadError as exc:
            await context.bot.send_message(chat_id, f"❌ {error_message(exc)}")
            return
        except Exception as exc:
            logger.exception("Playlist re-extract failed")
            await context.bot.send_message(chat_id, f"❌ {error_message(exc)}")
            return

        entries = info.entries[: config.max_playlist_items]
        ok_count = 0
        for i, entry in enumerate(entries, start=1):
            entry_url = entry.get("url") or entry.get("webpage_url")
            entry_title = entry.get("title") or f"فيديو {i}"
            if not entry_url:
                continue
            job_id = uuid.uuid4().hex[:8]
            msg = await context.bot.send_message(
                chat_id,
                f"🕐 ({i}/{len(entries)}) {truncate_text(entry_title, 50)}",
                reply_markup=_cancel_markup(job_id),
            )
            status = StatusReporter(
                msg, config.edit_throttle_seconds, markup=_cancel_markup(job_id)
            )
            try:
                ok = await run_download_job(
                    config=config,
                    manager=manager,
                    downloader=downloader,
                    uploader=uploader,
                    status=status,
                    chat_id=chat_id,
                    user_id=user_id,
                    url=entry_url,
                    format_key=format_key,
                    title_hint=entry_title,
                    job_id=job_id,
                )
                if ok:
                    ok_count += 1
            except asyncio.CancelledError:
                with contextlib.suppress(Exception):
                    await asyncio.shield(
                        context.bot.send_message(chat_id, "🚫 اتلغت باقي البلايليست.")
                    )
                return
            except Exception:
                logger.exception("Playlist entry %d failed", i)
        await context.bot.send_message(
            chat_id, f"📃 خلصت البلايليست — نجح {ok_count} من {len(entries)}."
        )

    context.application.create_task(run())
