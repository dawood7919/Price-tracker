import subprocess
import threading
from pathlib import Path

import pytest

from bot.splitter import (
    SplitCancelledError,
    SplitError,
    compute_num_parts,
    get_duration_seconds,
    split_media,
)

GB = 1024**3
LIMIT = 2 * GB  # exact 2GB per-part target


class TestComputeNumParts:
    def test_under_limit_is_one_part(self):
        assert compute_num_parts(int(1.5 * GB), LIMIT) == 1

    def test_exactly_at_limit_is_one_part(self):
        # requirement: files <= 2GB must never be split
        assert compute_num_parts(LIMIT, LIMIT) == 1

    def test_just_over_limit_is_two_parts(self):
        # requirement: anything over 2GB must be split
        assert compute_num_parts(LIMIT + 1, LIMIT) == 2

    def test_4gb_file_is_exactly_two_parts(self):
        assert compute_num_parts(4 * GB, LIMIT) == 2

    def test_large_file(self):
        assert compute_num_parts(6 * GB, LIMIT) == 3

    def test_zero_limit_raises(self):
        with pytest.raises(SplitError):
            compute_num_parts(GB, 0)


@pytest.fixture(scope="module")
def sample_video(tmp_path_factory) -> Path:
    """A tiny real video with a keyframe every second, so splitting is
    tested end-to-end.

    ``-g 24`` (at 24fps => 1 keyframe/sec) matters: ffmpeg can only cut a
    stream copy at keyframes, and libx264's default GOP is ~250 frames,
    which for a 10s clip means effectively no keyframe to cut at. Real
    videos carry keyframes every few seconds, so this matches reality —
    see ``keyframeless_video`` for the opposite case.
    """
    path = tmp_path_factory.mktemp("media") / "sample.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=10:size=320x240:rate=24",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
            "-c:v", "libx264", "-preset", "ultrafast", "-g", "24", "-c:a", "aac",
            "-shortest", str(path),
        ],
        check=True, capture_output=True,
    )
    return path


@pytest.fixture(scope="module")
def keyframeless_video(tmp_path_factory) -> Path:
    """A clip with no usable keyframe to cut at (default long GOP)."""
    path = tmp_path_factory.mktemp("media-nokf") / "nokf.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=10:size=320x240:rate=24",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
            "-shortest", str(path),
        ],
        check=True, capture_output=True,
    )
    return path


class TestSplitMedia:
    def test_small_file_returned_unchanged(self, sample_video, tmp_path):
        parts = split_media(sample_video, sample_video.stat().st_size + 1, tmp_path)
        assert parts == [sample_video]

    def test_splits_into_playable_parts(self, sample_video, tmp_path):
        size = sample_video.stat().st_size
        limit = size // 3 + 1
        parts = split_media(sample_video, limit, tmp_path)
        assert len(parts) >= 2
        for part in parts:
            assert part.exists()
            assert part.stat().st_size > 0
            # each part must be a valid media file on its own
            assert get_duration_seconds(part) > 0

    def test_every_part_is_under_the_limit(self, sample_video, tmp_path):
        """The limit is on bytes but cuts happen on time, so an uneven
        stretch can overshoot the average — split_media must verify and
        re-split rather than hand back a part Telegram will reject."""
        size = sample_video.stat().st_size
        limit = size // 3 + 1
        parts = split_media(sample_video, limit, tmp_path)
        assert all(p.stat().st_size <= limit for p in parts)

    def test_parts_do_not_overlap(self, sample_video, tmp_path):
        """Regression test for a real bug: cutting with a loop of
        ``-ss``/``-t`` + ``-c copy`` made every part rewind to the nearest
        preceding keyframe, so parts overlapped, duplicated content, and
        the final part was the entire file — the parts summed to roughly
        twice the original size. Non-overlapping parts sum to about the
        original (plus small per-part container overhead).
        """
        size = sample_video.stat().st_size
        parts = split_media(sample_video, size // 3 + 1, tmp_path)
        total = sum(p.stat().st_size for p in parts)
        assert total < size * 1.5

        # ...and no single part may be the whole file over again
        assert max(p.stat().st_size for p in parts) < size

    def test_keyframeless_file_raises_instead_of_oversized_parts(
        self, keyframeless_video, tmp_path
    ):
        """A stream copy can only cut at keyframes. With none available the
        honest outcome is a clear error, not a "part" that is really the
        whole file and will fail to upload."""
        size = keyframeless_video.stat().st_size
        with pytest.raises(SplitError, match="keyframes"):
            split_media(keyframeless_video, size // 3 + 1, tmp_path)
        assert list(tmp_path.iterdir()) == []  # nothing left behind

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises((SplitError, OSError)):
            split_media(tmp_path / "nope.mp4", 100, tmp_path)

    def test_cancel_event_stops_before_any_part_is_cut(self, sample_video, tmp_path):
        size = sample_video.stat().st_size
        cancel_event = threading.Event()
        cancel_event.set()  # already cancelled before the first part
        with pytest.raises(SplitCancelledError):
            split_media(sample_video, size // 3 + 1, tmp_path, cancel_event=cancel_event)
        assert list(tmp_path.iterdir()) == []  # no half-finished parts left behind


class TestGetDuration:
    def test_reads_duration(self, sample_video):
        duration = get_duration_seconds(sample_video)
        assert 9.0 <= duration <= 11.0
