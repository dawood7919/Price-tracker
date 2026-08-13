"""Download the real content described by a .torrent file, via aria2c
(already used elsewhere in this project for HTTP downloads — it also
speaks BitTorrent directly). Generic: this module has no idea what the
.torrent file describes; the caller is responsible for only pointing it at
legitimate content.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable


class TorrentDownloadError(RuntimeError):
    pass


def _dir_size_bytes(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            total += p.stat().st_size
    return total


def _pick_largest_file(out_dir: Path) -> Path | None:
    files = [p for p in out_dir.rglob("*") if p.is_file() and p.suffix != ".torrent"]
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_size)


def download_torrent(
    torrent_file: Path,
    out_dir: Path,
    progress_callback: Callable[[int, float | None], None] | None = None,
    cancel_event: "threading.Event | None" = None,
) -> Path:
    """Blocking — always run via asyncio.to_thread.

    *progress_callback*, if given, is called roughly once a second with
    (downloaded_bytes, speed_bytes_per_sec). Total size isn't reported —
    getting it would mean parsing the .torrent file's bencoded metadata
    ourselves, which isn't worth it here; callers show total as unknown.
    """
    if not shutil.which("aria2c"):
        raise TorrentDownloadError("aria2c مش متثبت على السيرفر.")

    out_dir.mkdir(parents=True, exist_ok=True)
    # --bt-stop-timeout: stop ONLY after N consecutive seconds at 0 B/s.
    # Was previously 1s, which killed every torrent before peers could connect.
    # 300s (5 min) gives trackers + DHT time to find seeders on a normal VPS.
    #
    # DHT is enabled here: on a real Ubuntu VPS (e.g. Tencent Cloud) outbound
    # UDP is usually allowed, and official Ubuntu torrents often need DHT (or
    # extra trackers) when the few listed trackers are slow/unreachable.
    # On hosts that truly block all BT/UDP, the download will still fail —
    # but with a clear timeout after 5 minutes instead of an instant 0 B/s exit.
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
        str(torrent_file),
    ]
    # aria2c writes most of its actual diagnostic/error output to stdout, not
    # stderr — capturing stderr alone (as this used to) silently threw away
    # the real reason for any failure, leaving only a bare "فشل تحميل
    # التورنت." with nothing to act on.
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
            progress_callback(done, speed)

    output = proc.stdout.read().decode(errors="ignore").strip() if proc.stdout else ""
    detail = output[-500:] if output else ""

    if proc.returncode != 0:
        raise TorrentDownloadError(f"فشل تحميل التورنت{': ' + detail if detail else ''}.")

    result = _pick_largest_file(out_dir)
    if result is None:
        raise TorrentDownloadError(
            "التحميل خلص من غير ما ينتج ملف"
            + (f": {detail}" if detail else ".")
        )
    # Guard against a "successful" exit that only left an empty/tiny stub
    # (e.g. metadata-only after a zero-speed timeout that still returned 0).
    if result.stat().st_size < 1024:
        raise TorrentDownloadError(
            f"الملف الناتج فاضي أو صغير جدًا ({result.stat().st_size} bytes) — "
            "غالبًا مفيش peers أو الشبكة بتمنع BitTorrent."
            + (f"\n{detail}" if detail else "")
        )
    return result
