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


class TorrentDownloadError(RuntimeError):
    pass


VIDEO_EXTS = {
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v",
    ".mpg", ".mpeg", ".ts", ".m2ts", ".vob", ".3gp", ".ogv",
}

MAGNET_RE = re.compile(r"magnet:\?[^"]+", re.IGNORECASE)


def is_magnet_uri(text: str) -> bool:
    t = (text or "").strip()
    return t.lower().startswith("magnet:?")


def extract_magnet(text: str) -> str | None:
    if not text:
        return None
    m = MAGNET_RE.search(text)
    return m.group(0).strip() if m else None


def parse_magnet_name(magnet: str) -> str:
    """Best-effort display name from dn= parameter."""
    from urllib.parse import parse_qs, unquote, urlparse
    try:
        qs = parse_qs(urlparse(magnet).query)
        dn = qs.get("dn", [None])[0]
        if dn:
            return unquote(dn)[:120]
    except Exception:
        pass
    # xt=urn:btih:HASH
    m = re.search(r"xt=urn:btih:([a-fA-F0-9]{40}|[a-zA-Z2-7]{32})", magnet)
    if m:
        return f"magnet-{m.group(1)[:12]}"
    return "magnet-download"


def _bdecode(data: bytes, index: int = 0):
    """Minimal bencode decoder for torrent metadata."""
    if index >= len(data):
        raise ValueError("truncated bencode")
    c = data[index:index + 1]
    if c == b"i":
        end = data.index(b"e", index)
        return int(data[index + 1:end]), end + 1
    if c == b"l":
        index += 1
        out = []
        while data[index:index + 1] != b"e":
            val, index = _bdecode(data, index)
            out.append(val)
        return out, index + 1
    if c == b"d":
        index += 1
        out = {}
        while data[index:index + 1] != b"e":
            key, index = _bdecode(data, index)
            val, index = _bdecode(data, index)
            out[key] = val
        return out, index + 1
    # string: <len>:<data>
    colon = data.index(b":", index)
    length = int(data[index:colon])
    start = colon + 1
    end = start + length
    return data[start:end], end


def parse_torrent_meta(torrent_bytes: bytes) -> dict:
    """Parse name, total size, and file list from a .torrent."""
    try:
        root, _ = _bdecode(torrent_bytes)
        if not isinstance(root, dict):
            return {}
        info = root.get(b"info") or {}
        name_b = info.get(b"name") or b"torrent"
        name = name_b.decode("utf-8", errors="replace") if isinstance(name_b, bytes) else str(name_b)
        files = []
        total = 0
        if b"files" in info:
            for f in info[b"files"]:
                length = int(f.get(b"length") or 0)
                path_parts = f.get(b"path") or []
                parts = [
                    (p.decode("utf-8", errors="replace") if isinstance(p, bytes) else str(p))
                    for p in path_parts
                ]
                files.append({"path": "/".join(parts), "length": length})
                total += length
        else:
            length = int(info.get(b"length") or 0)
            files.append({"path": name, "length": length})
            total = length
        return {"name": name, "total_size": total, "files": files}
    except Exception:
        return {}


def _dir_size_bytes(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            total += p.stat().st_size
    return total


def collect_media_files(out_dir: Path) -> list[Path]:
    """Return media files sorted by size descending (largest first)."""
    files = [
        p for p in out_dir.rglob("*")
        if p.is_file()
        and p.suffix.lower() in VIDEO_EXTS
        and p.suffix.lower() != ".torrent"
    ]
    if not files:
        # fallback: any non-tiny non-torrent file
        files = [
            p for p in out_dir.rglob("*")
            if p.is_file()
            and p.suffix.lower() != ".torrent"
            and p.stat().st_size >= 1024
        ]
    files.sort(key=lambda p: p.stat().st_size, reverse=True)
    return files


def download_torrent(
    torrent_input: Path | str,
    out_dir: Path,
    progress_callback: Callable[[int, float | None, int | None], None] | None = None,
    cancel_event: "threading.Event | None" = None,
    total_hint: int | None = None,
) -> list[Path]:
    """Blocking — always run via asyncio.to_thread.

    Returns a list of media Path objects (multi-file aware).
    progress_callback(done_bytes, speed, total_or_None)
    """
    if not shutil.which("aria2c"):
        raise TorrentDownloadError("aria2c مش متثبت على السيرفر.")

    out_dir.mkdir(parents=True, exist_ok=True)

    is_magnet = isinstance(torrent_input, str) and str(torrent_input).startswith("magnet:")
    if not is_magnet and not Path(torrent_input).is_file():
        raise TorrentDownloadError("ملف التورنت غير موجود.")

    # Try to get total size from .torrent for better progress
    if total_hint is None and not is_magnet:
        try:
            meta = parse_torrent_meta(Path(torrent_input).read_bytes())
            total_hint = meta.get("total_size")
        except Exception:
            pass

    cmd = [
        "aria2c",
        "--seed-time=0",
        "--bt-stop-timeout=300",
        "--summary-interval=0",
        "--file-allocation=none",
        "--enable-dht=true",
        "--enable-dht6=true",
        "--bt-enable-lpd=true",
        "--bt-max-peers=100",
        "--max-connection-per-server=16",
        "--split=16",
        "--connect-timeout=30",
        "--timeout=60",
        "--max-tries=5",
        "--retry-wait=3",
        "--listen-port=6881-6999",
        "--dht-listen-port=6881-6999",
        "--dir",
        str(out_dir),
        str(torrent_input),
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

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
    results = [p for p in results if p.stat().st_size >= 1024]
    if not results:
        raise TorrentDownloadError(
            "الملفات الناتجة فاضية أو صغيرة جدًا — "
            "غالبًا مفيش peers أو الشبكة بتمنع BitTorrent."
            + (f"\n{detail}" if detail else "")
        )
    return results
