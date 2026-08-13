from bot.utils import (
    build_progress_bar,
    disk_usage_mb,
    error_message,
    format_duration,
    format_eta,
    format_progress_panel,
    format_size,
    format_speed,
    sanitize_filename,
    system_stats,
    truncate_text,
)


class TestFormatDuration:
    def test_handles_float(self):
        # gotcha #3: yt-dlp returns floats like 125.9
        assert format_duration(125.9) == "2:05"

    def test_handles_none(self):
        assert format_duration(None) == "0:00"

    def test_handles_int(self):
        assert format_duration(59) == "0:59"

    def test_handles_hours(self):
        assert format_duration(3725) == "1:02:05"

    def test_handles_zero(self):
        assert format_duration(0) == "0:00"

    def test_handles_negative(self):
        assert format_duration(-5) == "0:00"

    def test_handles_garbage(self):
        assert format_duration("abc") == "0:00"  # type: ignore[arg-type]


class TestFormatSize:
    def test_bytes(self):
        assert format_size(512) == "512 B"

    def test_megabytes(self):
        assert format_size(5 * 1024 * 1024) == "5.0 MB"

    def test_gigabytes(self):
        assert format_size(int(2.5 * 1024**3)) == "2.5 GB"

    def test_none(self):
        assert format_size(None) == "غير معروف"

    def test_zero(self):
        assert format_size(0) == "غير معروف"


class TestTruncateText:
    def test_short_untouched(self):
        assert truncate_text("hello", 10) == "hello"

    def test_long_truncated(self):
        out = truncate_text("a" * 100, 20)
        assert len(out) <= 20
        assert out.endswith("…")

    def test_empty(self):
        assert truncate_text("") == ""

    def test_500_char_title(self):
        # edge case from the design doc: very long titles
        out = truncate_text("ع" * 500, 60)
        assert len(out) <= 60


class TestSanitizeFilename:
    def test_removes_bad_chars(self):
        assert "/" not in sanitize_filename("a/b:c*d?e")
        assert ":" not in sanitize_filename("a/b:c*d?e")

    def test_keeps_arabic_and_emoji(self):
        out = sanitize_filename("فيديو جميل 🎬")
        assert "فيديو" in out

    def test_empty_falls_back(self):
        assert sanitize_filename("") == "video"

    def test_only_bad_chars_falls_back(self):
        assert sanitize_filename("///:::") == "video"

    def test_length_capped(self):
        assert len(sanitize_filename("x" * 500)) <= 80


class TestErrorMessage:
    def test_never_empty(self):
        # gotcha #1: some exceptions stringify to ""
        assert error_message(ValueError()) == "ValueError"

    def test_uses_message_when_present(self):
        assert error_message(ValueError("boom")) == "boom"

    def test_truncates_long_messages(self):
        assert len(error_message(ValueError("x" * 1000))) <= 200


class TestProgressBar:
    def test_zero_percent(self):
        assert build_progress_bar(0, length=10) == "░" * 10

    def test_hundred_percent(self):
        assert build_progress_bar(100, length=10) == "█" * 10

    def test_partial(self):
        bar = build_progress_bar(50, length=10)
        assert bar.count("█") == 5
        assert bar.count("░") == 5

    def test_clamps_out_of_range(self):
        assert build_progress_bar(150, length=10) == "█" * 10
        assert build_progress_bar(-10, length=10) == "░" * 10


class TestFormatSpeed:
    def test_normal(self):
        assert format_speed(5 * 1024 * 1024) == "5.0 MB/s"

    def test_none_or_zero(self):
        assert format_speed(None) == "-- MB/s"
        assert format_speed(0) == "-- MB/s"


class TestFormatEta:
    def test_normal(self):
        assert format_eta(154) == "02:34"

    def test_none(self):
        assert format_eta(None) == "--:--"

    def test_negative(self):
        assert format_eta(-5) == "--:--"


class TestFormatProgressPanel:
    def test_download_panel_contains_expected_labels(self):
        panel = format_progress_panel(
            phase="download",
            percent=67,
            speed_bytes_per_sec=12.8 * 1024 * 1024,
            done_bytes=int(1.6 * 1024**3),
            total_bytes=int(2.4 * 1024**3),
            eta_seconds=154,
        )
        assert "📥 <b>Download</b>" in panel
        assert "67%" in panel
        assert "12.8 MB/s" in panel
        assert "1.6 GB / 2.4 GB" in panel
        assert "02:34" in panel

    def test_upload_panel_uses_upload_labels(self):
        panel = format_progress_panel(
            phase="upload",
            percent=42,
            speed_bytes_per_sec=18 * 1024 * 1024,
            done_bytes=860 * 1024**2,
            total_bytes=2 * 1024**3,
            eta_seconds=72,
        )
        assert "📤 <b>Upload</b>" in panel
        assert "Upload Speed" in panel
        assert "Uploaded" in panel
        assert "ETA" in panel

    def test_title_is_escaped_for_html(self):
        panel = format_progress_panel(
            phase="download",
            percent=10,
            speed_bytes_per_sec=None,
            done_bytes=0,
            total_bytes=None,
            eta_seconds=None,
            title="Tom & Jerry <official>",
        )
        assert "Tom &amp; Jerry &lt;official&gt;" in panel
        assert "<official>" not in panel

    def test_unknown_total_bytes(self):
        panel = format_progress_panel(
            phase="download",
            percent=0,
            speed_bytes_per_sec=None,
            done_bytes=0,
            total_bytes=None,
            eta_seconds=None,
        )
        assert "؟" in panel
        assert "--:--" in panel


class TestDiskUsageMb:
    def test_returns_sane_triple(self, tmp_path):
        total, used, free = disk_usage_mb(tmp_path)
        assert total > 0
        assert used >= 0
        assert free >= 0
        assert used <= total


class TestSystemStats:
    def test_returns_expected_keys(self):
        stats = system_stats()
        assert set(stats) == {
            "cpu_percent",
            "memory_percent",
            "memory_used_mb",
            "memory_total_mb",
        }

    def test_values_in_sane_ranges(self):
        stats = system_stats()
        assert 0 <= stats["cpu_percent"] <= 100
        assert 0 <= stats["memory_percent"] <= 100
        assert 0 <= stats["memory_used_mb"] <= stats["memory_total_mb"]
