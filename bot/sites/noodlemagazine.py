"""NoodleMagazine search.

Search:  https://noodlemagazine.com/video/QUERY
Videos:  /watch/ID  (yt-dlp has a native NoodleMagazine extractor)
"""

from __future__ import annotations

import logging
import re
from html import unescape
from urllib.parse import quote, unquote, urljoin

import aiohttp

logger = logging.getLogger(__name__)

HOME = "https://noodlemagazine.com"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# /watch/-123_456  or  /watch/123456
_WATCH = re.compile(
    r"(?P<path>/watch/(?P<vid>-?[\w]+))",
    re.I,
)
_HREF_WATCH = re.compile(
    r'href=["\']([^"\']*/watch/-?[\w]+)["\']',
    re.I,
)
_TITLE_NEAR = re.compile(
    r'(?:title|alt)=["\']([^"\']{3,200})["\']',
    re.I,
)
_IMG_NEAR = re.compile(
    r'(?:src|data-src|data-original)=["\']([^"\']+\.(?:jpg|jpeg|png|webp)[^"\']*)["\']',
    re.I,
)


def _headers() -> dict[str, str]:
    return {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": HOME + "/",
    }


def _clean_title(raw: str) -> str:
    text = unescape(unquote(raw or "")).strip()
    text = re.sub(r"\s+", " ", text)
    return text[:120] or "Video"


async def search(query: str, *, timeout: float = 20.0, limit: int = 40) -> list[dict]:
    q = query.strip()
    if len(q) < 2:
        return []

    # site uses /video/query with spaces as + ; pagination ?p=0,1,2...
    slug = quote(q, safe="").replace("%20", "+")
    page_urls = [
        f"{HOME}/video/{slug}",
        f"{HOME}/video/{slug}?p=1",
        f"{HOME}/video/{slug}?page=2",
    ]

    seen: set[str] = set()
    out: list[dict] = []
    timeout_cfg = aiohttp.ClientTimeout(total=timeout)

    async with aiohttp.ClientSession(timeout=timeout_cfg, headers=_headers()) as session:
        for page_url in page_urls:
            try:
                async with session.get(page_url, allow_redirects=True) as resp:
                    logger.info("noodlemagazine GET %s -> %s", page_url, resp.status)
                    if resp.status >= 400:
                        continue
                    html = await resp.text(errors="ignore")
            except Exception as exc:
                logger.warning("noodlemagazine fetch failed %s: %s", page_url, exc)
                continue

            for m in _HREF_WATCH.finditer(html):
                path = m.group(1).split("?")[0]
                if not path.startswith("/"):
                    path = "/" + path.split("/", 3)[-1] if "/watch/" in path else path
                # normalize to absolute
                if path.startswith("http"):
                    url = path.split("?")[0]
                    if "/watch/" not in url:
                        continue
                else:
                    # keep only /watch/ID
                    wm = _WATCH.search(path)
                    if not wm:
                        continue
                    url = urljoin(HOME, wm.group("path"))

                if url in seen:
                    continue
                seen.add(url)

                window = html[max(0, m.start() - 400) : m.end() + 350]
                title = "Video"
                tm = _TITLE_NEAR.search(window)
                if tm:
                    title = _clean_title(tm.group(1))
                else:
                    # fallback: last path segment as id
                    vid = url.rstrip("/").rsplit("/", 1)[-1]
                    title = f"Video {vid}"

                thumb = None
                im = _IMG_NEAR.search(window)
                if im:
                    thumb = im.group(1).strip()
                    if thumb.startswith("//"):
                        thumb = "https:" + thumb
                    elif thumb.startswith("/"):
                        thumb = urljoin(HOME, thumb)

                out.append({"url": url, "title": title, "thumb": thumb})
                if len(out) >= limit:
                    logger.info("noodlemagazine search %r -> %d results", q, len(out))
                    return out

    logger.info("noodlemagazine search %r -> %d results", q, len(out))
    return out
