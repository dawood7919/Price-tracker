"""4kporno.xxx search.

Search:  https://www.4kporno.xxx/search/QUERY/
Videos:  /videos/ID/slug/
"""

from __future__ import annotations

import logging
import re
from html import unescape
from urllib.parse import quote, unquote, urljoin

import aiohttp

logger = logging.getLogger(__name__)

HOME = "https://www.4kporno.xxx"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_VIDEO_PATH = re.compile(
    r"(?P<path>/videos/(?P<id>\d+)/(?P<slug>[\w\-]+)/?)",
    re.I,
)
_IMG_NEAR = re.compile(
    r'(?:src|data-src|data-original)=["\']([^"\']+\.(?:jpg|jpeg|png|webp)[^"\']*)["\']',
    re.I,
)


def _title_from_slug(slug: str) -> str:
    text = unquote(slug).replace("-", " ").replace("_", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return unescape(text).title() or "Video"


def _headers() -> dict[str, str]:
    return {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": HOME + "/",
    }


async def search(query: str, *, timeout: float = 20.0, limit: int = 40) -> list[dict]:
    q = query.strip()
    if len(q) < 2:
        return []

    slug_q = quote(q.replace(" ", "-"), safe="-")
    page_urls = [
        f"{HOME}/search/{slug_q}/",
        f"{HOME}/?s={quote(q)}",
        f"{HOME}/search/{slug_q}/page/2/",
    ]

    seen: set[str] = set()
    out: list[dict] = []
    timeout_cfg = aiohttp.ClientTimeout(total=timeout)

    async with aiohttp.ClientSession(timeout=timeout_cfg, headers=_headers()) as session:
        for page_url in page_urls:
            try:
                async with session.get(page_url, allow_redirects=True) as resp:
                    logger.info("4kporno GET %s -> %s", page_url, resp.status)
                    if resp.status >= 400:
                        continue
                    html = await resp.text(errors="ignore")
            except Exception as exc:
                logger.warning("4kporno fetch failed %s: %s", page_url, exc)
                continue

            for m in _VIDEO_PATH.finditer(html):
                path = m.group("path")
                if not path.endswith("/"):
                    path = path + "/"
                url = urljoin(HOME, path)
                if url in seen:
                    continue
                seen.add(url)
                slug = m.group("slug") or "video"
                thumb = None
                window = html[max(0, m.start() - 500) : m.end() + 120]
                im = _IMG_NEAR.search(window)
                if im:
                    thumb = urljoin(page_url, im.group(1).strip())
                    if thumb.startswith("//"):
                        thumb = "https:" + thumb
                out.append({"url": url, "title": _title_from_slug(slug), "thumb": thumb})
                if len(out) >= limit:
                    return out

    logger.info("4kporno search %r -> %d results", q, len(out))
    return out
