"""Extractor layer on top of yt-dlp.

Blocking yt-dlp calls are always executed in a thread by the caller
(``asyncio.to_thread``) — never inside the event loop.
"""

from __future__ import annotations

import logging
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import psutil
import yt_dlp

from .config import Config
from .utils import sanitize_filename

logger = logging.getLogger(__name__)

try:
    import curl_cffi  # noqa: F401
    from yt_dlp.networking.impersonate import ImpersonateTarget

    _HAS_CURL_CFFI = True
except ImportError:
    _HAS_CURL_CFFI = False

AUDIO_FORMAT_KEYS: tuple[str, ...] = ("mp3", "m4a", "wav", "flac")

FORMAT_SELECTORS: dict[str, str] = {
    "best": (
        "best[height>=2160]/best[height=2160]/2160p/"
        "best[height>=1440]/best[height=1440]/1440p/"
        "best[height>=1080]/best[height=1080]/1080p/"
        "bestvideo*+bestaudio/bestvideo*/best"
    ),
    "2160": (
        "best[height=2160]/2160p/"
        "bestvideo*[height<=2160]+bestaudio/best[height<=2160]/best"
    ),
    "1440": (
        "best[height=1440]/1440p/"
        "bestvideo*[height<=1440]+bestaudio/best[height<=1440]/best"
    ),
    "1080": (
        "best[height=1080]/1080p/"
        "bestvideo*[height<=1080]+bestaudio/best[height<=1080]/best"
    ),
    "720": (
        "best[height=720]/720p/"
        "bestvideo*[height<=720]+bestaudio/best[height<=720]/best"
    ),
    "480": (
        "best[height=480]/480p/"
        "bestvideo*[height<=480]+bestaudio/best[height<=480]/best"
    ),
    "360": (
        "best[height=360]/360p/"
        "bestvideo*[height<=360]+bestaudio/best[height<=360]/best"
    ),
    **{key: "bestaudio/best" for key in AUDIO_FORMAT_KEYS},
}

FORMAT_LABELS: dict[str, str] = {
    "best": "🌟 أفضل جودة",
    "2160": "🎬 4K (2160p)",
    "1440": "🎞️ 1440p",
    "1080": "🎥 1080p",
    "720": "📺 720p",
    "480": "📱 480p",
    "360": "📲 360p",
    "mp3": "🎵 MP3",
    "m4a": "🎵 M4A",
    "wav": "🎵 WAV",
    "flac": "🎵 FLAC",
}

QUALITY_TIERS: dict[str, int] = {
    "2160": 2160,
    "1440": 1440,
    "1080": 1080,
    "720": 720,
    "480": 480,
    "360": 360,
}

_ARIA2_CONNECTIONS = 16
_CONCURRENT_FRAGMENTS = 16


def available_qualities(formats: list[dict[str, Any]]) -> list[str]:
    heights = [
        f.get("height")
        for f in formats
        if f.get("height") and f.get("vcodec") not in (None, "none")
    ]
    if not heights:
        for f in formats:
            for key in ("format_id", "format", "resolution"):
                val = str(f.get(key) or "").lower()
                for h in (2160, 1440, 1080, 720, 480, 360):
                    if f"{h}" in val:
                        heights.append(h)
                        break
    tiers = ["best"]
    if heights:
        max_height = max(heights)
        for key, ceiling in QUALITY_TIERS.items():
            if ceiling > max_height:
                continue
            if any(h >= ceiling * 0.9 for h in heights):
                tiers.append(key)
    tiers.extend(AUDIO_FORMAT_KEYS)
    return tiers


class DownloadError(RuntimeError):
    pass


class DownloadCancelledError(DownloadError):
    """Raised when a job was cancelled by the user."""


@dataclass
class MediaInfo:
    url: str
    title: str
    duration: float | None
    is_playlist: bool
    uploader: str | None = None
    entries: list[dict[str, Any]] = field(default_factory=list)
    formats: list[dict[str, Any]] = field(default_factory=list)
    thumbnail: str | None = None


@dataclass
class DownloadProgress:
    status: str = "starting"
    downloaded_bytes: int = 0
    total_bytes: int | None = None
    speed: float | None = None


@dataclass
class DownloadResult:
    file_path: Path
    thumbnail_path: Path | None = None


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


class YTDLPDownloader:
    def __init__(self, config: Config) -> None:
        self._config = config

    def _prepare_url(self, url: str) -> str:
        """Site-specific URL rewriting before yt-dlp."""
        if "hqporner.com" in url.lower():
            try:
                from .sites.hqporner import resolve_playable_url_sync

                resolved = resolve_playable_url_sync(url)
                if resolved and resolved != url:
                    logger.info("hqporner resolved %s -> %s", url[:80], resolved[:120])
                return resolved or url
            except Exception:
                logger.exception("hqporner resolve failed; using original URL")
        return url

    def extract_info(self, url: str) -> MediaInfo:
        url = self._prepare_url(url)
        opts = self._base_opts()
        opts.update(
            {
                "skip_download": True,
                "extract_flat": "in_playlist",
                "playlist_items": f"1:{self._config.max_playlist_items}",
            }
        )
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except yt_dlp.utils.DownloadError as exc:
            raise DownloadError(self._clean_ytdlp_error(exc)) from exc
        if not info:
            raise DownloadError("مقدرتش أقرأ معلومات اللينك ده.")

        if info.get("_type") == "playlist" or "entries" in info:
            entries = [e for e in (info.get("entries") or []) if e]
            if not entries:
                raise DownloadError("البلايليست فاضية أو كل فيديوهاتها غير متاحة.")
            return MediaInfo(
                url=url,
                title=info.get("title") or "Playlist",
                duration=None,
                is_playlist=True,
                uploader=info.get("uploader"),
                entries=entries,
            )
        return MediaInfo(
            url=url,
            title=info.get("title") or "Video",
            duration=info.get("duration"),
            is_playlist=False,
            uploader=info.get("uploader"),
            formats=info.get("formats") or [],
            thumbnail=info.get("thumbnail"),
        )

    def download(
        self,
        url: str,
        format_key: str,
        out_dir: Path,
        progress_callback: Callable[[DownloadProgress], None] | None = None,
        cancel_event: "threading.Event | None" = None,
    ) -> DownloadResult:
        url = self._prepare_url(url)
        out_dir.mkdir(parents=True, exist_ok=True)
        selector = FORMAT_SELECTORS.get(format_key, FORMAT_SELECTORS["best"])
        is_audio = format_key in AUDIO_FORMAT_KEYS

        opts = self._base_opts()
        # Direct mp4 links: no format gymnastics
        if url.lower().endswith(".mp4") or ".mp4?" in url.lower():
            opts.update(
                {
                    "format": "best",
                    "outtmpl": str(out_dir / "%(title).80B [%(id)s].%(ext)s"),
                    "noplaylist": True,
                    "progress_hooks": [self._make_hook(progress_callback, cancel_event)],
                    "postprocessor_hooks": [self._make_pp_hook(progress_callback)],
                    "writethumbnail": False,
                    "http_headers": {
                        **opts.get("http_headers", {}),
                        "Referer": "https://hqporner.com/",
                    },
                }
            )
        else:
            opts.update(
                {
                    "format": selector,
                    "format_sort": ["res", "br", "size"],
                    "format_sort_force": True,
                    "outtmpl": str(out_dir / "%(title).80B [%(id)s].%(ext)s"),
                    "noplaylist": True,
                    "merge_output_format": "mp4",
                    "progress_hooks": [self._make_hook(progress_callback, cancel_event)],
                    "postprocessor_hooks": [self._make_pp_hook(progress_callback)],
                    "writethumbnail": not is_audio,
                    "concurrent_fragment_downloads": _CONCURRENT_FRAGMENTS,
                    "http_chunk_size": 10 * 1024 * 1024,
                }
            )
        if is_audio:
            opts.pop("merge_output_format", None)
            quality = "0" if format_key in ("wav", "flac") else "192"
            opts["postprocessors"] = [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": format_key,
                    "preferredquality": quality,
                }
            ]

        using_aria2c = bool(shutil.which("aria2c"))
        if using_aria2c:
            n = str(_ARIA2_CONNECTIONS)
            opts["external_downloader"] = {"default": "aria2c"}
            opts["external_downloader_args"] = {
                "aria2c": [
                    f"--max-connection-per-server={n}",
                    f"--split={n}",
                    "--min-split-size=1M",
                    "--max-concurrent-downloads=1",
                    "--file-allocation=none",
                    "--summary-interval=0",
                    "--continue=true",
                    "--max-tries=8",
                    "--retry-wait=1",
                    "--timeout=30",
                    "--connect-timeout=15",
                    "--lowest-speed-limit=1K",
                    "--allow-overwrite=true",
                    "--auto-file-renaming=false",
                    "--console-log-level=error",
                ]
            }

        poller_stop = threading.Event()
        poller_thread: threading.Thread | None = None
        if using_aria2c:
            total_estimate = self._estimate_total_bytes(url, format_key)
            poller_thread = threading.Thread(
                target=self._poll_aria2c_progress,
                args=(
                    out_dir,
                    total_estimate,
                    progress_callback,
                    cancel_event,
                    poller_stop,
                ),
                daemon=True,
            )
            poller_thread.start()

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.extract_info(url, download=True)
        except yt_dlp.utils.DownloadCancelled as exc:
            raise DownloadCancelledError(str(exc) or "Download cancelled by user") from exc
        except yt_dlp.utils.DownloadError as exc:
            if cancel_event is not None and cancel_event.is_set():
                raise DownloadCancelledError("Download cancelled by user") from exc
            raise DownloadError(self._clean_ytdlp_error(exc)) from exc
        finally:
            poller_stop.set()
            if poller_thread is not None:
                poller_thread.join(timeout=2.0)

        result = self._pick_output_file(out_dir)
        if result is None:
            raise DownloadError("التحميل خلص من غير ما ينتج ملف (فشل صامت من yt-dlp).")
        if result.stat().st_size == 0:
            raise DownloadError("الملف المحمّل حجمه صفر.")
        return DownloadResult(
            file_path=result, thumbnail_path=self._pick_thumbnail_file(out_dir)
        )

    def _base_opts(self) -> dict[str, Any]:
        opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "socket_timeout": 30,
            "retries": 10,
            "fragment_retries": 10,
            "file_access_retries": 5,
            "extractor_retries": 3,
            "nocheckcertificate": False,
            "restrictfilenames": False,
            "continuedl": True,
            "source_address": "0.0.0.0",
            "extractor_args": {
                "youtube": {"player_client": ["android", "web", "ios"]}
            },
            "http_headers": {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
            },
        }
        if self._config.ytdlp_cookies_file:
            cookies = Path(self._config.ytdlp_cookies_file)
            if cookies.exists():
                opts["cookiefile"] = str(cookies)
            else:
                logger.warning("YTDLP_COOKIES_FILE set but not found: %s", cookies)
        if _HAS_CURL_CFFI:
            opts["impersonate"] = ImpersonateTarget()
            opts["extractor_args"]["generic"] = {"impersonate": ["chrome"]}
        return opts

    def _estimate_total_bytes(self, url: str, format_key: str) -> int | None:
        try:
            opts = {**self._base_opts(), "skip_download": True, "quiet": True}
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception:
            return None
        if not info:
            return None
        formats = info.get("formats") or []
        if format_key in AUDIO_FORMAT_KEYS:
            candidates = [f for f in formats if f.get("acodec") not in (None, "none")]
        else:
            ceiling = QUALITY_TIERS.get(format_key)
            candidates = formats
            if ceiling:
                narrowed = [
                    f for f in formats if f.get("height") and f["height"] <= ceiling
                ]
                if narrowed:
                    candidates = narrowed
        sizes = [f.get("filesize") or f.get("filesize_approx") for f in candidates]
        sizes = [s for s in sizes if s]
        return max(sizes) if sizes else None

    @staticmethod
    def _poll_aria2c_progress(
        out_dir: Path,
        total: int | None,
        cb: Callable[[DownloadProgress], None] | None,
        cancel_event: "threading.Event | None",
        stop_event: threading.Event,
        poll_interval: float = 1.0,
    ) -> None:
        last_bytes = 0
        last_time = time.monotonic()
        while not stop_event.wait(poll_interval):
            if cancel_event is not None and cancel_event.is_set():
                YTDLPDownloader._kill_aria2c_for(out_dir)
                return
            try:
                candidates = [
                    p
                    for p in out_dir.iterdir()
                    if p.is_file()
                    and not p.name.endswith((".aria2", ".ytdl"))
                    and p.suffix.lower() not in IMAGE_EXTS
                ]
                if not candidates:
                    continue
                target = max(candidates, key=lambda p: p.stat().st_size)
                st = target.stat()
                done = st.st_blocks * 512
            except (OSError, ValueError):
                continue
            now = time.monotonic()
            elapsed = now - last_time
            speed = (done - last_bytes) / elapsed if elapsed > 0 else None
            last_bytes, last_time = done, now
            if cb is not None:
                cb(
                    DownloadProgress(
                        status="downloading",
                        downloaded_bytes=done,
                        total_bytes=total,
                        speed=speed,
                    )
                )

    @staticmethod
    def _kill_aria2c_for(out_dir: Path) -> None:
        target = str(out_dir)
        for proc in psutil.process_iter(("name", "cmdline")):
            try:
                if proc.info.get("name") != "aria2c":
                    continue
                cmdline = proc.info.get("cmdline") or []
                if any(target in arg for arg in cmdline):
                    proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

    @staticmethod
    def _make_hook(
        cb: Callable[[DownloadProgress], None] | None,
        cancel_event: "threading.Event | None" = None,
    ):
        def hook(d: dict[str, Any]) -> None:
            if cancel_event is not None and cancel_event.is_set():
                raise yt_dlp.utils.DownloadCancelled("Download cancelled by user")
            if cb is None:
                return
            status = d.get("status")
            if status == "downloading":
                cb(
                    DownloadProgress(
                        status="downloading",
                        downloaded_bytes=int(d.get("downloaded_bytes") or 0),
                        total_bytes=d.get("total_bytes") or d.get("total_bytes_estimate"),
                        speed=d.get("speed"),
                    )
                )
            elif status == "finished":
                cb(DownloadProgress(status="processing"))

        return hook

    @staticmethod
    def _make_pp_hook(cb: Callable[[DownloadProgress], None] | None):
        def hook(d: dict[str, Any]) -> None:
            if cb is not None and d.get("status") == "finished":
                cb(DownloadProgress(status="finished"))

        return hook

    @staticmethod
    def _pick_output_file(out_dir: Path) -> Path | None:
        files = [
            p
            for p in out_dir.iterdir()
            if p.is_file()
            and not p.name.endswith((".part", ".ytdl", ".tmp"))
            and p.suffix.lower() not in IMAGE_EXTS
        ]
        if not files:
            return None
        return max(files, key=lambda p: p.stat().st_size)

    @staticmethod
    def _pick_thumbnail_file(out_dir: Path) -> Path | None:
        images = [
            p for p in out_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS
        ]
        return images[0] if images else None

    @staticmethod
    def _clean_ytdlp_error(exc: Exception) -> str:
        msg = str(exc)
        msg = msg.replace("ERROR:", "").strip()
        if "Sign in to confirm" in msg or "login" in msg.lower():
            return "الموقع طالب تسجيل دخول — ممكن تحتاج كوكيز (YTDLP_COOKIES_FILE)."
        if "Unsupported URL" in msg:
            return "الموقع ده مش مدعوم أو مفيش ستريم ظاهر."
        if not msg:
            return "فشل التحميل لسبب غير معروف."
        return msg[:200]


def safe_title_for_caption(title: str) -> str:
    return sanitize_filename(title, max_len=60)
