"""/torrent and inline (@BotName query) search over a fixed, legitimate
source (official Ubuntu ISO torrents), with real BitTorrent downloading
(via aria2c) and upload through the same pipeline the rest of the bot uses.

Also accepts a .torrent file sent directly as a document — you already know
exactly what it is, so there's no search/discovery step involved at all.

Deliberately not a general search — see search_torrents() below.
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import re
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
from ..torrentdl import TorrentDownloadError, download_torrent
from ..uploader import UploadManager
from ..utils import error_message, format_progress_panel, free_disk_mb, notify_owner

logger = logging.getLogger(__name__)

FETCH_TIMEOUT_SECONDS = 20
MAX_PENDING_PER_USER = 20
MAX_PENDING_INLINE_RESULTS = 200
INLINE_RESULT_TTL_SECONDS = 15 * 60
MAX_TORRENT_FILE_BYTES = 2 * 1024 * 1024  # a real .torrent descriptor is a few KB


def _format_size(size_bytes: int | float | None) -> str:
    """دالة مساعدة لتحويل الحجم من Bytes إلى حجم مقروء (MB, GB, إلخ)."""
    if not size_bytes:
        return "N/A"
    try:
        size = float(size_bytes)
    except (ValueError, TypeError):
        return str(size_bytes)

    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(size) < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} PB"


# Fixed, legitimate source: Canonical's own release server, nothing else.
#
# Filenames are discovered from the directory listing rather than hardcoded.
# Hardcoding them rots: Ubuntu publishes point releases (24.04.4) and drops
# the plain "24.04" file, so a pinned URL starts 404-ing the moment a point
# release lands — which is exactly what happened here.
#
# Deliberately NOT a general query against a public torrent index (e.g.
# YTS/1337x/TPB) — those are dominated by pirated commercial media, and a
# "search any title, download whatever matches" tool is what this project
# has explicitly declined to build. To offer more variety, add another
# *individually-named* legitimate source below — not a search backend.
UBUNTU_BASE = "https://releases.ubuntu.com"
UBUNTU_RELEASE_DIRS = ("26.04", "24.04")  # current + previous LTS
LISTING_CACHE_TTL_SECONDS = 600

_ENTRY_RE = re.compile(
    r'href="(ubuntu-(\d+\.\d+(?:\.\d+)?)-(desktop|live-server)-amd64\.iso\.torrent)"'
)
_FLAVOUR_LABELS = {"desktop": "Desktop", "live-server": "Server"}

# (results, fetched_at) — inline mode fires a query per keystroke, so without
# this every character typed would hit Canonical's server again.
_listing_cache: tuple[list[dict], float] | None = None


def _parse_release_listing(html: str, release_dir: str) -> list[dict]:
    """Newest point release per flavour from one directory listing.

    Pure/offline so it can be unit-tested without touching the network.
    """
    newest: dict[str, tuple[tuple[int, ...], str, str]] = {}
    for filename, version, flavour in _ENTRY_RE.findall(html):
        key = tuple(int(p) for p in version.split("."))
        # Compare numerically: a plain string sort puts "24.04.10" before
        # "24.04.9".
        if flavour not in newest or key > newest[flavour][0]:
            newest[flavour] = (key, version, filename)

    return [
        {
            "name": f"Ubuntu {version} {_FLAVOUR_LABELS[flavour]} (amd64)",
            "size": "؟",
            "seeders": "official",
            "torrent": f"{UBUNTU_BASE}/{release_dir}/{filename}",
            "iso": f"{UBUNTU_BASE}/{release_dir}/{filename[: -len('.torrent')]}",
        }
        for flavour, (_key, version, filename) in sorted(newest.items())
    ]


def _matches(item: dict, tokens: list[str]) -> bool:
    haystack = f"{item['name']} ubuntu linux iso".lower()
    return all(token in haystack for token in tokens)


async def _fill_sizes(session: aiohttp.ClientSession, items: list[dict]) -> None:
    """Best-effort real ISO sizes via HEAD. The listing's own size column is
    the .torrent descriptor's size (a few hundred KB), not the image's."""

    async def one(item: dict) -> None:
        with contextlib.suppress(Exception):
            async with session.head(item["iso"], allow_redirects=True) as resp:
                length = resp.headers.get("Content-Length")
                if length and length.isdigit():
                    item["size"] = _format_size(int(length))

    await asyncio.gather(*(one(i) for i in items))


async def search_torrents(query: str) -> list[dict]:
    """Available official Ubuntu images, filtered by *query*."""
    global _listing_cache

    now = time.monotonic()
    if _listing_cache is not None and now - _listing_cache[1] < LISTING_CACHE_TTL_SECONDS:
        results = _listing_cache[0]
    else:
        results = []
        try:
            timeout = aiohttp.ClientTimeout(total=FETCH_TIMEOUT_SECONDS)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                for release_dir in UBUNTU_RELEASE_DIRS:
                    try:
                        async with session.get(f"{UBUNTU_BASE}/{release_dir}/") as resp:
                            if resp.status >= 400:
                                continue
                            html = await resp.text()
                    except aiohttp.ClientError as exc:
                        logger.warning("Ubuntu listing %s failed: %s", release_dir, exc)
                        continue
                    results.extend(_parse_release_listing(html, release_dir))
                if results:
                    await _fill_sizes(session, results)
        except Exception:
            logger.exception("Failed to list official Ubuntu images")
            return []

        if results:
            _listing_cache = (results, now)

    tokens = [t for t in query.lower().split() if t]
    if not tokens:
        return results
    return [item for item in results if _matches(item, tokens)]


# ---------------------------------------------------------------- /torrent


async def torrent_search_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return

    query = " ".join(context.args or [])
    if not query:
        await update.message.reply_text("اكتب كلمة البحث بعد الأمر\nمثال:\n/torrent ubuntu")
        return

    results = await search_torrents(query)
    if not results:
        await update.message.reply_text(
            "مفيش نتايج للكلمة دي.\n\n"
            "ℹ️ المصدر هنا هو نسخ Ubuntu الرسمية بس (releases.ubuntu.com) — "
            "جرب <code>ubuntu</code> أو <code>desktop</code> أو <code>server</code>.\n"
            "لأي تورينت تاني، ابعتلي ملف <code>.torrent</code> مباشرة كملف.",
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
                    f"{item['name']}\nالحجم: {item['size']} — Seeders: {item['seeders']}",
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
    if not query:
        return

    results = await search_torrents(query)

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
                description=f"الحجم: {item['size']} — Seeders: {item['seeders']}",
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
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(torrent_source) as response:
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
