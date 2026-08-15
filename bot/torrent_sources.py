"""Torrent search providers used by the bot.

Official (legal ISO) sources + optional public indexes. Each provider returns
the same normalized record shape so handlers stay source-agnostic:

    name, size, seeders, source, torrent (magnet or .torrent URL), keywords

Public indexes are OFF by default — enable them from /settings.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from collections.abc import Awaitable, Callable
from html import unescape
from urllib.parse import quote, quote_plus, unquote

import aiohttp

from .config import Config
from .settings_store import TORRENT_SITES, get_active_torrent_sources, torrent_site_label

logger = logging.getLogger(__name__)

FETCH_TIMEOUT_SECONDS = 25
CACHE_TTL_SECONDS = 8 * 60
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

# ---- official --------------------------------------------------------------
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

# ---- public APIs / mirrors -------------------------------------------------
APIBAY_URL = "https://apibay.org/q.php"
YTS_API = "https://yts.mx/api/v2/list_movies.json"
EZTV_SEARCH = "https://eztv.re/api/get-torrents"
SOLID_API = "https://solidtorrents.to/api/v1/search"
TORRCSV_API = "https://torrents-csv.com/service/search"
NYAA_SEARCH = "https://nyaa.si/?f=0&c=0_0&q={query}&s=seeders&o=desc"
X1337_SEARCH = "https://www.1377x.to/search/{query}/1/"
# Community poster CDN keyed by IMDb id (tt1234567)
METAHUB_POSTER = "https://images.metahub.space/poster/medium/{imdb}/img"

_CACHE: dict[tuple[tuple[str, ...], str], tuple[list[dict[str, str]], float]] = {}


def source_label(source_id: str) -> str:
    return torrent_site_label(source_id) if source_id in TORRENT_SITES else source_id


def _matches(item: dict[str, str], tokens: list[str]) -> bool:
    haystack = " ".join(
        (item.get("name", ""), item.get("source", ""), item.get("keywords", ""))
    ).lower()
    return all(token in haystack for token in tokens)


def _format_size(size_bytes: float | int | None) -> str:
    if not size_bytes:
        return "غير متاح"
    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size) < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} PB"


def _magnet(info_hash: str, name: str, trackers: list[str] | None = None) -> str:
    """Build a magnet URI from an info-hash + display name."""
    h = info_hash.strip().lower()
    dn = quote(name[:180], safe="")
    parts = [f"magnet:?xt=urn:btih:{h}", f"dn={dn}"]
    default_trackers = trackers or [
        "udp://tracker.opentrackr.org:1337/announce",
        "udp://open.stealth.si:80/announce",
        "udp://tracker.openbittorrent.com:6969/announce",
        "udp://exodus.desync.com:6969/announce",
        "udp://tracker.torrent.eu.org:451/announce",
    ]
    for t in default_trackers:
        parts.append(f"tr={quote(t, safe='')}")
    return "&".join(parts)


async def _fetch_text(session: aiohttp.ClientSession, url: str) -> str:
    async with session.get(url, allow_redirects=True) as response:
        if response.status >= 400:
            raise RuntimeError(f"HTTP {response.status}")
        return await response.text(errors="ignore")


async def _fetch_json(session: aiohttp.ClientSession, url: str, **kwargs) -> object:
    async with session.get(url, allow_redirects=True, **kwargs) as response:
        if response.status >= 400:
            raise RuntimeError(f"HTTP {response.status}")
        return await response.json(content_type=None)


# ======================== Official providers ================================


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


async def search_ubuntu(session: aiohttp.ClientSession, query: str = "") -> list[dict[str, str]]:
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


async def search_debian(session: aiohttp.ClientSession, query: str = "") -> list[dict[str, str]]:
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


async def search_fedora(session: aiohttp.ClientSession, query: str = "") -> list[dict[str, str]]:
    payload = await _fetch_json(session, FEDORA_TORRENTS_JSON)
    return _parse_fedora_payload(payload)


# ======================== Public providers ==================================


async def search_piratebay(session: aiohttp.ClientSession, query: str) -> list[dict[str, str]]:
    """The Pirate Bay via apibay.org JSON API."""
    q = (query or "").strip()
    if len(q) < 2:
        return []
    payload = await _fetch_json(session, APIBAY_URL, params={"q": q})
    if not isinstance(payload, list):
        return []
    results: list[dict[str, str]] = []
    for item in payload[:40]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        info_hash = str(item.get("info_hash") or "").strip()
        if not name or not info_hash or info_hash == "0000000000000000000000000000000000000000":
            continue
        try:
            size = _format_size(int(item.get("size") or 0))
        except (TypeError, ValueError):
            size = "غير متاح"
        seeders = str(item.get("seeders") or "?")
        leechers = str(item.get("leechers") or "?")
        row = {
            "name": name[:180],
            "size": size,
            "seeders": f"{seeders}↑ {leechers}↓",
            "source": source_label("piratebay"),
            "torrent": _magnet(info_hash, name),
            "keywords": f"piratebay tpb {name}",
        }
        imdb = str(item.get("imdb") or "").strip()
        if imdb.startswith("tt"):
            row["thumb"] = METAHUB_POSTER.format(imdb=imdb)
        results.append(row)
    return results


async def search_yts(session: aiohttp.ClientSession, query: str) -> list[dict[str, str]]:
    """YTS movie torrents (JSON API)."""
    q = (query or "").strip()
    if len(q) < 2:
        return []
    payload = await _fetch_json(
        session,
        YTS_API,
        params={"query_term": q, "limit": 20, "sort_by": "seeds", "order_by": "desc"},
    )
    if not isinstance(payload, dict):
        return []
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    movies = data.get("movies") if isinstance(data, dict) else None
    if not isinstance(movies, list):
        return []
    results: list[dict[str, str]] = []
    for movie in movies:
        if not isinstance(movie, dict):
            continue
        title = str(movie.get("title_long") or movie.get("title") or "YTS").strip()
        year = movie.get("year")
        torrents = movie.get("torrents")
        if not isinstance(torrents, list):
            continue
        for t in torrents:
            if not isinstance(t, dict):
                continue
            quality = str(t.get("quality") or "")
            ttype = str(t.get("type") or "")
            info_hash = str(t.get("hash") or "").strip()
            if not info_hash:
                continue
            size = str(t.get("size") or "غير متاح")
            seeds = str(t.get("seeds") or "?")
            peers = str(t.get("peers") or "?")
            label = f"{title} [{quality} {ttype}]".strip()
            thumb = str(
                movie.get("medium_cover_image")
                or movie.get("large_cover_image")
                or movie.get("background_image_original")
                or ""
            ).strip()
            row = {
                "name": label[:180],
                "size": size,
                "seeders": f"{seeds}↑ {peers}↓",
                "source": source_label("yts"),
                "torrent": _magnet(info_hash, label),
                "keywords": f"yts movie {title} {year or ''}",
            }
            if thumb:
                row["thumb"] = thumb
            results.append(row)
    return results



async def search_eztv(session: aiohttp.ClientSession, query: str) -> list[dict[str, str]]:
    """EZTV — TV episodes (JSON API). Filters client-side by query tokens."""
    q = (query or "").strip()
    if len(q) < 2:
        return []
    # API is imdb/page oriented; use get-torrents and filter by name.
    try:
        payload = await _fetch_json(
            session, EZTV_SEARCH, params={"limit": 100, "page": 1}
        )
    except Exception as exc:
        logger.warning("EZTV failed: %s", exc)
        return []
    torrents = payload.get("torrents") if isinstance(payload, dict) else None
    if not isinstance(torrents, list):
        return []
    tokens = [t for t in q.lower().split() if t]
    results: list[dict[str, str]] = []
    for item in torrents:
        if not isinstance(item, dict):
            continue
        name = str(item.get("title") or item.get("filename") or "").strip()
        if not name:
            continue
        if tokens and not all(tok in name.lower() for tok in tokens):
            continue
        info_hash = str(item.get("hash") or "").strip()
        magnet = str(item.get("magnet_url") or "").strip()
        if not magnet and info_hash:
            magnet = _magnet(info_hash, name)
        if not magnet:
            continue
        try:
            size = _format_size(int(item.get("size_bytes") or item.get("size") or 0))
        except (TypeError, ValueError):
            size = "غير متاح"
        seeds = str(item.get("seeds") or item.get("seeders") or "?")
        peers = str(item.get("peers") or item.get("leechers") or "?")
        row = {
            "name": name[:180],
            "size": size,
            "seeders": f"{seeds}↑ {peers}↓",
            "source": source_label("eztv"),
            "torrent": magnet,
            "keywords": f"eztv tv {name}",
        }
        imdb = str(item.get("imdb_id") or "").strip()
        if imdb and not imdb.startswith("tt"):
            imdb = f"tt{imdb}"
        if imdb.startswith("tt"):
            row["thumb"] = METAHUB_POSTER.format(imdb=imdb)
        results.append(row)
        if len(results) >= 30:
            break
    return results


async def search_solid(session: aiohttp.ClientSession, query: str) -> list[dict[str, str]]:
    """SolidTorrents public JSON API."""
    q = (query or "").strip()
    if len(q) < 2:
        return []
    try:
        payload = await _fetch_json(
            session,
            SOLID_API,
            params={"q": q, "category": "all", "sort": "seeders"},
        )
    except Exception as exc:
        logger.warning("SolidTorrents failed: %s", exc)
        return []
    items = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []
    results: list[dict[str, str]] = []
    for item in items[:35]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("title") or item.get("name") or "").strip()
        info_hash = str(item.get("infohash") or item.get("infoHash") or "").strip()
        if not name or not info_hash:
            continue
        try:
            size = _format_size(int(item.get("size") or 0))
        except (TypeError, ValueError):
            size = "غير متاح"
        seeds = str(item.get("seeders") or "?")
        leech = str(item.get("leechers") or "?")
        results.append(
            {
                "name": name[:180],
                "size": size,
                "seeders": f"{seeds}↑ {leech}↓",
                "source": source_label("solid"),
                "torrent": _magnet(info_hash, name),
                "keywords": f"solid {name}",
            }
        )
    return results


async def search_torrcsv(session: aiohttp.ClientSession, query: str) -> list[dict[str, str]]:
    """torrents-csv.com — large public CSV-backed index (JSON)."""
    q = (query or "").strip()
    if len(q) < 2:
        return []
    try:
        payload = await _fetch_json(
            session, TORRCSV_API, params={"q": q, "size": 30}
        )
    except Exception as exc:
        logger.warning("Torrents-CSV failed: %s", exc)
        return []
    items = payload.get("torrents") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []
    results: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        info_hash = str(item.get("infohash") or item.get("info_hash") or "").strip()
        if not name or not info_hash:
            continue
        try:
            size = _format_size(int(item.get("size_bytes") or item.get("size") or 0))
        except (TypeError, ValueError):
            size = "غير متاح"
        seeds = str(item.get("seeders") or "?")
        leech = str(item.get("leechers") or "?")
        results.append(
            {
                "name": name[:180],
                "size": size,
                "seeders": f"{seeds}↑ {leech}↓",
                "source": source_label("torrcsv"),
                "torrent": _magnet(info_hash, name),
                "keywords": f"torrcsv {name}",
            }
        )
    return results


_NYAA_ROW_RE = re.compile(

    r'<a[^>]+href="(/view/\d+)"[^>]*>([^<]+)</a>.*?'
    r'href="(magnet:\?[^"]+)"'
    r'.*?<td[^>]*class="[^"]*text-center[^"]*"[^>]*>([0-9.]+\s*[KMGT]?i?B)</td>'
    r'.*?<td[^>]*class="[^"]*text-center[^"]*"[^>]*>(\d+)</td>'
    r'.*?<td[^>]*class="[^"]*text-center[^"]*"[^>]*>(\d+)</td>',
    re.IGNORECASE | re.DOTALL,
)


async def search_nyaa(session: aiohttp.ClientSession, query: str) -> list[dict[str, str]]:
    """Nyaa.si HTML search (anime / raw / etc.)."""
    q = (query or "").strip()
    if len(q) < 2:
        return []
    url = NYAA_SEARCH.format(query=quote_plus(q))
    html = await _fetch_text(session, url)
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in _NYAA_ROW_RE.finditer(html):
        _view, name, magnet, size, seeders, leechers = match.groups()
        name = unescape(name).strip()
        magnet = unescape(magnet).strip()
        if not name or not magnet or magnet in seen:
            continue
        seen.add(magnet)
        results.append(
            {
                "name": name[:180],
                "size": size.strip(),
                "seeders": f"{seeders}↑ {leechers}↓",
                "source": source_label("nyaa"),
                "torrent": magnet,
                "keywords": f"nyaa anime {name}",
            }
        )
        if len(results) >= 30:
            break
    # Fallback lighter parse if regex is too strict
    if not results:
        for m in re.finditer(r'href="(magnet:\?[^"]+)"', html, re.I):
            magnet = unescape(m.group(1))
            if magnet in seen:
                continue
            seen.add(magnet)
            dn = "nyaa"
            dn_m = re.search(r"[?&]dn=([^&]+)", magnet, re.I)
            if dn_m:
                dn = unquote(dn_m.group(1).replace("+", " "))[:180]
            results.append(
                {
                    "name": dn,
                    "size": "غير متاح",
                    "seeders": "?",
                    "source": source_label("nyaa"),
                    "torrent": magnet,
                    "keywords": f"nyaa {dn}",
                }
            )
            if len(results) >= 25:
                break
    return results


_X1337_ROW_RE = re.compile(
    r'href="(/torrent/\d+/[^"]+)"[^>]*>([^<]+)</a>.*?'
    r"<td[^>]*>([0-9.]+)</td>\s*<td[^>]*>([0-9.]+)</td>\s*<td[^>]*>([^<]+)</td>",
    re.IGNORECASE | re.DOTALL,
)


async def search_x1337(session: aiohttp.ClientSession, query: str) -> list[dict[str, str]]:
    """1337x search — resolves each result page for the magnet (best-effort)."""
    q = (query or "").strip()
    if len(q) < 2:
        return []
    url = X1337_SEARCH.format(query=quote(q.replace(" ", "%20")))
    try:
        html = await _fetch_text(session, url)
    except Exception as exc:
        logger.warning("1337x search failed: %s", exc)
        return []

    rows = list(_X1337_ROW_RE.finditer(html))[:12]
    if not rows:
        # alternate layout
        rows = list(
            re.finditer(
                r'href="(/torrent/\d+/[^"]+)"[^>]*>([^<]+)<',
                html,
                re.I,
            )
        )[:12]

    results: list[dict[str, str]] = []

    async def resolve(path: str, name: str, seeders: str = "?", size: str = "غير متاح") -> dict[str, str] | None:
        page_url = f"https://www.1377x.to{path}"
        try:
            page = await _fetch_text(session, page_url)
        except Exception:
            return None
        mag = re.search(r'href="(magnet:\?[^"]+)"', page, re.I)
        if not mag:
            return None
        return {
            "name": unescape(name).strip()[:180],
            "size": size.strip(),
            "seeders": f"{seeders}↑",
            "source": source_label("x1337"),
            "torrent": unescape(mag.group(1)),
            "keywords": f"1337x {name}",
        }

    tasks = []
    for row in rows:
        groups = row.groups()
        if len(groups) >= 5:
            path, name, seeders, _leech, size = groups[0], groups[1], groups[2], groups[3], groups[4]
            tasks.append(resolve(path, name, seeders, size))
        else:
            path, name = groups[0], groups[1]
            tasks.append(resolve(path, name))

    for item in await asyncio.gather(*tasks, return_exceptions=True):
        if isinstance(item, dict):
            results.append(item)
    return results


# ======================== Registry & public API =============================

Provider = Callable[[aiohttp.ClientSession, str], Awaitable[list[dict[str, str]]]]

_PROVIDERS: dict[str, Provider] = {
    # official
    "ubuntu": search_ubuntu,
    "debian": search_debian,
    "fedora": search_fedora,
    # public (optional — enable in /settings)
    "piratebay": search_piratebay,
    "yts": search_yts,
    "eztv": search_eztv,
    "solid": search_solid,
    "torrcsv": search_torrcsv,
    "nyaa": search_nyaa,
    "x1337": search_x1337,
}

# Sources that ignore empty queries usefully (list all ISOs)
_CATALOG_SOURCES = frozenset({"ubuntu", "debian", "fedora"})


async def search_torrents(query: str, config: Config) -> list[dict[str, str]]:
    """Search all currently enabled sources and normalize the results."""
    source_ids = get_active_torrent_sources(config.torrent_sources)
    normalized_query = " ".join(query.lower().split())
    cache_key = (source_ids, normalized_query)
    now = time.monotonic()
    cached = _CACHE.get(cache_key)
    if cached and now - cached[1] < CACHE_TTL_SECONDS:
        return list(cached[0])

    timeout = aiohttp.ClientTimeout(total=FETCH_TIMEOUT_SECONDS)
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/json,*/*"}
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        jobs = []
        active: list[str] = []
        for source_id in source_ids:
            provider = _PROVIDERS.get(source_id)
            if provider is None:
                continue
            # Public indexes need a real query; catalog sources always run.
            if source_id not in _CATALOG_SOURCES and len(normalized_query) < 2:
                continue
            jobs.append(provider(session, normalized_query))
            active.append(source_id)
        raw_results = await asyncio.gather(*jobs, return_exceptions=True)

    merged: list[dict[str, str]] = []
    seen: set[str] = set()
    for source_id, source_results in zip(active, raw_results):
        if isinstance(source_results, Exception):
            logger.warning("Torrent provider %s failed: %s", source_id, source_results)
            continue
        for item in source_results:
            torrent_url = item.get("torrent") or ""
            if not torrent_url or torrent_url in seen:
                continue
            seen.add(torrent_url)
            merged.append(item)

    tokens = normalized_query.split()
    if tokens:
        # Official catalog still benefits from token filter; public already searched by query.
        filtered = [item for item in merged if _matches(item, tokens)]
        # Keep public hits even if token filter is strict on extra keywords
        if filtered:
            merged = filtered

    # Prefer higher seeders; items with a poster image float slightly higher for UX.
    def _rank(item: dict[str, str]) -> tuple[int, int]:
        m = re.search(r"(\d+)", item.get("seeders") or "")
        seeds = int(m.group(1)) if m else 0
        has_thumb = 1 if (item.get("thumb") or "").startswith("http") else 0
        return (has_thumb, seeds)

    merged.sort(key=_rank, reverse=True)
    merged = merged[: config.torrent_max_results]
    _CACHE[cache_key] = (list(merged), now)
    return merged
