"""/secret + inline (@Bot s query) — owner-only listing/search for a fixed site.

Fetches search/path pages, extracts /videos/ links with title + thumbnail,
then shows a keyboard (command) or inline results with images.

Examples:
  /secret
  /secret most-popular/week
  /secret search milf
  /secret s blonde
  Inline: @YourBot s milf
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
import uuid
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import aiohttp
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Update,
)
from telegram.ext import ContextTypes

from ..config import Config
from ..downloader import YTDLPDownloader
from ..jobs import StatusReporter, run_download_job
from ..manager import DownloadManager
from ..uploader import UploadManager
from ..utils import error_message, truncate_text

logger = logging.getLogger(__name__)

SECRET_BASE_URL = "https://www.wow.xxx/"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Referer": SECRET_BASE_URL,
}

_VIDEO_PATH_RE = re.compile(r"/videos/([^/?#]+)/?", re.I)
# Card blocks often look like: <a href="/videos/slug/"> ... <img src|data-src="thumb"> ... title
_CARD_RE = re.compile(
    r'<a[^>]+href=["\']([^"\']*?/videos/[^"\']+)["\'][^>]*>(.*?)</a>',
    re.I | re.S,
)
_IMG_RE = re.compile(
    r'<img[^>]+(?:data-src|data-original|data-thumb|src)=["\']([^"\']+)["\']',
    re.I,
)
_TITLE_ATTR_RE = re.compile(r'\btitle=["\']([^"\']+)["\']', re.I)
_ALT_ATTR_RE = re.compile(r'\balt=["\']([^"\']+)["\']', re.I)
_TAG_RE = re.compile(r"<[^>]+>")

INLINE_RESULT_TTL_SECONDS = 15 * 60
MAX_PENDING_INLINE_RESULTS = 200
INLINE_CACHE_TTL_SECONDS = 45  # short — user complained of always-same results
_search_cache: dict[str, tuple[list[dict], float]] = {}
# Rotate which page of results we start from so repeated searches feel fresher
_page_cursor: dict[str, int] = {}


class _AnchorCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.pairs: list[tuple[str, str]] = []
        self._in_a = False
        self._href: str | None = None
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = None
        for name, value in attrs:
            if name == "href" and value:
                href = value
                break
        if href:
            self._in_a = True
            self._href = href
            self._text_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_a:
            self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_a and self._href:
            text = " ".join(p.strip() for p in self._text_parts if p.strip())
            self.pairs.append((self._href, text))
            self._in_a = False
            self._href = None
            self._text_parts = []


def _is_owner(config: Config, update: Update) -> bool:
    return (
        config.telegram_owner_id is not None
        and update.effective_user is not None
        and update.effective_user.id == config.telegram_owner_id
    )


def _encode_search_slug(query: str) -> str:
    """wow.xxx uses hyphenated path segments: /search/big-ass/relevance/."""
    return "-".join(query.split())


def _slug_to_title(slug: str) -> str:
    return unescape(slug.replace("-", " ").strip()).title()


def _clean_title(raw: str) -> str:
    text = unescape(_TAG_RE.sub(" ", raw or ""))
    text = re.sub(r"\s+", " ", text).strip()
    # Drop pure noise labels
    if text.lower() in {"", "full video", "hd", "4k", "watch", "preview"}:
        return ""
    return text


def _resolve_url(args: list[str] | None) -> str:
    if not args:
        return SECRET_BASE_URL

    first = args[0].strip().lower()

    if first in ("search", "s") and len(args) >= 2:
        query = " ".join(a.strip() for a in args[1:] if a.strip())
        if not query:
            return SECRET_BASE_URL
        slug = _encode_search_slug(query)
        return urljoin(SECRET_BASE_URL, f"search/{slug}/")

    first_raw = args[0].strip()
    if first_raw.startswith("http://") or first_raw.startswith("https://"):
        return (
            first_raw
            if first_raw.endswith("/") or "/videos/" in first_raw
            else first_raw.rstrip("/") + "/"
        )

    path = "/".join(a.strip("/") for a in args if a.strip())
    return urljoin(SECRET_BASE_URL, path + "/")


def _search_page_urls(query: str, start_page: int = 1, pages: int = 3) -> list[str]:
    """Build several search page variants so results rotate."""
    slug = _encode_search_slug(query)
    urls: list[str] = []
    for p in range(start_page, start_page + pages):
        if p <= 1:
            urls.append(urljoin(SECRET_BASE_URL, f"search/{slug}/"))
            urls.append(urljoin(SECRET_BASE_URL, f"search/{slug}/relevance/"))
        else:
            urls.append(urljoin(SECRET_BASE_URL, f"search/{slug}/relevance/{p}/"))
            urls.append(urljoin(SECRET_BASE_URL, f"search/{slug}/{p}/"))
    # dedupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _candidate_urls(url: str) -> list[str]:
    parsed = urlparse(url)
    base_no_www = parsed.netloc.removeprefix("www.")
    path = parsed.path or "/"
    if not path.endswith("/") and "/videos/" not in path:
        path = path + "/"
    variants = [
        url,
        f"{parsed.scheme}://{parsed.netloc}{path}",
        f"{parsed.scheme}://www.{base_no_www}{path}",
        f"{parsed.scheme}://{base_no_www}{path}",
    ]
    if "/search/" in path and "/relevance" not in path:
        rel = path.rstrip("/") + "/relevance/"
        variants.extend(
            [
                f"{parsed.scheme}://{parsed.netloc}{rel}",
                f"{parsed.scheme}://www.{base_no_www}{rel}",
                f"{parsed.scheme}://{base_no_www}{rel}",
            ]
        )
    seen: set[str] = set()
    out: list[str] = []
    for v in variants:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


async def _fetch_html(url: str, timeout_seconds: float) -> tuple[str, str]:
    last_err = "مفيش رد من الموقع"
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    async with aiohttp.ClientSession(timeout=timeout, headers=_HEADERS) as session:
        for candidate in _candidate_urls(url):
            try:
                async with session.get(candidate, allow_redirects=True) as resp:
                    if resp.status >= 400:
                        last_err = f"الصفحة رجّعت خطأ {resp.status} ({candidate})"
                        logger.warning("secret fetch %s -> %s", candidate, resp.status)
                        continue
                    text = await resp.text(errors="ignore")
                    if not text or len(text) < 200:
                        last_err = f"الصفحة فاضية أو محجوبة ({candidate})"
                        continue
                    return str(resp.url), text
            except aiohttp.ClientError as exc:
                last_err = f"مقدرتش أفتح الصفحة: {exc}"
                logger.warning("secret fetch failed %s: %s", candidate, exc)
    raise RuntimeError(last_err)


def _extract_video_entries(page_url: str, html: str) -> list[dict]:
    """Extract videos with best-effort title + thumbnail."""
    base_domain = urlparse(page_url).netloc.removeprefix("www.")
    seen: set[str] = set()
    entries: list[dict] = []

    # 1) Card-style extraction (href + inner img/title)
    for match in _CARD_RE.finditer(html):
        href = match.group(1)
        inner = match.group(2)
        absolute = urljoin(page_url, href).split("#", 1)[0]
        parsed = urlparse(absolute)
        host = parsed.netloc.removeprefix("www.")
        if parsed.scheme not in ("http", "https"):
            continue
        if host and host != base_domain:
            continue
        m = _VIDEO_PATH_RE.search(parsed.path)
        if not m:
            continue
        slug = m.group(1)
        norm = absolute if absolute.endswith("/") else absolute + "/"
        if norm in seen:
            continue

        title = ""
        for attr_re in (_TITLE_ATTR_RE, _ALT_ATTR_RE):
            am = attr_re.search(inner) or attr_re.search(match.group(0))
            if am:
                title = _clean_title(am.group(1))
                if title:
                    break
        if not title:
            title = _clean_title(inner)
        if not title:
            title = _slug_to_title(slug)

        thumb = None
        im = _IMG_RE.search(inner) or _IMG_RE.search(match.group(0))
        if im:
            thumb = urljoin(page_url, im.group(1).strip())
            if thumb.startswith("//"):
                thumb = "https:" + thumb

        seen.add(norm)
        entries.append({"url": norm, "title": title, "thumb": thumb})

    # 2) Fallback: simple anchors (old path)
    if len(entries) < 3:
        parser = _AnchorCollector()
        parser.feed(html)
        for href, text in parser.pairs:
            absolute = urljoin(page_url, href).split("#", 1)[0]
            parsed = urlparse(absolute)
            host = parsed.netloc.removeprefix("www.")
            if parsed.scheme not in ("http", "https"):
                continue
            if host != base_domain:
                continue
            m = _VIDEO_PATH_RE.search(parsed.path)
            if not m:
                continue
            norm = absolute if absolute.endswith("/") else absolute + "/"
            if norm in seen:
                continue
            seen.add(norm)
            title = _clean_title(text) or _slug_to_title(m.group(1))
            entries.append({"url": norm, "title": title, "thumb": None})

    return entries


async def search_secret_videos(
    query: str,
    *,
    config: Config,
    downloader: YTDLPDownloader | None = None,
) -> list[dict]:
    """Search wow.xxx across a few pages; return {url, title, thumb?}."""
    q = query.strip()
    if not q:
        return []

    cache_key = q.lower()
    now = time.monotonic()
    cached = _search_cache.get(cache_key)
    if cached is not None and now - cached[1] < INLINE_CACHE_TTL_SECONDS:
        # Still rotate order a bit so it doesn't feel identical
        items = list(cached[0])
        random.shuffle(items)
        return items[: config.max_scan_items]

    start_page = _page_cursor.get(cache_key, 1)
    page_urls = _search_page_urls(q, start_page=start_page, pages=2)
    _page_cursor[cache_key] = 1 if start_page >= 4 else start_page + 1

    all_entries: list[dict] = []
    seen_urls: set[str] = set()
    for page_url in page_urls:
        try:
            final_url, html = await _fetch_html(page_url, config.extract_timeout_seconds)
        except Exception:
            logger.warning("secret search page failed: %s", page_url)
            continue
        for item in _extract_video_entries(final_url, html):
            if item["url"] in seen_urls:
                continue
            seen_urls.add(item["url"])
            all_entries.append(item)
        if len(all_entries) >= config.max_scan_items * 3:
            break

    if not all_entries:
        return []

    # Prefer entries that have real titles (not just slug) and thumbs
    all_entries.sort(
        key=lambda e: (
            0 if e.get("thumb") else 1,
            0 if len(e.get("title") or "") > 12 else 1,
        )
    )

    found: list[dict] = []
    checked = 0
    for item in all_entries:
        if checked >= config.max_scan_probe_links or len(found) >= config.max_scan_items:
            break
        checked += 1
        if downloader is None:
            found.append(item)
            continue
        try:
            info = await asyncio.wait_for(
                asyncio.to_thread(downloader.extract_info, item["url"]),
                timeout=config.scan_probe_timeout_seconds,
            )
            if info.is_playlist:
                continue
            found.append(
                {
                    "url": item["url"],
                    "title": info.title or item["title"],
                    "thumb": item.get("thumb"),
                }
            )
        except Exception:
            if len(found) < config.max_scan_items:
                found.append(item)

    if found:
        _search_cache[cache_key] = (list(found), now)
        if len(_search_cache) > 50:
            oldest = min(_search_cache.items(), key=lambda kv: kv[1][1])
            _search_cache.pop(oldest[0], None)

    return found


async def secret_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return

    config: Config = context.bot_data["config"]
    user_id = update.effective_user.id

    if config.telegram_owner_id is None:
        await update.message.reply_text(
            "⚠️ الأمر /secret محتاج <code>TELEGRAM_OWNER_ID</code> في إعدادات السيرفر.\n\n"
            f"آيدي حسابك دلوقتي: <code>{user_id}</code>\n"
            "حطه في ملف <code>.env</code>:\n"
            f"<code>TELEGRAM_OWNER_ID={user_id}</code>",
            parse_mode="HTML",
        )
        return

    if user_id != config.telegram_owner_id:
        await update.message.reply_text(
            "⛔ الأمر ده لصاحب البوت بس.\n"
            f"آيدي حسابك: <code>{user_id}</code>\n"
            f"المسجّل: <code>{config.telegram_owner_id}</code>",
            parse_mode="HTML",
        )
        return

    args = list(context.args or [])
    is_search = bool(args) and args[0].strip().lower() in ("search", "s")
    downloader: YTDLPDownloader = context.bot_data["downloader"]

    if is_search and len(args) >= 2:
        query = " ".join(a.strip() for a in args[1:] if a.strip())
        status = await update.message.reply_text(
            f"🔐 ببحث عن:\n<code>{query}</code>\nبدوّر على فيديوهات...",
            parse_mode="HTML",
        )
        found = await search_secret_videos(query, config=config, downloader=downloader)
        if not found:
            await status.edit_text(
                "❌ مفيش نتايج للكلمة دي.\n"
                "جرّب كلمات تانية أو:\n"
                "<code>/secret most-popular/week</code>",
                parse_mode="HTML",
            )
            return
    else:
        url = _resolve_url(args)
        status = await update.message.reply_text(
            f"🔐 بفحص الصفحة:\n<code>{url}</code>\nبدوّر على فيديوهات...",
            parse_mode="HTML",
        )
        try:
            final_url, html = await _fetch_html(url, config.extract_timeout_seconds)
        except Exception as exc:
            logger.exception("Secret page fetch failed for %s", url)
            await status.edit_text(
                f"❌ {error_message(exc)}\n\n"
                "لو السيرفر في الصين/Tencent، Cloudflare أحيانًا بيحجب الطلب.\n"
                "جرّب من السيرفر:\n"
                "<code>curl -I https://www.wow.xxx/</code>",
                parse_mode="HTML",
            )
            return

        entries = _extract_video_entries(final_url, html)
        if not entries:
            await status.edit_text(
                "❌ مالقتش لينكات /videos/ في الصفحة.\n"
                "جرّب مثلًا:\n"
                "<code>/secret</code>\n"
                "<code>/secret most-popular/week</code>\n"
                "<code>/secret search milf</code>",
                parse_mode="HTML",
            )
            return

        found = []
        checked = 0
        for item in entries:
            if checked >= config.max_scan_probe_links or len(found) >= config.max_scan_items:
                break
            checked += 1
            try:
                info = await asyncio.wait_for(
                    asyncio.to_thread(downloader.extract_info, item["url"]),
                    timeout=config.scan_probe_timeout_seconds,
                )
                if info.is_playlist:
                    continue
                found.append(
                    {
                        "url": item["url"],
                        "title": info.title or item["title"],
                        "thumb": item.get("thumb"),
                    }
                )
            except Exception:
                if len(found) < config.max_scan_items:
                    found.append(item)

    if not found:
        await status.edit_text("❌ مالقتش فيديوهات قابلة للعرض.")
        return

    key = uuid.uuid4().hex[:8]
    pending: dict = context.user_data.setdefault("pending_secret", {})
    pending[key] = found

    rows: list[list[InlineKeyboardButton]] = []
    for i, item in enumerate(found):
        label = f"⬇️ {i + 1}. {truncate_text(item['title'], 40)}"
        rows.append([InlineKeyboardButton(label, callback_data=f"secretdl:{key}:{i}")])
    rows.append(
        [InlineKeyboardButton(f"⬇️ حمّل الكل ({len(found)})", callback_data=f"secretrun:{key}")]
    )
    rows.append([InlineKeyboardButton("❌ إلغاء", callback_data=f"secretdrop:{key}")])

    lines = "\n".join(
        f"{i}. {truncate_text(item['title'], 55)}" for i, item in enumerate(found, start=1)
    )
    await status.edit_text(
        f"🔐 لقيت <b>{len(found)}</b> فيديو:\n\n{lines}\n\nاختار واحد أو حمّل الكل:",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode="HTML",
    )

    # Send preview photos (title + thumbnail) when available
    previews = [it for it in found[:8] if it.get("thumb")]
    for i, item in enumerate(previews, start=1):
        try:
            await context.bot.send_photo(
                chat_id=update.message.chat_id,
                photo=item["thumb"],
                caption=f"{i}. {truncate_text(item['title'], 200)}",
            )
        except Exception:
            logger.debug("secret preview photo failed for %s", item.get("url"))


def parse_secret_inline_query(raw: str) -> str | None:
    text = (raw or "").strip()
    if not text:
        return None
    lower = text.lower()
    for prefix in ("search:", "s:", "search ", "s "):
        if lower.startswith(prefix):
            q = text[len(prefix) :].strip()
            return q or None
    return None


async def secret_inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    inline_query = update.inline_query
    if inline_query is None:
        return False

    search_q = parse_secret_inline_query(inline_query.query)
    if search_q is None:
        return False

    config: Config = context.bot_data["config"]
    user = update.effective_user
    if (
        config.telegram_owner_id is None
        or user is None
        or user.id != config.telegram_owner_id
    ):
        await inline_query.answer(
            [],
            cache_time=5,
            is_personal=True,
            switch_pm_text="البحث ده لصاحب البوت بس",
            switch_pm_parameter="secret",
        )
        return True

    results = await search_secret_videos(search_q, config=config, downloader=None)

    pending: dict[str, tuple[dict, float]] = context.bot_data.setdefault(
        "inline_secret_results", {}
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
        title = truncate_text(item["title"], 64) or "فيديو"
        kwargs: dict = {
            "id": key,
            "title": title,
            "description": truncate_text(item["title"], 100),
            "input_message_content": InputTextMessageContent(f"🔐 {title}"),
            "reply_markup": InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬇️ تحميل", callback_data=f"secretdl_i:{key}")]]
            ),
        }
        thumb = item.get("thumb")
        if thumb and thumb.startswith("http"):
            kwargs["thumbnail_url"] = thumb
        answers.append(InlineQueryResultArticle(**kwargs))

    await inline_query.answer(answers, cache_time=5, is_personal=True)
    return True


async def handle_secret_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    query = update.callback_query
    if query is None or query.data is None:
        return False

    parts = query.data.split(":")
    action = parts[0]
    if action not in ("secretdl", "secretrun", "secretdrop", "secretdl_i"):
        return False

    config: Config = context.bot_data["config"]
    if not _is_owner(config, update):
        await query.answer("مش مسموح.", show_alert=True)
        return True

    if len(parts) < 2:
        return True

    key = parts[1]
    bot = context.bot
    inline_id = query.inline_message_id
    message = query.message

    if action == "secretdl_i":
        await query.answer()
        inline_pending: dict = context.bot_data.get("inline_secret_results", {})
        entry = inline_pending.pop(key, None)
        item = None
        if entry is not None:
            item, created_at = entry
            if time.monotonic() - created_at > INLINE_RESULT_TTL_SECONDS:
                item = None
        if item is None:
            text = "انتهت صلاحية النتيجة — دوّر تاني."
            if message:
                await message.edit_text(text)
            elif inline_id:
                await bot.edit_message_text(text, inline_message_id=inline_id)
            return True

        manager: DownloadManager = context.bot_data["manager"]
        downloader: YTDLPDownloader = context.bot_data["downloader"]
        uploader: UploadManager = context.bot_data["uploader"]
        user_id = update.effective_user.id if update.effective_user else 0
        chat_id = message.chat_id if message else user_id
        job_id = uuid.uuid4().hex[:8]

        status = StatusReporter(
            message,
            config.edit_throttle_seconds,
            inline_message_id=inline_id,
            bot=bot,
        )
        await status.update(f"🕐 {truncate_text(item['title'], 60)}", force=True)

        async def run_one() -> None:
            try:
                await run_download_job(
                    config=config,
                    manager=manager,
                    downloader=downloader,
                    uploader=uploader,
                    status=status,
                    chat_id=chat_id,
                    user_id=user_id,
                    url=item["url"],
                    format_key="best",
                    title_hint=item["title"],
                    job_id=job_id,
                )
            except Exception:
                logger.exception("Secret inline download failed")

        context.application.create_task(run_one())
        return True

    pending: dict = context.user_data.get("pending_secret", {})

    if message is None:
        return True

    if action == "secretdrop":
        pending.pop(key, None)
        await message.edit_text("👌 اتلغى.")
        return True

    items = pending.get(key)
    if items is None:
        await message.edit_text("القائمة دي قديمة أو خلصت — ابعت /secret تاني.")
        return True

    manager = context.bot_data["manager"]
    downloader = context.bot_data["downloader"]
    uploader = context.bot_data["uploader"]
    chat_id = message.chat_id
    user_id = update.effective_user.id if update.effective_user else 0

    if action == "secretdl":
        if len(parts) < 3:
            return True
        try:
            idx = int(parts[2])
        except ValueError:
            return True
        if idx < 0 or idx >= len(items):
            await query.answer("فيديو مش موجود.", show_alert=True)
            return True
        item = items[idx]
        await query.answer(f"بدء: {truncate_text(item['title'], 40)}")
        job_id = uuid.uuid4().hex[:8]
        msg = await context.bot.send_message(
            chat_id, f"🕐 {truncate_text(item['title'], 60)}"
        )
        job_status = StatusReporter(msg, config.edit_throttle_seconds)

        async def run_cmd_one() -> None:
            try:
                await run_download_job(
                    config=config,
                    manager=manager,
                    downloader=downloader,
                    uploader=uploader,
                    status=job_status,
                    chat_id=chat_id,
                    user_id=user_id,
                    url=item["url"],
                    format_key="best",
                    title_hint=item["title"],
                    job_id=job_id,
                )
            except Exception:
                logger.exception("Secret single download failed")

        context.application.create_task(run_cmd_one())
        return True

    batch = pending.pop(key, items)
    await message.edit_text(f"📃 بدء تحميل {len(batch)} فيديو...")

    async def run_all() -> None:
        ok_count = 0
        for i, item in enumerate(batch, start=1):
            job_id = uuid.uuid4().hex[:8]
            msg = await context.bot.send_message(
                chat_id, f"🕐 ({i}/{len(batch)}) {truncate_text(item['title'], 50)}"
            )
            job_status = StatusReporter(msg, config.edit_throttle_seconds)
            try:
                ok = await run_download_job(
                    config=config,
                    manager=manager,
                    downloader=downloader,
                    uploader=uploader,
                    status=job_status,
                    chat_id=chat_id,
                    user_id=user_id,
                    url=item["url"],
                    format_key="best",
                    title_hint=item["title"],
                    job_id=job_id,
                )
                if ok:
                    ok_count += 1
            except asyncio.CancelledError:
                await context.bot.send_message(chat_id, "🚫 اتلغى باقي التحميلات.")
                return
            except Exception:
                logger.exception("Secret batch entry %d failed")
        await context.bot.send_message(chat_id, f"📃 خلص — نجح {ok_count} من {len(batch)}.")

    context.application.create_task(run_all())
    return True
