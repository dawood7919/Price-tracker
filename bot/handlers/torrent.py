"""Full torrent engine: search, magnet links, .torrent files, and download flow.

Search results come from the allowlisted official providers in
``bot.torrent_sources``. The download pipeline accepts:
  - results from /torrent + inline search
  - a user-supplied ``.torrent`` file
  - a ``magnet:`` URI pasted as a message
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
from ..torrentdl import (
    TorrentDownloadError,
    download_torrent,
    extract_magnet,
    is_magnet_uri,
    parse_magnet_name,
    parse_torrent_meta,
)
from ..uploader import UploadManager
from ..utils import error_message, format_progress_panel, free_disk_mb, notify_owner

logger = logging.getLogger(__name__)

FETCH_TIMEOUT_SECONDS = 20
MAX_PENDING_PER_USER = 20
MAX_PENDING_INLINE_RESULTS = 200
INLINE_RESULT_TTL_SECONDS = 15 * 60
MAX_TORRENT_FILE_BYTES = 2 * 1024 * 1024  # real .torrent descriptors are a few KB


# ---------------------------------------------------------------- /torrent


async def torrent_search_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return

    query = " ".join(context.args or [])
    if not query:
        await update.message.reply_text(
            "🧲 <b>محرك التورنت</b>\n"
            "――――――――――――――――\n"
            "• <code>/torrent كلمة</code> — بحث في المصادر المفعّلة\n"
            "• ابعت ملف <code>.torrent</code> مباشرة\n"
            "• الصق رابط <code>magnet:?</code>\n"
            "• Inline: <code>@bot t كلمة</code>\n\n"
            "الرسمي: Ubuntu / Debian / Fedora\n"
            "العام (من /settings): PirateBay / YTS / EZTV / Solid / CSV / Nyaa / 1337x",
            parse_mode="HTML",
        )
        return

    # If the argument is itself a magnet, download directly.
    if is_magnet_uri(query):
        await _start_magnet_download(update.message, context, query)
        return

    config: Config = context.bot_data["config"]
    results = await search_torrents(query, config)
    if not results:
        await update.message.reply_text(
            "مفيش نتايج للكلمة دي.\n\n"
            "ℹ️ فعّل مصادر إضافية من <code>/settings</code> "
            "(PirateBay / YTS / EZTV / Solid / CSV / Nyaa / 1337x) أو جرّب كلمة أوضح.\n\n"
            "تقدر كمان تبعت ملف <code>.torrent</code> أو رابط <code>magnet:?</code>.",
            parse_mode="HTML",
        )
        return

    pending: dict = context.user_data.setdefault("torrent_results", {})
    if len(pending) >= MAX_PENDING_PER_USER:
        pending.clear()

    # Cap how many photo cards we send to avoid flooding the chat.
    MAX_PHOTO_CARDS = 15
    shown = results[:MAX_PHOTO_CARDS]
    extra = len(results) - len(shown)

    await update.message.reply_text(
        f"🧲 <b>نتائج بحث التورنت</b> ({len(results)})\n"
        "اضغط ⬇️ تحميل جنب النتيجة اللي عايزها — التحميل يبدأ فورًا.",
        parse_mode="HTML",
    )

    for item in shown:
        key = uuid.uuid4().hex[:8]
        pending[key] = item
        caption = (
            f"🎬 <b>{html.escape(item['name'][:120])}</b>\n"
            f"📡 {html.escape(item['source'])}\n"
            f"📦 {html.escape(str(item.get('size') or '?'))} · 🌱 {html.escape(str(item.get('seeders') or '?'))}"
        )
        markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton("⬇️ تحميل الآن", callback_data=f"torrentdl:{key}")]]
        )
        thumb = (item.get("thumb") or "").strip()
        try:
            if thumb.startswith("http"):
                await update.message.reply_photo(
                    photo=thumb,
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=markup,
                )
            else:
                await update.message.reply_text(
                    caption, parse_mode="HTML", reply_markup=markup
                )
        except Exception:
            # Photo URL may be blocked — fall back to text card.
            await update.message.reply_text(
                caption, parse_mode="HTML", reply_markup=markup
            )

    if extra > 0:
        # Remaining as compact buttons
        buttons = []
        for item in results[MAX_PHOTO_CARDS:]:
            key = uuid.uuid4().hex[:8]
            pending[key] = item
            label = f"{item['name'][:40]} · {item.get('size', '?')}"
            buttons.append(
                [InlineKeyboardButton(label, callback_data=f"torrentdl:{key}")]
            )
        await update.message.reply_text(
            f"و {extra} نتيجة إضافية:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )


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
        thumb = (item.get("thumb") or "").strip()
        kwargs = {}
        if thumb.startswith("http"):
            kwargs["thumbnail_url"] = thumb
        answers.append(
            InlineQueryResultArticle(
                id=key,
                title=item["name"][:64],
                description=f"{item['source']} — {item.get('size', '?')} · {item.get('seeders', '')}",
                input_message_content=InputTextMessageContent(
                    f"🧲 {item['name']}\n📦 {item.get('size', '?')} · 🌱 {item.get('seeders', '?')}"
                ),
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("⬇️ تحميل الآن", callback_data=f"torrentdl:{key}")]]
                ),
                **kwargs,
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
    total_hint: int | None = None,
) -> None:
    """Downloads whatever *torrent_input* describes (local .torrent or magnet:),
    then uploads the largest resulting file.
    """
    await manager.acquire_slot(user_id)
    try:
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

        def on_progress(done_bytes: int, speed: float | None, total: int | None) -> None:
            nonlocal last_render
            now = time.monotonic()
            if now - last_render < config.edit_throttle_seconds:
                return
            last_render = now
            total_bytes = total if total and total > 0 else None
            percent = 0.0
            eta = None
            if total_bytes and total_bytes > 0:
                percent = min(100.0, (done_bytes / total_bytes) * 100.0)
                if speed and speed > 0:
                    eta = (total_bytes - done_bytes) / speed
            panel = format_progress_panel(
                phase="download",
                percent=percent,
                speed_bytes_per_sec=speed,
                done_bytes=done_bytes,
                total_bytes=total_bytes,
                eta_seconds=eta,
                title=name,
            )
            asyncio.run_coroutine_threadsafe(status.update(panel), loop)

        try:
            result_paths = await asyncio.to_thread(
                download_torrent,
                torrent_input,
                job_dir,
                on_progress,
                cancel_event,
                total_hint,
            )
        except TorrentDownloadError as exc:
            if cancel_event is not None and cancel_event.is_set():
                manager.set_state(job_id, JobState.CANCELLED)
                await status.update("🚫 <b>اتلغى الطلب.</b>", force=True, final=True)
                return
            manager.set_state(job_id, JobState.FAILED)
            reason = str(exc)
            await status.update(f"❌ {html.escape(reason)}", force=True, final=True)
            await _notify_torrent_failure(bot, config, name=name, user_id=user_id, reason=reason)
            return

        if not isinstance(result_paths, list):
            result_paths = [result_paths]

        manager.set_state(job_id, JobState.UPLOADING)
        total_files = len(result_paths)
        await status.update(
            f"📤 <b>جاري الرفع</b> — {total_files} ملف"
            + (" (هيتقسم لو أكبر من حد الرفع)" if total_files == 1 else " واحد واحد"),
            force=True,
        )

        async def notify(text: str) -> None:
            await status.update(text, force=True)

        uploaded = 0
        try:
            for idx, result_path in enumerate(result_paths, start=1):
                if cancel_event is not None and cancel_event.is_set():
                    manager.set_state(job_id, JobState.CANCELLED)
                    await status.update("🚫 <b>اتلغى الطلب.</b>", force=True, final=True)
                    return
                file_caption = name
                if total_files > 1:
                    file_caption = f"{name}\n📁 ملف {idx}/{total_files}: {result_path.name}"
                    await status.update(
                        f"📤 رفع الملف {idx}/{total_files}\n<i>{html.escape(result_path.name)}</i>",
                        force=True,
                    )
                await uploader.upload(
                    chat_id,
                    result_path,
                    file_caption,
                    notify=notify,
                    cancel_event=cancel_event,
                )
                uploaded += 1
        except Exception as exc:
            manager.set_state(job_id, JobState.FAILED)
            logger.exception("Torrent upload failed for %s", name)
            reason = f"فشل الرفع بعد {uploaded}/{total_files}: {error_message(exc)}"
            await status.update(f"❌ {html.escape(reason)}", force=True, final=True)
            await _notify_torrent_failure(bot, config, name=name, user_id=user_id, reason=reason)
            return

        manager.set_state(job_id, JobState.COMPLETED)
        if total_files > 1:
            await status.update(
                f"✅ <b>تم!</b> اتبعت {uploaded} ملف 👆",
                force=True,
                final=True,
            )
        else:
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
    """Handles torrentdl:<key> from either /torrent results or an inline pick."""
    query = update.callback_query
    if query is None or query.data is None:
        return

    bot = context.bot
    inline_id = query.inline_message_id
    message = query.message
    chat_id = message.chat_id if message else (update.effective_user.id if update.effective_user else 0)

    await query.answer()

    _, _, key = query.data.partition(":")
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
    await status.update(f"⏳ <b>بدء تحميل</b>\n<i>{html.escape(item['name'])}</i>", force=True)

    job_dir.mkdir(parents=True, exist_ok=True)
    torrent_source = item["torrent"]
    total_hint: int | None = None

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
            await status.update(f"❌ {html.escape(reason)}", force=True, final=True)
            await _notify_torrent_failure(
                bot, config, name=item["name"], user_id=user_id, reason=reason
            )
            shutil.rmtree(job_dir, ignore_errors=True)
            return

        torrent_input = job_dir / "meta.torrent"
        torrent_input.write_bytes(torrent_bytes)
        try:
            meta = parse_torrent_meta(torrent_bytes)
            total_hint = meta.get("total_length")  # type: ignore[assignment]
            if meta.get("name") and item.get("name") in ("", None):
                item["name"] = meta["name"]
        except Exception:
            pass

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
        total_hint=total_hint,
    )


# ------------------------------------------------------ direct .torrent file


async def torrent_document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Accepts a .torrent file sent directly as a document."""
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
    total_hint: int | None = None
    try:
        meta = parse_torrent_meta(torrent_bytes)
        if meta.get("name"):
            name = str(meta["name"])
        total_hint = meta.get("total_length")  # type: ignore[assignment]
        n_files = len(meta.get("files") or [])
        if n_files > 1:
            name = f"{name} ({n_files} ملف)"
    except Exception:
        pass

    job_id = uuid.uuid4().hex[:8]
    job_dir = config.download_dir / f"torrent_{job_id}"
    job_dir.mkdir(parents=True, exist_ok=True)
    torrent_path = job_dir / "meta.torrent"
    torrent_path.write_bytes(torrent_bytes)

    status_message: Message = await message.reply_text(
        f"⏳ <b>بدء تحميل</b>\n<i>{html.escape(name)}</i>",
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
        total_hint=total_hint,
    )


# ------------------------------------------------------ magnet: from message


async def _start_magnet_download(
    message: Message,
    context: ContextTypes.DEFAULT_TYPE,
    magnet: str,
) -> None:
    config: Config = context.bot_data["config"]
    manager: DownloadManager = context.bot_data["manager"]
    uploader: UploadManager = context.bot_data["uploader"]
    bot = context.bot
    user_id = message.from_user.id if message.from_user else 0

    name = parse_magnet_name(magnet)
    job_id = uuid.uuid4().hex[:8]
    job_dir = config.download_dir / f"torrent_{job_id}"
    job_dir.mkdir(parents=True, exist_ok=True)

    status_message = await message.reply_text(
        f"⏳ <b>بدء تحميل Magnet</b>\n<i>{html.escape(name)}</i>\n"
        "⏳ جاري البحث عن peers عبر DHT/Trackers...",
        parse_mode="HTML",
        reply_markup=cancel_markup(job_id),
    )
    status = StatusReporter(
        status_message, config.edit_throttle_seconds, markup=cancel_markup(job_id)
    )

    await _run_torrent_download(
        torrent_input=magnet.strip(),
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
        total_hint=None,
    )


async def handle_magnet_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """If the message contains a magnet URI, start a download and return True."""
    message = update.message
    if message is None or not message.text:
        return False
    magnet = extract_magnet(message.text)
    if not magnet:
        return False
    await _start_magnet_download(message, context, magnet)
    return True
