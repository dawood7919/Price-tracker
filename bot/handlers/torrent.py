"""Torrent command, inline search, and download flow.

Search results are supplied by the allowlisted official providers in
``bot.torrent_sources``. The download and upload pipeline remains shared with
the rest of the bot and also accepts a user-supplied ``.torrent`` file.
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import shutil
import time
import uuid
from pathlib import Path

import aiohttp
from telegram import (
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Message,
    Update,
)
from telegram.ext import ContextTypes

from ..config import Config
from ..jobs import StatusReporter
from ..manager import DownloadManager, JobState
from ..torrent_sources import search_torrents
from ..torrentdl import TorrentDownloadError, download_torrent
from ..uploader import UploadManager
from ..utils import error_message, format_progress_panel, free_disk_mb, notify_owner

logger = logging.getLogger(__name__)

FETCH_TIMEOUT_SECONDS = 20
MAX_PENDING_PER_USER = 20
MAX_PENDING_INLINE_RESULTS = 200
INLINE_RESULT_TTL_SECONDS = 15 * 60
MAX_TORRENT_FILE_BYTES = 2 * 1024 * 1024  # a real .torrent descriptor is a few KB


# ---------------------------------------------------------------- /torrent


async def torrent_search_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return

    query = " ".join(context.args or [])
    if not query:
        await update.message.reply_text("اكتب كلمة البحث بعد الأمر\nمثال:\n/torrent ubuntu")
        return

    config: Config = context.bot_data["config"]
    results = await search_torrents(query, config)
    if not results:
        await update.message.reply_text(
            "مفيش نتايج للكلمة دي.\n\n"
            "ℹ️ المصادر القانونية المفعّلة: Ubuntu وDebian وFedora. "
            "جرّب <code>ubuntu</code> أو <code>debian</code> أو <code>fedora</code>، "
            "أو غيّر المصادر من <code>/settings</code>.",
            parse_mode="HTML",
        )
        return

    pending: dict = context.user_data.setdefault("torrent_results", {})
    if len(pending) >= MAX_PENDING_PER_USER:
        pending.clear()

    buttons = []
    for item in results:
        key = uuid.uuid4().hex[:8]
        pending[key] = item
        buttons.append(
            [
                InlineKeyboardButton(
                    f"{item['name']}\n{item['source']} — الحجم: {item['size']}",
                    callback_data=f"torrentdl:{key}",
                )
            ]
        )

    await update.message.reply_text("نتائج البحث:", reply_markup=InlineKeyboardMarkup(buttons))


# ------------------------------------------------------------- inline mode


async def torrent_inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    inline_query = update.inline_query
    if inline_query is None:
        return

    query = inline_query.query.strip()
    lower = query.lower()
    for prefix in ("torrent ", "t "):
        if lower.startswith(prefix):
            query = query[len(prefix) :].strip()
            break
    if not query:
        return

    config: Config = context.bot_data["config"]
    results = await search_torrents(query, config)

    # An inline message can be forwarded or its button can be pressed by
    # somebody other than the person who typed the query. user_data belongs
    # only to that original user, so keep a short-lived shared lookup for
    # inline callback tokens.
    pending: dict[str, tuple[dict, float]] = context.bot_data.setdefault(
        "inline_torrent_results", {}
    )
    now = time.monotonic()
    for stale_key, (_item, created_at) in list(pending.items()):
        if now - created_at > INLINE_RESULT_TTL_SECONDS:
            del pending[stale_key]
    if len(pending) + len(results) > MAX_PENDING_INLINE_RESULTS:
        pending.clear()

    answers = []
    for item in results:
        key = uuid.uuid4().hex[:8]
        pending[key] = (item, now)
        answers.append(
            InlineQueryResultArticle(
                id=key,
                title=item["name"],
                description=f"{item['source']} — الحجم: {item['size']}",
                input_message_content=InputTextMessageContent(f"🎬 {item['name']}"),
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("⬇️ تحميل", callback_data=f"torrentdl:{key}")]]
                ),
            )
        )
    await inline_query.answer(answers, cache_time=10)


# ---------------------------------------------------------- shared download


def cancel_markup(job_id: str) -> InlineKeyboardMarkup:
    """Cancel button routed to the shared ``cancel:<job_id>`` handler."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🚫 إلغاء", callback_data=f"cancel:{job_id}")]]
    )


async def _notify_torrent_failure(
    bot: Bot,
    config: Config,
    *,
    name: str,
    user_id: int,
    reason: str,
) -> None:
    """Send a short torrent-failure alert to TELEGRAM_OWNER_ID if configured."""
    text = (
        "🚨 <b>فشل تورينت</b>\n"
        "――――――――――――――――\n"
        f"📌 {html.escape(name)}\n"
        f"👤 user=<code>{user_id}</code>\n"
        f"❌ {html.escape(reason)}"
    )
    await notify_owner(bot, config, text)


async def _run_torrent_download(
    *,
    torrent_input: Path | str,
    name: str,
    chat_id: int,
    user_id: int,
    job_id: str,
    status: StatusReporter,
    config: Config,
    manager: DownloadManager,
    uploader: UploadManager,
    job_dir: Path,
    bot: Bot,
) -> None:
    """Downloads whatever *torrent_input* (a local .torrent file path or a
    magnet: URI) describes, via aria2c, then uploads the result. Shared by
    the /torrent + inline-search button and by a directly-uploaded
    .torrent file — the only difference is where torrent_input came from.
    """
    await manager.acquire_slot(user_id)
    try:
        # Register the job so it is visible to /stats and stoppable by both
        # the cancel button and /killall. Without this the job still holds a
        # global concurrency slot while reporting as "not running", so a
        # batch of stuck torrents can exhaust the semaphore and wedge every
        # future download with no way to recover short of a restart.
        task = asyncio.current_task()
        cancel_event = manager.start_job(job_id, task) if task is not None else None

        free = free_disk_mb(config.download_dir)
        if free < config.min_free_disk_mb:
            manager.set_state(job_id, JobState.FAILED)
            reason = f"مفيش مساحة كافية على السيرفر ({free}MB)"
            await status.update(
                f"⛔ <b>مفيش مساحة كافية</b> على السيرفر دلوقتي ({free}MB). جرب بعد شوية.",
                force=True,
                final=True,
            )
            await _notify_torrent_failure(bot, config, name=name, user_id=user_id, reason=reason)
            return

        manager.set_state(job_id, JobState.DOWNLOADING)
        loop = asyncio.get_running_loop()
        last_render = 0.0

        def on_progress(done_bytes: int, speed: float | None) -> None:
            nonlocal last_render
            now = time.monotonic()
            if now - last_render < config.edit_throttle_seconds:
                return
            last_render = now
            panel = format_progress_panel(
                phase="download",
                percent=0.0,  # total unknown for a raw BT download — see torrentdl.py
                speed_bytes_per_sec=speed,
                done_bytes=done_bytes,
                total_bytes=None,
                eta_seconds=None,
                title=name,
            )
            asyncio.run_coroutine_threadsafe(status.update(panel), loop)

        try:
            result_path = await asyncio.to_thread(
                download_torrent, torrent_input, job_dir, on_progress, cancel_event
            )
        except TorrentDownloadError as exc:
            if cancel_event is not None and cancel_event.is_set():
                manager.set_state(job_id, JobState.CANCELLED)
                await status.update("🚫 <b>اتلغى الطلب.</b>", force=True, final=True)
                return
            manager.set_state(job_id, JobState.FAILED)
            reason = str(exc)
            await status.update(f"❌ {exc}", force=True, final=True)
            await _notify_torrent_failure(bot, config, name=name, user_id=user_id, reason=reason)
            return

        manager.set_state(job_id, JobState.UPLOADING)
        await status.update("📤 <b>جاري الرفع</b>...", force=True)

        async def notify(text: str) -> None:
            await status.update(text, force=True)

        try:
            await uploader.upload(
                chat_id, result_path, name, notify=notify, cancel_event=cancel_event
            )
        except Exception as exc:
            manager.set_state(job_id, JobState.FAILED)
            logger.exception("Torrent upload failed for %s", name)
            reason = f"فشل الرفع: {error_message(exc)}"
            await status.update(f"❌ {reason}", force=True, final=True)
            await _notify_torrent_failure(bot, config, name=name, user_id=user_id, reason=reason)
            return

        manager.set_state(job_id, JobState.COMPLETED)
        await status.update("✅ <b>تم!</b> استلم ملفك فوق 👆", force=True, final=True)
    except asyncio.CancelledError:
        manager.set_state(job_id, JobState.CANCELLED)
        with contextlib.suppress(Exception):
            await asyncio.shield(status.update("🚫 <b>اتلغى الطلب.</b>", force=True, final=True))
        raise
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)
        await manager.release_slot(user_id)


async def torrent_download_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles torrentdl:<key> from either /torrent's results or an inline pick."""
    query = update.callback_query
    if query is None or query.data is None:
        return

    # In inline mode, query.message is None and query.inline_message_id is present.
    # We must handle both.
    bot = context.bot
    inline_id = query.inline_message_id
    message = query.message
    chat_id = message.chat_id if message else (update.effective_user.id if update.effective_user else 0)

    await query.answer()

    _, _, key = query.data.partition(":")
    # Inline result tokens are shared because the user clicking a button need
    # not be the same user who made the inline query. Keep the old per-user
    # lookup for /torrent command buttons.
    inline_pending: dict = context.bot_data.get("inline_torrent_results", {})
    inline_entry = inline_pending.pop(key, None)
    if inline_entry is not None:
        item, created_at = inline_entry
        if time.monotonic() - created_at > INLINE_RESULT_TTL_SECONDS:
            item = None
    else:
        pending: dict = context.user_data.get("torrent_results", {})
        item = pending.pop(key, None)
    if item is None:
        text = "انتهت صلاحية النتيجة دي — دوّر تاني."
        if message:
            await message.edit_text(text)
        elif inline_id:
            await bot.edit_message_text(text, inline_message_id=inline_id)
        return

    config: Config = context.bot_data["config"]
    manager: DownloadManager = context.bot_data["manager"]
    uploader: UploadManager = context.bot_data["uploader"]

    user_id = update.effective_user.id if update.effective_user else 0
    job_id = uuid.uuid4().hex[:8]
    job_dir = config.download_dir / f"torrent_{job_id}"
    status = StatusReporter(
        message,
        config.edit_throttle_seconds,
        markup=cancel_markup(job_id),
        inline_message_id=inline_id,
        bot=bot,
    )
    await status.update(f"⏳ <b>بدء تحميل</b>\n<i>{item['name']}</i>", force=True)

    job_dir.mkdir(parents=True, exist_ok=True)
    torrent_source = item["torrent"]

    if str(torrent_source).startswith("magnet:"):
        torrent_input: Path | str = torrent_source
    else:
        try:
            timeout = aiohttp.ClientTimeout(total=FETCH_TIMEOUT_SECONDS)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(torrent_source) as response,
            ):
                if response.status >= 400:
                    reason = f"فشل تحميل ملف .torrent (HTTP {response.status})"
                    await status.update(f"❌ {reason}.", force=True, final=True)
                    await _notify_torrent_failure(
                        bot, config, name=item["name"], user_id=user_id, reason=reason
                    )
                    shutil.rmtree(job_dir, ignore_errors=True)
                    return
                torrent_bytes = await response.read()
        except aiohttp.ClientError as exc:
            reason = f"فشل تحميل ملف .torrent: {exc}"
            await status.update(f"❌ {reason}", force=True, final=True)
            await _notify_torrent_failure(
                bot, config, name=item["name"], user_id=user_id, reason=reason
            )
            shutil.rmtree(job_dir, ignore_errors=True)
            return

        torrent_input = job_dir / "meta.torrent"
        torrent_input.write_bytes(torrent_bytes)

    await _run_torrent_download(
        torrent_input=torrent_input,
        name=item["name"],
        chat_id=chat_id,
        user_id=user_id,
        job_id=job_id,
        status=status,
        config=config,
        manager=manager,
        uploader=uploader,
        job_dir=job_dir,
        bot=bot,
    )


# ------------------------------------------------------ direct .torrent file


async def torrent_document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Accepts a .torrent file sent directly as a document. No search
    involved — you already have the exact file, so this just downloads and
    delivers whatever it describes, same as /torrent's own download step."""
    message = update.message
    if message is None or message.document is None:
        return

    document = message.document
    if document.file_size and document.file_size > MAX_TORRENT_FILE_BYTES:
        await message.reply_text("⛔ الملف كبير أوي عشان يكون ملف .torrent حقيقي.")
        return

    config: Config = context.bot_data["config"]
    manager: DownloadManager = context.bot_data["manager"]
    uploader: UploadManager = context.bot_data["uploader"]
    bot = context.bot
    user_id = update.effective_user.id if update.effective_user else 0

    tg_file = await document.get_file()
    torrent_bytes = bytes(await tg_file.download_as_bytearray())

    name = (document.file_name or "torrent").rsplit(".", 1)[0]
    job_id = uuid.uuid4().hex[:8]
    job_dir = config.download_dir / f"torrent_{job_id}"
    job_dir.mkdir(parents=True, exist_ok=True)
    torrent_path = job_dir / "meta.torrent"
    torrent_path.write_bytes(torrent_bytes)

    status_message: Message = await message.reply_text(
        f"⏳ <b>بدء تحميل</b>\n<i>{name}</i>",
        parse_mode="HTML",
        reply_markup=cancel_markup(job_id),
    )
    status = StatusReporter(
        status_message, config.edit_throttle_seconds, markup=cancel_markup(job_id)
    )

    await _run_torrent_download(
        torrent_input=torrent_path,
        name=name,
        chat_id=message.chat_id,
        user_id=user_id,
        job_id=job_id,
        status=status,
        config=config,
        manager=manager,
        uploader=uploader,
        job_dir=job_dir,
        bot=bot,
    )
