"""Perverzija search scraper — returns title, url, thumb."""

from __future__ import annotations

import logging
import re
from urllib.parse import quote_plus, urljoin

import aiohttp

logger = logging.getLogger(__name__)

BASE = "https://tube.perverzija.com"
SEARCH_URL = BASE + "/?s={query}"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_VIDEO_HREF_RE = re.compile(
    r'href=["\']([^"\']+/(?:video|watch|\d+)[^"\']*)["\']',
    re.I,
)
_IMG_RE = re.compile(
    r'<img[^>]+(?:src|data-src|data-lazy-src)=["\']([^"\']+)["\'][^>]*>',
    re.I,
)
_TITLE_RE = re.compile(r'title=["\']([^"\']+)["\']', re.I)


async def search(query: str, timeout: float = 20.0, limit: int = 40) -> list[dict]:
    """Search Perverzija and return list of {title, url, thumb}."""
    q = (query or "").strip()
    if len(q) < 2:
        return []

    url = SEARCH_URL.format(query=quote_plus(q))
    results: list[dict] = []
    try:
        timeout_cfg = aiohttp.ClientTimeout(total=min(timeout, 25))
        headers = {
            "User-Agent": _USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
        }
        async with aiohttp.ClientSession(timeout=timeout_cfg, headers=headers) as session:
            async with session.get(url) as resp:
                if resp.status >= 400:
                    logger.warning("perverzija search HTTP %s", resp.status)
                    return []
                html = await resp.text(errors="ignore")
    except Exception as exc:
        logger.warning("perverzija search failed: %s", exc)
        return []

    seen: set[str] = set()
    blocks = re.split(
        r'<(?:article|div)[^>]+class=["\'][^"\']*(?:post|video|item|thumb)[^"\']*["\']',
        html,
        flags=re.I,
    )
    for block in blocks[1:]:
        hrefs = _VIDEO_HREF_RE.findall(block)
        imgs = _IMG_RE.findall(block)
        titles = _TITLE_RE.findall(block)
        if not hrefs:
            continue
        href = hrefs[0]
        if href.startswith("/"):
            href = urljoin(BASE, href)
        if href in seen or "perverzija" not in href:
            continue
        seen.add(href)
        thumb = ""
        for img in imgs:
            if img.startswith("//"):
                img = "https:" + img
            elif img.startswith("/"):
                img = urljoin(BASE, img)
            if any(x in img.lower() for x in (".jpg", ".jpeg", ".png", ".webp")):
                thumb = img
                break
        title = titles[0] if titles else href.rstrip("/").split("/")[-1].replace("-", " ")
        results.append({"title": title[:120], "url": href, "thumb": thumb})
        if len(results) >= limit:
            break

    if not results:
        for m in _VIDEO_HREF_RE.finditer(html):
            href = m.group(1)
            if href.startswith("/"):
                href = urljoin(BASE, href)
            if href in seen or "perverzija" not in href:
                continue
            seen.add(href)
            start = max(0, m.start() - 400)
            window = html[start : m.end() + 200]
            imgs = _IMG_RE.findall(window)
            thumb = ""
            for img in imgs:
                if img.startswith("//"):
                    img = "https:" + img
                elif img.startswith("/"):
                    img = urljoin(BASE, img)
                if any(x in img.lower() for x in (".jpg", ".jpeg", ".png", ".webp")):
                    thumb = img
                    break
            title = href.rstrip("/").split("/")[-1].replace("-", " ")
            results.append({"title": title[:120], "url": href, "thumb": thumb})
            if len(results) >= limit:
                break

    return results
