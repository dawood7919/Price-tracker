"""Full BitTorrent download engine via aria2c.

Supports:
  - local .torrent files
  - magnet: URIs
  - progress with total size (parsed from torrent metadata when available)
  - cancel via threading.Event
  - DHT / LPD / multiple trackers

Generic: this module does not decide *what* is legitimate to download —
callers are responsible for the source of the torrent/magnet.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable
from urllib.parse import unquote


class TorrentDownloadError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Minimal bencode reader (enough to extract name + total length)
# ---------------------------------------------------------------------------


def _bdecode(data: bytes, index: int = 0) -> tuple[object, int]:
    """Decode a single bencoded value starting at *index*. Returns (value, new_index)."""
    if index >= len(data):
        raise ValueError("truncated bencode")
    ch = data[index : index + 1]
    if ch == b"i":
        end = data.index(b"e", index)
        return int(data[index + 1 : end]), end + 1
    if ch == b"l":
        index += 1
        out: list[object] = []
        while data[index : index + 1] != b"e":
            val, index = _bdecode(data, index)
            out.append(val)
        return out, index + 1
    if ch == b"d":
        index += 1
        out_d: dict[bytes, object] = {}
        while data[index : index + 1] != b"e":
            key, index = _bdecode(data, index)
            val, index = _bdecode(data, index)
            if not isinstance(key, bytes):
                raise ValueError("dict key must be bytes")
            out_d[key] = val
        return out_d, index + 1
    if ch.isdigit():
        colon = data.index(b":", index)
        length = int(data[index:colon])
        start = colon + 1
        return data[start : start + length], start + length
    raise ValueError(f"invalid bencode at {index}")


def parse_torrent_meta(torrent_bytes: bytes) -> dict:
    """Extract useful fields from a .torrent file without external deps.

    Returns keys: name (str), total_length (int|None), files (list of
    {path, length}), info_hash (str hex).
    """
    root, _ = _bdecode(torrent_bytes)
    if not isinstance(root, dict):
        return {"name": "torrent", "total_length": None, "files": [], "info_hash": ""}

    info = root.get(b"info")
    if not isinstance(info, dict):
        return {"name": "torrent", "total_length": None, "files": [], "info_hash": ""}

    info_hash = ""
    try:
        # Locate the raw info dict in the original payload for a real SHA1.
        # Pattern: 4:infod ... e  (the dict that follows the key "info")
        key = b"4:info"
        pos = torrent_bytes.find(key)
        if pos >= 0:
            start = pos + len(key)
            _, end = _bdecode(torrent_bytes, start)
            info_hash = hashlib.sha1(torrent_bytes[start:end]).hexdigest()
    except Exception:
        pass

    name_b = info.get(b"name") or b"torrent"
    name = name_b.decode("utf-8", errors="replace") if isinstance(name_b, bytes) else str(name_b)

    files: list[dict] = []
    total: int | None = None

    if b"length" in info:
        length = info[b"length"]
        if isinstance(length, int):
            total = length
            files.append({"path": name, "length": length})
    elif b"files" in info and isinstance(info[b"files"], list):
        total = 0
        for entry in info[b"files"]:
            if not isinstance(entry, dict):
                continue
            length = entry.get(b"length")
            path_parts = entry.get(b"path")
            if not isinstance(length, int):
                continue
            if isinstance(path_parts, list):
                parts = [
                    p.decode("utf-8", errors="replace") if isinstance(p, bytes) else str(p)
                    for p in path_parts
                ]
                path = "/".join(parts)
            else:
                path = "file"
            files.append({"path": path, "length": length})
            total += length

    return {
        "name": name,
        "total_length": total,
        "files": files,
        "info_hash": info_hash,
    }


def parse_magnet_name(magnet: str) -> str:
    """Best-effort display name from a magnet URI (dn= parameter)."""
    m = re.search(r"[?&]dn=([^&]+)", magnet, re.IGNORECASE)
    if not m:
        h = re.search(r"btih:([a-fA-F0-9]{40}|[a-zA-Z2-7]{32})", magnet)
        if h:
            return f"magnet-{h.group(1)[:8]}"
        return "magnet"
    return unquote(m.group(1).replace("+", " "))[:120]


def is_magnet_uri(text: str) -> bool:
    return bool(text) and text.strip().lower().startswith("magnet:?")


def extract_magnet(text: str) -> str | None:
    """Pull the first magnet: URI out of free-form text."""
    if not text:
        return None
    text = text.strip()
    if text.lower().startswith("magnet:?"):
        return text.split()[0]
    m = re.search(r"(magnet:\?[^\s<>\"']+)", text, re.IGNORECASE)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


def _dir_size_bytes(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        if p.is_file() and p.suffix != ".torrent" and not p.name.endswith(".aria2"):
            total += p.stat().st_size
    return total


VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".ts", ".flv"}
AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".ogg", ".opus", ".flac", ".wav"}
MEDIA_EXTS = VIDEO_EXTS | AUDIO_EXTS
# skip tiny samples / nfo / images
_SKIP_SUFFIXES = {".torrent", ".aria2", ".nfo", ".txt", ".jpg", ".jpeg", ".png", ".gif", ".srt", ".ass", ".ssa", ".url", ".lnk"}


def _is_downloaded_file(p: Path) -> bool:
    return (
        p.is_file()
        and p.suffix.lower() not in _SKIP_SUFFIXES
        and not p.name.endswith(".aria2")
        and p.name != "meta.torrent"
    )


def collect_media_files(out_dir: Path) -> list[Path]:
    """All video/audio files in the download tree, largest first.

    Falls back to the single largest non-meta file when no media extension matches
    (e.g. an ISO or archive).
    """
    files = [p for p in out_dir.rglob("*") if _is_downloaded_file(p)]
    if not files:
        return []
    media = [p for p in files if p.suffix.lower() in MEDIA_EXTS and p.stat().st_size >= 1024]
    if media:
        return sorted(media, key=lambda p: p.stat().st_size, reverse=True)
    # non-media payload (ISO, etc.) — send the largest file only
    return [max(files, key=lambda p: p.stat().st_size)]


def _pick_result_file(out_dir: Path) -> Path | None:
    """Back-compat: largest primary file."""
    files = collect_media_files(out_dir)
    return files[0] if files else None


def download_torrent(
    torrent_input: Path | str,
    out_dir: Path,
    progress_callback: Callable[[int, float | None, int | None], None] | None = None,
    cancel_event: threading.Event | None = None,
    total_hint: int | None = None,
) -> list[Path]:
    """Blocking BitTorrent download — always run via ``asyncio.to_thread``.

    *torrent_input*: local ``Path`` to a ``.torrent`` file, **or** a ``magnet:``
    URI string.

    *progress_callback(downloaded_bytes, speed_bps, total_bytes_or_None)* is
    invoked roughly once per second.

    *total_hint*: optional known total size (e.g. from parsed metadata).

    Returns a list of media file paths (videos first, largest first). Callers upload each file one-by-one; oversize files are split by UploadManager.
    """
    if not shutil.which("aria2c"):
        raise TorrentDownloadError("aria2c مش متثبت على السيرفر.")

    out_dir.mkdir(parents=True, exist_ok=True)

    is_magnet = isinstance(torrent_input, str) and is_magnet_uri(str(torrent_input))
    if not is_magnet and isinstance(torrent_input, str):
        torrent_input = Path(torrent_input)

    if not is_magnet:
        path = Path(torrent_input)
        if not path.is_file():
            raise TorrentDownloadError(f"ملف التورنت مش موجود: {path}")
        if total_hint is None:
            try:
                meta = parse_torrent_meta(path.read_bytes())
                total_hint = meta.get("total_length")  # type: ignore[assignment]
            except Exception:
                pass

    # --bt-stop-timeout: stop only after N consecutive seconds at 0 B/s.
    # 300s gives trackers + DHT time to find seeders on a normal VPS.
    cmd = [
        "aria2c",
        "--seed-time=0",
        "--bt-stop-timeout=300",
        "--bt-tracker-connect-timeout=30",
        "--bt-tracker-timeout=60",
        "--summary-interval=0",
        "--file-allocation=none",
        "--enable-dht=true",
        "--enable-dht6=true",
        "--bt-enable-lpd=true",
        "--bt-max-peers=80",
        "--max-connection-per-server=16",
        "--split=16",
        "--connect-timeout=30",
        "--timeout=60",
        "--max-tries=5",
        "--retry-wait=3",
        "--listen-port=6881-6999",
        "--dht-listen-port=6881-6999",
        "--check-certificate=false",
        "--allow-overwrite=true",
        "--auto-file-renaming=false",
        "--dir",
        str(out_dir),
        str(torrent_input),
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=str(out_dir),
    )

    last_bytes = 0
    last_time = time.monotonic()
    while proc.poll() is None:
        if cancel_event is not None and cancel_event.is_set():
            proc.kill()
            proc.wait()
            raise TorrentDownloadError("اتلغى التحميل.")
        time.sleep(1.0)
        done = _dir_size_bytes(out_dir)
        now = time.monotonic()
        elapsed = now - last_time
        speed = (done - last_bytes) / elapsed if elapsed > 0 else None
        last_bytes, last_time = done, now
        if progress_callback is not None:
            progress_callback(done, speed, total_hint)

    output = proc.stdout.read().decode(errors="ignore").strip() if proc.stdout else ""
    detail = output[-600:] if output else ""

    if proc.returncode != 0:
        raise TorrentDownloadError(
            f"فشل تحميل التورنت{': ' + detail if detail else ''}."
        )

    results = collect_media_files(out_dir)
    if not results:
        raise TorrentDownloadError(
            "التحميل خلص من غير ما ينتج ملف"
            + (f": {detail}" if detail else ".")
        )
    # drop tiny stubs
    results = [p for p in results if p.stat().st_size >= 1024]
    if not results:
        raise TorrentDownloadError(
            "الملفات الناتجة فاضية أو صغيرة جدًا — "
            "غالبًا مفيش peers أو الشبكة بتمنع BitTorrent."
            + (f"\n{detail}" if detail else "")
        )
    return results
