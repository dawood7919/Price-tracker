"""Segment-based media splitting with ffmpeg (stream copy, no re-encode).

Each produced part is a fully playable file on its own — never raw
byte-splitting (design doc §3.2).
"""

from __future__ import annotations

import logging
import math
import os
import subprocess
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)


class SplitError(RuntimeError):
    pass


class SplitCancelledError(SplitError):
    """Raised when a cancellation was requested mid-split."""


def get_duration_seconds(path: Path) -> float:
    """Media duration via ffprobe."""
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        return float(out.stdout.strip())
    except (subprocess.SubprocessError, ValueError, OSError) as exc:
        raise SplitError(f"ffprobe failed for {path.name}: {exc}") from exc


def compute_num_parts(size_bytes: int, target_part_bytes: int) -> int:
    """How many parts are needed. Parts are sized by bytes, not duration."""
    if target_part_bytes <= 0:
        raise SplitError("target_part_bytes must be positive")
    return max(1, math.ceil(size_bytes / target_part_bytes))


# Parts are cut by equal *duration*, but the limit is on *bytes* — and
# bitrate is not constant across a video (dense scenes carry far more data
# per second). Splitting into exactly ceil(size/limit) parts therefore aims
# at the average and lets any above-average segment overshoot the limit,
# producing a part Telegram will refuse. Aim below the limit, then verify.
SPLIT_SAFETY_MARGIN = 0.95
MAX_SPLIT_ATTEMPTS = 4


def _part_prefix(path: Path) -> str:
    return f"{path.stem}.part"


def _existing_parts(path: Path, out_dir: Path) -> list[Path]:
    """Parts previously produced for *path*, in order.

    Uses plain string matching rather than Path.glob: video titles routinely
    contain '[' and ']' (e.g. "Some Title [dQw4w9WgXcQ].mp4"), which glob
    would interpret as a character class and silently fail to match.
    """
    prefix, suffix = _part_prefix(path), path.suffix or ".mp4"
    return sorted(
        p
        for p in out_dir.iterdir()
        if p.is_file() and p.name.startswith(prefix) and p.name.endswith(suffix)
    )


def _remove_all(parts: list[Path]) -> None:
    for p in parts:
        try:
            os.remove(p)
        except OSError:
            pass


def _cut_parts(
    path: Path,
    duration: float,
    num_parts: int,
    out_dir: Path,
    cancel_event: "threading.Event | None",
) -> list[Path]:
    """Cut *path* into parts of roughly ``duration / num_parts`` each.

    Uses ffmpeg's segment muxer rather than a loop of ``-ss``/``-t`` calls.
    The loop approach is subtly broken with ``-c copy``: ``-ss`` before
    ``-i`` rewinds to the nearest *preceding* keyframe, so on a file with
    sparse keyframes every part silently starts back at 0 — parts overlap,
    the content is duplicated across them, and the last part ends up being
    the entire file (verified: parts summed to ~2x the original size). The
    segment muxer cuts at keyframes itself, producing parts that are
    non-overlapping and cover the input exactly once.
    """
    part_duration = max(0.1, duration / num_parts)
    stem, ext = path.stem, path.suffix or ".mp4"

    # Clear any leftovers from a previous attempt so the scan below only
    # ever sees parts produced by this run.
    _remove_all(_existing_parts(path, out_dir))

    if cancel_event is not None and cancel_event.is_set():
        raise SplitCancelledError("Split cancelled by user")

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(path),
        "-c",
        "copy",
        "-map",
        "0",
        "-f",
        "segment",
        "-segment_time",
        f"{part_duration:.3f}",
        "-reset_timestamps",
        "1",
        str(out_dir / f"{stem}.part%03d{ext}"),
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        # Poll instead of a blocking run() so a cancel actually stops the
        # split — this is now a single long ffmpeg call over the whole file
        # rather than one short call per part.
        while proc.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                proc.kill()
                proc.wait()
                raise SplitCancelledError("Split cancelled by user")
            time.sleep(0.5)

        if proc.returncode != 0:
            output = proc.stdout.read().decode(errors="ignore") if proc.stdout else ""
            raise SplitError(f"ffmpeg segment failed: {output.strip()[-300:] or 'unknown error'}")

        parts = _existing_parts(path, out_dir)
        if not parts:
            raise SplitError("ffmpeg مطلعش أي أجزاء.")
        return parts
    except Exception:
        # Don't leave half-finished parts on disk
        _remove_all(_existing_parts(path, out_dir))
        raise
    finally:
        if proc.poll() is None:  # pragma: no cover - safety net
            proc.kill()
            proc.wait()


def split_media(
    path: Path,
    target_part_bytes: int,
    out_dir: Path,
    cancel_event: "threading.Event | None" = None,
) -> list[Path]:
    """Split *path* into playable parts each under *target_part_bytes*.

    Uses ``-c copy`` (stream copy): fast, lossless; cuts land on the nearest
    keyframe so boundaries may shift by a second or two.
    Returns the original path unchanged if it already fits.

    *cancel_event*, if set, is checked between parts (not mid-ffmpeg-call —
    each stream-copy part is normally a few seconds, so this is a reasonable
    trade-off against the complexity of a Popen+poll hard-kill).

    Every returned part is verified to be at or under *target_part_bytes*;
    if an uneven-bitrate section overshoots, the split is retried with more
    parts rather than handing the caller a part that will fail to upload.
    """
    if target_part_bytes <= 0:
        raise SplitError("target_part_bytes must be positive")

    size = path.stat().st_size
    if size <= target_part_bytes:
        return [path]

    duration = get_duration_seconds(path)
    if duration <= 0:
        raise SplitError("المدة غير معروفة، مش هينفع يتقسم")

    out_dir.mkdir(parents=True, exist_ok=True)
    num_parts = compute_num_parts(size, max(1, int(target_part_bytes * SPLIT_SAFETY_MARGIN)))

    for attempt in range(1, MAX_SPLIT_ATTEMPTS + 1):
        parts = _cut_parts(path, duration, num_parts, out_dir, cancel_event)
        largest = max(p.stat().st_size for p in parts)
        if largest <= target_part_bytes:
            return parts

        single_segment = len(parts) == 1
        _remove_all(parts)

        if single_segment:
            # ffmpeg can only cut a stream copy at keyframes. Getting one
            # segment back despite asking for several means the file has no
            # usable keyframe to cut at, so asking for even shorter segments
            # cannot help — fail now with the real reason instead of
            # repeating an identical split three more times.
            raise SplitError(
                "الملف ده مفيهوش keyframes كفاية عشان يتقسم من غير إعادة ترميز، "
                f"وحجمه ({largest} بايت) أكبر من حد الرفع ({target_part_bytes})."
            )

        if attempt == MAX_SPLIT_ATTEMPTS:
            raise SplitError(
                f"مقدرتش أقسّم الملف لأجزاء تحت الحد بعد {MAX_SPLIT_ATTEMPTS} محاولات "
                f"(أكبر جزء طلع {largest} بايت والحد {target_part_bytes})."
            )
        # Scale up by how far the worst part overshot, so this converges in
        # one or two more attempts instead of creeping up one part at a time.
        scaled = math.ceil(num_parts * largest / (target_part_bytes * SPLIT_SAFETY_MARGIN))
        num_parts = max(num_parts + 1, scaled)
        logger.info(
            "Split overshot the limit (largest part %d > %d); retrying with %d parts",
            largest,
            target_part_bytes,
            num_parts,
        )

    raise SplitError("unreachable")  # pragma: no cover
