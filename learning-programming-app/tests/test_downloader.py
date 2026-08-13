import threading

import pytest

from bot.config import Config
from bot.downloader import (
    AUDIO_FORMAT_KEYS,
    DownloadCancelledError,
    YTDLPDownloader,
    _HAS_CURL_CFFI,
    available_qualities,
)


def make_downloader(**overrides) -> YTDLPDownloader:
    cfg = Config(telegram_bot_token="123456:dummy", **overrides)  # type: ignore[arg-type]
    return YTDLPDownloader(cfg)


def test_impersonate_is_a_proper_target_object_not_a_string():
    """Regression test for the bare AssertionError bug.

    yt-dlp's curl_cffi request handler asserts isinstance(self.impersonate,
    (ImpersonateTarget, NoneType)) on every request. A raw string like ""
    satisfies neither and crashes with an unmessaged AssertionError on the
    first real request — it must be an actual ImpersonateTarget instance.
    """
    opts = make_downloader()._base_opts()
    if _HAS_CURL_CFFI:
        from yt_dlp.networking.impersonate import ImpersonateTarget

        assert isinstance(opts["impersonate"], ImpersonateTarget)
        assert opts["extractor_args"]["generic"]["impersonate"] == ["chrome"]
    else:
        assert "impersonate" not in opts


def test_cancel_event_aborts_download_via_progress_hook(monkeypatch, tmp_path):
    """Setting the flag mid-download must raise DownloadCancelledError.

    This is what actually stops yt-dlp (raising from a progress hook is its
    documented abort mechanism) — unlike cancelling the wrapping asyncio
    task, which does not stop the thread this blocking call runs in.
    """
    import bot.downloader as downloader_module

    monkeypatch.setattr(downloader_module.shutil, "which", lambda name: None)
    cancel_event = threading.Event()
    cancel_event.set()  # already cancelled before the first progress tick

    class FakeYDL:
        def __init__(self, opts):
            self._hook = opts["progress_hooks"][0]

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download=True):
            self._hook({"status": "downloading", "downloaded_bytes": 0})
            return {}

    monkeypatch.setattr(downloader_module.yt_dlp, "YoutubeDL", FakeYDL)

    d = make_downloader()
    with pytest.raises(DownloadCancelledError):
        d.download("https://example.com/v", "best", tmp_path, cancel_event=cancel_event)


def test_cancel_event_reclassifies_aria2c_kill_as_cancelled(monkeypatch, tmp_path):
    """When aria2c is used, the hook-based cancel check above never fires
    mid-transfer (see _poll_aria2c_progress's docstring), so cancellation
    instead kills the aria2c process directly, which makes yt-dlp raise a
    generic DownloadError (e.g. "aria2c exited with code -9"). download()
    must recognize that the cancel flag was set and report a clean
    DownloadCancelledError instead of that ugly underlying message.
    """
    import bot.downloader as downloader_module

    monkeypatch.setattr(downloader_module.shutil, "which", lambda name: "/usr/bin/aria2c")
    monkeypatch.setattr(downloader_module.YTDLPDownloader, "_estimate_total_bytes", lambda self, url, fk: None)
    monkeypatch.setattr(downloader_module.YTDLPDownloader, "_poll_aria2c_progress", staticmethod(lambda *a, **k: None))
    cancel_event = threading.Event()
    cancel_event.set()

    class FakeYDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download=True):
            raise downloader_module.yt_dlp.utils.DownloadError("aria2c exited with code -9")

    monkeypatch.setattr(downloader_module.yt_dlp, "YoutubeDL", FakeYDL)

    d = make_downloader()
    with pytest.raises(DownloadCancelledError):
        d.download("https://example.com/v", "best", tmp_path, cancel_event=cancel_event)


def test_continuedl_enabled_for_resume():
    """A partial .part file should resume via HTTP Range, not restart."""
    opts = make_downloader()._base_opts()
    assert opts["continuedl"] is True


def test_aria2c_continue_flag_present(monkeypatch, tmp_path):
    import bot.downloader as downloader_module

    monkeypatch.setattr(downloader_module.shutil, "which", lambda name: "/usr/bin/aria2c")
    captured = {}

    class FakeYDL:
        def __init__(self, opts):
            captured.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download=True):
            (tmp_path / "video [id].mp4").write_bytes(b"x")
            return {}

    monkeypatch.setattr(downloader_module.yt_dlp, "YoutubeDL", FakeYDL)

    d = make_downloader()
    d.download("https://example.com/v", "best", tmp_path)

    assert "--continue=true" in captured["external_downloader_args"]["default"]


def test_youtube_player_client_fallback_present():
    opts = make_downloader()._base_opts()
    assert opts["extractor_args"]["youtube"]["player_client"] == ["android", "web", "ios"]


def test_aria2c_used_when_available(monkeypatch, tmp_path):
    import bot.downloader as downloader_module

    monkeypatch.setattr(downloader_module.shutil, "which", lambda name: "/usr/bin/aria2c")

    captured = {}

    class FakeYDL:
        def __init__(self, opts):
            captured.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download=True):
            (tmp_path / "video [id].mp4").write_bytes(b"x")
            return {}

    monkeypatch.setattr(downloader_module.yt_dlp, "YoutubeDL", FakeYDL)

    d = make_downloader()
    d.download("https://example.com/v", "best", tmp_path)

    assert captured["external_downloader"] == "aria2c"
    assert "--split=8" in captured["external_downloader_args"]["default"]


def test_aria2c_not_used_when_missing(monkeypatch, tmp_path):
    import bot.downloader as downloader_module

    monkeypatch.setattr(downloader_module.shutil, "which", lambda name: None)

    captured = {}

    class FakeYDL:
        def __init__(self, opts):
            captured.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download=True):
            (tmp_path / "video [id].mp4").write_bytes(b"x")
            return {}

    monkeypatch.setattr(downloader_module.yt_dlp, "YoutubeDL", FakeYDL)

    d = make_downloader()
    d.download("https://example.com/v", "best", tmp_path)

    assert "external_downloader" not in captured


def test_download_picks_up_thumbnail_file(monkeypatch, tmp_path):
    """writethumbnail=True should be requested for video, and the saved
    image file returned separately from the main media file, never
    mistaken for it (design doc gotcha: don't let a small preview image
    get picked as "the largest file")."""
    import bot.downloader as downloader_module

    monkeypatch.setattr(downloader_module.shutil, "which", lambda name: None)
    captured = {}

    class FakeYDL:
        def __init__(self, opts):
            captured.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download=True):
            (tmp_path / "video [id].mp4").write_bytes(b"x" * 100)
            (tmp_path / "video [id].webp").write_bytes(b"y")
            return {}

    monkeypatch.setattr(downloader_module.yt_dlp, "YoutubeDL", FakeYDL)

    d = make_downloader()
    result = d.download("https://example.com/v", "best", tmp_path)

    assert captured["writethumbnail"] is True
    assert result.file_path.suffix == ".mp4"
    assert result.thumbnail_path is not None
    assert result.thumbnail_path.suffix == ".webp"


def test_download_no_thumbnail_for_audio(monkeypatch, tmp_path):
    import bot.downloader as downloader_module

    monkeypatch.setattr(downloader_module.shutil, "which", lambda name: None)
    captured = {}

    class FakeYDL:
        def __init__(self, opts):
            captured.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download=True):
            (tmp_path / "audio [id].mp3").write_bytes(b"x")
            return {}

    monkeypatch.setattr(downloader_module.yt_dlp, "YoutubeDL", FakeYDL)

    d = make_downloader()
    result = d.download("https://example.com/v", "mp3", tmp_path)

    assert captured["writethumbnail"] is False
    assert result.thumbnail_path is None


@pytest.mark.parametrize(
    "format_key,expected_codec,expected_quality",
    [
        ("mp3", "mp3", "192"),
        ("m4a", "m4a", "192"),
        ("wav", "wav", "0"),
        ("flac", "flac", "0"),
    ],
)
def test_audio_format_selects_matching_codec(monkeypatch, tmp_path, format_key, expected_codec, expected_quality):
    import bot.downloader as downloader_module

    monkeypatch.setattr(downloader_module.shutil, "which", lambda name: None)
    captured = {}

    class FakeYDL:
        def __init__(self, opts):
            captured.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download=True):
            (tmp_path / f"audio [id].{format_key}").write_bytes(b"x")
            return {}

    monkeypatch.setattr(downloader_module.yt_dlp, "YoutubeDL", FakeYDL)

    d = make_downloader()
    d.download("https://example.com/v", format_key, tmp_path)

    pp = captured["postprocessors"][0]
    assert pp["key"] == "FFmpegExtractAudio"
    assert pp["preferredcodec"] == expected_codec
    assert pp["preferredquality"] == expected_quality
    assert "merge_output_format" not in captured


class TestEstimateTotalBytes:
    def test_picks_max_filesize_among_matching_formats(self, monkeypatch, tmp_path):
        import bot.downloader as downloader_module

        formats = [
            {"height": 1080, "filesize": 500_000_000},
            {"height": 720, "filesize": 200_000_000},
            {"height": 480, "filesize": 80_000_000},
        ]

        class FakeYDL:
            def __init__(self, opts):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def extract_info(self, url, download=False):
                return {"formats": formats}

        monkeypatch.setattr(downloader_module.yt_dlp, "YoutubeDL", FakeYDL)
        d = make_downloader()
        assert d._estimate_total_bytes("https://x", "720") == 200_000_000

    def test_returns_none_when_extraction_fails(self, monkeypatch):
        import bot.downloader as downloader_module

        class FakeYDL:
            def __init__(self, opts):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def extract_info(self, url, download=False):
                raise RuntimeError("network down")

        monkeypatch.setattr(downloader_module.yt_dlp, "YoutubeDL", FakeYDL)
        d = make_downloader()
        assert d._estimate_total_bytes("https://x", "best") is None


class TestPollAria2cProgress:
    def test_reports_disk_usage_from_growing_file(self, tmp_path):
        """Regression test: yt-dlp's external-downloader framework
        (aria2c/curl/wget/...) only calls progress_hooks once, with
        status='finished', after the subprocess exits — never with
        intermediate progress (verified directly in yt_dlp's own
        downloader/external.py). Without this poller, the download-phase
        progress bar never moves while aria2c is downloading.
        """
        import bot.downloader as downloader_module

        (tmp_path / "video.mp4.part").write_bytes(b"x" * 8192)

        calls = []
        stop_event = threading.Event()

        def cb(progress):
            calls.append(progress)
            stop_event.set()

        downloader_module.YTDLPDownloader._poll_aria2c_progress(
            tmp_path, 1_000_000, cb, None, stop_event, poll_interval=0.001
        )

        assert len(calls) == 1
        assert calls[0].status == "downloading"
        assert calls[0].downloaded_bytes > 0
        assert calls[0].total_bytes == 1_000_000

    def test_ignores_aria2_and_thumbnail_files(self, tmp_path):
        import bot.downloader as downloader_module

        (tmp_path / "video.mp4.part.aria2").write_bytes(b"x" * 999_999)
        (tmp_path / "thumb.jpg").write_bytes(b"x" * 999_999)
        (tmp_path / "video.mp4.part").write_bytes(b"real content")

        calls = []
        stop_event = threading.Event()

        def cb(progress):
            calls.append(progress)
            stop_event.set()

        downloader_module.YTDLPDownloader._poll_aria2c_progress(
            tmp_path, None, cb, None, stop_event, poll_interval=0.001
        )

        assert len(calls) == 1

    def test_cancel_event_stops_polling_and_kills_process(self, tmp_path, monkeypatch):
        import bot.downloader as downloader_module

        killed = {}
        monkeypatch.setattr(
            downloader_module.YTDLPDownloader,
            "_kill_aria2c_for",
            staticmethod(lambda out_dir: killed.setdefault("dir", out_dir)),
        )

        cancel_event = threading.Event()
        cancel_event.set()
        stop_event = threading.Event()

        downloader_module.YTDLPDownloader._poll_aria2c_progress(
            tmp_path, None, lambda p: None, cancel_event, stop_event, poll_interval=0.001
        )

        assert killed["dir"] == tmp_path


class TestKillAria2cFor:
    def test_kills_matching_process_by_dir_argument(self, monkeypatch, tmp_path):
        import bot.downloader as downloader_module

        class FakeProc:
            def __init__(self, name, cmdline):
                self.info = {"name": name, "cmdline": cmdline}
                self.killed = False

            def kill(self):
                self.killed = True

        other = FakeProc("aria2c", ["aria2c", "--dir", "/some/other/job"])
        target = FakeProc("aria2c", ["aria2c", "--dir", str(tmp_path) + "/"])
        not_aria2c = FakeProc("python", ["python", "main.py"])

        monkeypatch.setattr(
            downloader_module.psutil, "process_iter", lambda attrs: [other, target, not_aria2c]
        )

        downloader_module.YTDLPDownloader._kill_aria2c_for(tmp_path)

        assert other.killed is False
        assert target.killed is True
        assert not_aria2c.killed is False


class TestAvailableQualities:
    def test_no_formats_only_best_and_audio(self):
        assert available_qualities([]) == ["best", *AUDIO_FORMAT_KEYS]

    def test_single_fixed_resolution_hides_all_tiers(self):
        """Common on Twitter/TikTok/Instagram: one video-only format, no ladder."""
        formats = [{"height": 720, "vcodec": "h264"}]
        assert available_qualities(formats) == ["best", *AUDIO_FORMAT_KEYS]

    def test_youtube_style_multi_resolution_ladder(self):
        formats = [
            {"height": 2160, "vcodec": "vp9"},
            {"height": 1080, "vcodec": "avc1"},
            {"height": 720, "vcodec": "avc1"},
            {"height": 480, "vcodec": "avc1"},
            {"height": 360, "vcodec": "avc1"},
            {"height": None, "vcodec": "none", "acodec": "mp4a"},  # audio-only format
        ]
        assert available_qualities(formats) == [
            "best", "1080", "720", "480", "360", *AUDIO_FORMAT_KEYS,
        ]

    def test_does_not_offer_tier_above_max_height(self):
        formats = [{"height": 480, "vcodec": "h264"}, {"height": 360, "vcodec": "h264"}]
        qualities = available_qualities(formats)
        assert "2160" not in qualities
        assert "1440" not in qualities
        assert "1080" not in qualities
        assert "720" not in qualities
        assert qualities == ["best", "360", *AUDIO_FORMAT_KEYS]

    def test_audio_only_formats_ignored_for_resolution_tiers(self):
        formats = [{"height": None, "vcodec": "none", "acodec": "mp4a"}]
        assert available_qualities(formats) == ["best", *AUDIO_FORMAT_KEYS]

    def test_all_four_audio_formats_offered(self):
        assert available_qualities([]) == ["best", "mp3", "m4a", "wav", "flac"]
