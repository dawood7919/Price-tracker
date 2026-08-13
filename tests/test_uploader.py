import pytest
from telegram import InputFile

from bot.config import Config
from bot.uploader import UploadManager, _ProgressFile, _SpeedTracker


def make_config(**overrides) -> Config:
    defaults = dict(telegram_bot_token="123456:dummy")
    defaults.update(overrides)
    return Config(**defaults)  # type: ignore[arg-type]


class TestProgressFile:
    def test_tracks_bytes_read(self, tmp_path):
        path = tmp_path / "f.bin"
        path.write_bytes(b"x" * 100)
        seen = []
        with path.open("rb") as fh:
            wrapped = _ProgressFile(fh, total_size=100, on_progress=lambda sent, total: seen.append((sent, total)))
            while wrapped.read(30):
                pass
        assert seen == [(30, 100), (60, 100), (90, 100), (100, 100)]

    def test_does_not_eagerly_read_whole_file(self, tmp_path):
        """The whole point: InputFile(read_file_handle=False) must not buffer everything."""
        path = tmp_path / "f.bin"
        path.write_bytes(b"x" * 100)
        with path.open("rb") as fh:
            wrapped = _ProgressFile(fh, total_size=100, on_progress=None)
            input_file = InputFile(wrapped, filename="f.bin", read_file_handle=False)
            # input_file_content must be our streaming wrapper, not raw bytes —
            # if it were bytes, PTB would have already read the whole file.
            assert input_file.input_file_content is wrapped
            assert not isinstance(input_file.input_file_content, bytes)


class TestSpeedTracker:
    def test_percent(self):
        tracker = _SpeedTracker()
        tracker.update(50, 200)
        assert tracker.percent == 25.0

    def test_percent_zero_total(self):
        tracker = _SpeedTracker()
        tracker.update(0, 0)
        assert tracker.percent == 0.0

    def test_eta_none_when_no_speed_yet(self):
        tracker = _SpeedTracker()
        tracker.update(0, 100)
        assert tracker.eta is None


class TestUploadManagerRouting:
    @pytest.mark.asyncio
    async def test_small_file_sent_without_splitting(self, tmp_path, monkeypatch):
        path = tmp_path / "video.mp4"
        path.write_bytes(b"x" * 10)

        calls = []

        class FakeBot:
            async def send_video(self, **kwargs):
                calls.append(("video", kwargs))
                return object()

        cfg = make_config(safe_upload_limit=1000)
        manager = UploadManager(cfg, FakeBot())

        async def notify(_text):
            pass

        await manager.upload(chat_id=1, file_path=path, caption="hi", notify=notify)

        assert len(calls) == 1
        assert calls[0][0] == "video"
        assert isinstance(calls[0][1]["video"], InputFile)

    @pytest.mark.asyncio
    async def test_upload_call_uses_long_timeout_override(self, tmp_path):
        """Regression test: with a Local Bot API Server, our request to it
        doesn't get a response until the *real* (possibly slow) internet
        upload finishes relaying — the general 60s read_timeout is nowhere
        near enough, so upload calls must override it per-call instead of
        raising the global default (which would also make quick status
        edits and health checks tolerate an hour of silence).
        """
        path = tmp_path / "video.mp4"
        path.write_bytes(b"x" * 10)

        calls = []

        class FakeBot:
            async def send_video(self, **kwargs):
                calls.append(kwargs)
                return object()

        cfg = make_config(safe_upload_limit=1000, upload_call_timeout_seconds=3600)
        manager = UploadManager(cfg, FakeBot())

        async def notify(_text):
            pass

        await manager.upload(chat_id=1, file_path=path, caption="hi", notify=notify)

        assert calls[0]["read_timeout"] == 3600
        assert calls[0]["write_timeout"] == 3600
