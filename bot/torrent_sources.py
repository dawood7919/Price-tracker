"""Official, legal torrent search providers used by the bot.

Each provider returns one normalized record shape so that command and inline
handlers do not need source-specific parsing logic. New approved providers can
be added by registering an async function in ``_PROVIDERS``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from collections.abc import Awaitable, Callable

import aiohttp

from .config import Config
from .settings_store import get_active_torrent_sources

logger = logging.getLogger(__name__)

FETCH_TIMEOUT_SECONDS = 20
CACHE_TTL_SECONDS = 10 * 60

UBUNTU_BASE = "https://releases.ubuntu.com"
UBUNTU_RELEASE_DIRS = ("26.04", "24.04")
DEBIAN_BASE = "https://cdimage.debian.org/debian-cd/current/amd64/bt-cd/"
FEDORA_TORRENTS_JSON = "https://torrent.fedoraproject.org/torrents.json"
FEDORA_TORRENT_BASE = "https://torrent.fedoraproject.org/torrents/"

_UBUNTU_ENTRY_RE = re.compile(
    r'href="(ubuntu-(\d+\.\d+(?:\.\d+)?)-(desktop|live-server)-amd64\.iso\.torrent)"'
)
_DEBIAN_ENTRY_RE = re.compile(r'href="([^"]+\.iso\.torrent)"', re.IGNORECASE)
_FLAVOUR_LABELS = {"desktop": "Desktop", "live-server": "Server"}

# cache key: (source ids, normalized query)
_CACHE: dict[tuple[tuple[str, ...], str], tuple[list[dict[str, str]], float]] = {}


def source_label(source_id: str) -> str:
    labels = {
        "ubuntu": "Ubuntu الرسمي",
        "debian": "Debian الرسمي",
        "fedora": "Fedora الرسمي",
    }
    return labels.get(source_id, source_id)


def _matches(item: dict[str, str], tokens: list[str]) -> bool:
    haystack = " ".join(
        (item.get("name", ""), item.get("source", ""), item.get("keywords", ""))
    ).lower()
    return all(token in haystack for token in tokens)


def _format_size(size_bytes: float | None) -> str:
    if not size_bytes:
        return "غير متاح"
    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size) < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} PB"


async def _fetch_text(session: aiohttp.ClientSession, url: str) -> str:
    async with session.get(url, allow_redirects=True) as response:
        if response.status >= 400:
            raise RuntimeError(f"HTTP {response.status}")
        return await response.text(errors="ignore")


def _parse_ubuntu_listing(listing: str, release_dir: str) -> list[dict[str, str]]:
    newest: dict[str, tuple[tuple[int, ...], str, str]] = {}
    for filename, version, flavour in _UBUNTU_ENTRY_RE.findall(listing):
        key = tuple(int(part) for part in version.split("."))
        if flavour not in newest or key > newest[flavour][0]:
            newest[flavour] = (key, version, filename)

    return [
        {
            "name": f"Ubuntu {version} {_FLAVOUR_LABELS[flavour]} (amd64)",
            "size": "غير متاح",
            "seeders": "رسمي",
            "source": source_label("ubuntu"),
            "torrent": f"{UBUNTU_BASE}/{release_dir}/{filename}",
            "iso": f"{UBUNTU_BASE}/{release_dir}/{filename[: -len('.torrent')]}",
            "keywords": "ubuntu linux iso official",
        }
        for flavour, (_key, version, filename) in sorted(newest.items())
    ]


async def _fill_ubuntu_sizes(session: aiohttp.ClientSession, items: list[dict[str, str]]) -> None:
    async def one(item: dict[str, str]) -> None:
        with contextlib.suppress(Exception):
            async with session.head(item["iso"], allow_redirects=True) as response:
                length = response.headers.get("Content-Length")
                if length and length.isdigit():
                    item["size"] = _format_size(int(length))

    await asyncio.gather(*(one(item) for item in items))


async def search_ubuntu(session: aiohttp.ClientSession) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    for release_dir in UBUNTU_RELEASE_DIRS:
        try:
            listing = await _fetch_text(session, f"{UBUNTU_BASE}/{release_dir}/")
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError) as exc:
            logger.warning("Ubuntu listing %s failed: %s", release_dir, exc)
            continue
        results.extend(_parse_ubuntu_listing(listing, release_dir))
    if results:
        await _fill_ubuntu_sizes(session, results)
    return results


def _parse_debian_listing(listing: str) -> list[dict[str, str]]:
    seen: set[str] = set()
    results: list[dict[str, str]] = []
    for filename in _DEBIAN_ENTRY_RE.findall(listing):
        if filename in seen:
            continue
        seen.add(filename)
        name = filename[: -len(".torrent")].replace("_", " ")
        results.append(
            {
                "name": f"Debian {name}",
                "size": "غير متاح",
                "seeders": "رسمي",
                "source": source_label("debian"),
                "torrent": f"{DEBIAN_BASE}{filename}",
                "keywords": "debian linux iso official",
            }
        )
    return results


async def search_debian(session: aiohttp.ClientSession) -> list[dict[str, str]]:
    return _parse_debian_listing(await _fetch_text(session, DEBIAN_BASE))


def _parse_fedora_payload(payload: object) -> list[dict[str, str]]:
    if not isinstance(payload, list):
        return []
    results: list[dict[str, str]] = []
    for release in payload:
        if not isinstance(release, dict):
            continue
        for item in release.get("torrents", []):
            if not isinstance(item, dict):
                continue
            filename = str(item.get("torrent") or "").strip()
            if not filename.endswith(".torrent"):
                continue
            results.append(
                {
                    "name": str(item.get("description") or filename[: -len(".torrent")]),
                    "size": str(item.get("size") or "غير متاح"),
                    "seeders": "رسمي",
                    "source": source_label("fedora"),
                    "torrent": f"{FEDORA_TORRENT_BASE}{filename}",
                    "keywords": "fedora linux iso official " + str(item.get("group") or ""),
                }
            )
    return results


async def search_fedora(session: aiohttp.ClientSession) -> list[dict[str, str]]:
    async with session.get(FEDORA_TORRENTS_JSON, allow_redirects=True) as response:
        if response.status >= 400:
            raise RuntimeError(f"HTTP {response.status}")
        payload = await response.json(content_type=None)
    return _parse_fedora_payload(payload)


Provider = Callable[[aiohttp.ClientSession], Awaitable[list[dict[str, str]]]]
_PROVIDERS: dict[str, Provider] = {
    "ubuntu": search_ubuntu,
    "debian": search_debian,
    "fedora": search_fedora,
}


async def search_torrents(query: str, config: Config) -> list[dict[str, str]]:
    """Search all currently enabled official sources and normalize the results."""
    source_ids = get_active_torrent_sources(config.torrent_sources)
    normalized_query = " ".join(query.lower().split())
    cache_key = (source_ids, normalized_query)
    now = time.monotonic()
    cached = _CACHE.get(cache_key)
    if cached and now - cached[1] < CACHE_TTL_SECONDS:
        return list(cached[0])

    timeout = aiohttp.ClientTimeout(total=FETCH_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        jobs = [_PROVIDERS[source_id](session) for source_id in source_ids if source_id in _PROVIDERS]
        raw_results = await asyncio.gather(*jobs, return_exceptions=True)

    merged: list[dict[str, str]] = []
    seen: set[str] = set()
    for source_id, source_results in zip(
        [source_id for source_id in source_ids if source_id in _PROVIDERS], raw_results
    ):
        if isinstance(source_results, Exception):
            logger.warning("Torrent provider %s failed: %s", source_id, source_results)
            continue
        for item in source_results:
            torrent_url = item["torrent"]
            if torrent_url in seen:
                continue
            seen.add(torrent_url)
            merged.append(item)

    tokens = normalized_query.split()
    if tokens:
        merged = [item for item in merged if _matches(item, tokens)]
    merged = merged[: config.torrent_max_results]
    _CACHE[cache_key] = (list(merged), now)
    return merged
