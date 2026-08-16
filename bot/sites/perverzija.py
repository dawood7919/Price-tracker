"""Perverzija.com (tube.perverzija.com) search scraper."""

from __future__ import annotations

import logging
import re
from html import unescape
from urllib.parse import quote_plus, urljoin

import aiohttp

logger = logging.getLogger(__name__)

HOME = "https://tube.perverzija.com/"
SEARCH_TMPL = "https://tube.perverzija.com/search/{query}/"
SEARCH_TMPL_PAGE = "https://tube.perverzija.com/search/{query}/page/{page}/"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Video posts live at /{long-slug}/ — exclude nav/studio/star paths.
_VIDEO_HREF_RE = re.compile(
    r'href=["\'](https?://tube\.perverzija\.com/([a-z0-9][a-z0-9\-]{12,})/)["\']',
    re.I,
)
_SKIP_PREFIXES = (
    "studio",
    "studios",
    "stars",
    "star",
    "tag",
    "tags",
    "category",
    "categories",
    "featured",
    "full-movie",
    "vr",
    "search",
    "page",
    "author",
    "wp-",
)
_IMG_RE = re.compile(
    r'<img[^>]+(?:src|data-src|data-lazy-src)=["\']([^"\']+)["\']',
    re.I,
)
_TITLE_NEAR_RE = re.compile(
    r'href=["\'](https?://tube\.perverzija\.com/[a-z0-9\-]{12,}/)["\'][^>]*>\s*([^<]{5,160})\s*<',
    re.I,
)


def _slug_title(slug: str) -> str:
    s = slug.replace("-", " ").strip()
    s = re.sub(r"\s+", " ", s)
    return unescape(s).title()[:160] or "Video"


def _is_video_slug(slug: str) -> bool:
    low = slug.lower().strip("/")
    if not low or len(low) < 12:
        return False
    first = low.split("-", 1)[0]
    if first in _SKIP_PREFIXES or low in _SKIP_PREFIXES:
        return False
    for p in _SKIP_PREFIXES:
        if low.startswith(p + "/"):
            return False
    return True


async def search(query: str, timeout: float = 20.0, limit: int = 40) -> list[dict]:
    q = (query or "").strip()
    if len(q) < 2:
        return []

    timeout_cfg = aiohttp.ClientTimeout(total=min(timeout, 25))
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": HOME,
    }

    pages = [
        SEARCH_TMPL.format(query=quote_plus(q).replace("+", "%20") if False else q.replace(" ", "+")),
        # WordPress also accepts /search/slug/
        f"https://tube.perverzija.com/search/{quote_plus(q)}/",
        f"https://tube.perverzija.com/?s={quote_plus(q)}",
    ]
    # Prefer pretty path used by the site
    pages = [
        f"https://tube.perverzija.com/search/{quote_plus(q)}/",
        f"https://tube.perverzija.com/search/{quote_plus(q)}/page/2/",
    ]

    seen: set[str] = set()
    results: list[dict] = []
    thumb_by_url: dict[str, str] = {}

    async with aiohttp.ClientSession(timeout=timeout_cfg, headers=headers) as session:
        for page_url in pages:
            try:
                async with session.get(page_url, allow_redirects=True) as resp:
                    if resp.status >= 400:
                        logger.warning("perverzija HTTP %s for %s", resp.status, page_url)
                        continue
                    html = await resp.text(errors="ignore")
            except Exception as exc:
                logger.warning("perverzija fetch failed %s: %s", page_url, exc)
                continue

            title_map: dict[str, str] = {}
            for m in _TITLE_NEAR_RE.finditer(html):
                u, title = m.group(1), unescape(m.group(2)).strip()
                if title and len(title) > 3:
                    title_map[u.rstrip("/") + "/"] = title[:160]

            # Window-based thumb association: image near the video href
            for m in _VIDEO_HREF_RE.finditer(html):
                url, slug = m.group(1), m.group(2)
                if not _is_video_slug(slug):
                    continue
                url = url.rstrip("/") + "/"
                if url in seen:
                    continue
                seen.add(url)
                title = title_map.get(url) or _slug_title(slug)
                item = {
                    "url": url,
                    "title": title,
                    "id": slug[:80],
                }
                start = max(0, m.start() - 800)
                end = min(len(html), m.end() + 400)
                window = html[start:end]
                for img in _IMG_RE.findall(window):
                    low = img.lower()
                    if not any(x in low for x in ("upload", "thumb", "jpg", "jpeg", "webp", "png")):
                        continue
                    if any(skip in low for skip in ("logo", "icon", "avatar", "emoji", "sprite")):
                        continue
                    if img.startswith("//"):
                        img = "https:" + img
                    elif img.startswith("/"):
                        img = urljoin(HOME, img)
                    item["thumb"] = img
                    break
                if "thumb" not in item:
                    for img in _IMG_RE.findall(html):
                        if any(part in img for part in slug.split("-")[:4] if len(part) > 4):
                            if img.startswith("//"):
                                img = "https:" + img
                            elif img.startswith("/"):
                                img = urljoin(HOME, img)
                            item["thumb"] = img
                            break
                results.append(item)
                if len(results) >= limit:
                    return results

    return results[:limit]


# ---------------------------------------------------------------------------
# Resolve page → xtremestream master m3u8 (so yt-dlp can download it)
# ---------------------------------------------------------------------------

_IFRAME_XTREME = re.compile(
    r'(?:src|data-src)=["\'](https?://[^"\']*xtremestream[^"\']*data=([a-f0-9]{16,})[^"\']*)["\']',
    re.I,
)
_XTREME_ANY = re.compile(
    r'https?://[a-z0-9.-]*xtremestream\.[a-z]+/player/(?:index\.php|xs1\.php)\?data=([a-f0-9]{16,})',
    re.I,
)


async def resolve_playable_url(page_url: str, *, timeout: float = 25.0) -> str:
    """Perverzija page → xtremestream master m3u8 playlist URL."""
    if "perverzija.com" not in page_url.lower():
        return page_url

    timeout_cfg = aiohttp.ClientTimeout(total=timeout)
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": HOME,
    }

    async with aiohttp.ClientSession(timeout=timeout_cfg, headers=headers) as session:
        try:
            async with session.get(page_url, allow_redirects=True) as resp:
                if resp.status >= 400:
                    logger.warning("perverzija resolve page HTTP %s", resp.status)
                    return page_url
                html = await resp.text(errors="ignore")
        except Exception as exc:
            logger.warning("perverzija page fetch failed: %s", exc)
            return page_url

        host = None
        data_hash = None

        m = _IFRAME_XTREME.search(html)
        if m:
            full = m.group(1)
            data_hash = m.group(2)
            # keep the same host (j2 / j1 / etc.)
            host_m = re.search(r"https?://([^/]+)", full)
            if host_m:
                host = host_m.group(1)

        if not data_hash:
            m2 = _XTREME_ANY.search(html)
            if m2:
                data_hash = m2.group(1)
                host_m = re.search(r"https?://([^/]+)", m2.group(0))
                if host_m:
                    host = host_m.group(1)

        if not data_hash:
            logger.warning(
                "perverzija: no xtremestream data found (html_len=%d)", len(html)
            )
            return page_url

        if not host:
            host = "j2.xtremestream.xyz"

        # Master playlist – yt-dlp handles multi-quality HLS fine
        m3u8 = f"https://{host}/player/xs1.php?data={data_hash}"
        logger.info("perverzija resolved %s -> %s", page_url[:80], m3u8)
        return m3u8


def resolve_playable_url_sync(page_url: str, timeout: float = 25.0) -> str:
    import asyncio

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Called from sync context while a loop is already running
            # (rare); fall back to a new loop in a thread would be safer
            # but for our download path we call from to_thread-ish places.
            return asyncio.get_event_loop().run_until_complete(
                resolve_playable_url(page_url, timeout=timeout)
            )
    except RuntimeError:
        pass
    return asyncio.run(resolve_playable_url(page_url, timeout=timeout))
