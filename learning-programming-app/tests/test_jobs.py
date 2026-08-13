from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.config import Config
from bot.jobs import StatusReporter, run_download_job
from bot.manager import DownloadManager


def make_config(**overrides) -> Config:
    defaults = dict(telegram_bot_token="123456:dummy", max_concurrent_downloads=1)
    defaults.update(overrides)
    return Config(**defaults)  # type: ignore[arg-type]


def make_status() -> StatusReporter:
    message = MagicMock()
    message.edit_text = AsyncMock()
    return StatusReporter(message, throttle_seconds=0)


@pytest.mark.asyncio
async def test_slot_released_even_if_start_job_raises(tmp_path, monkeypatch):
    """Regression test: acquire_slot()/start_job() used to run *outside*
    the try/finally. If anything between them raised, the semaphore slot
    was never released — leaking one slot per failure until the global
    cap was exhausted and every future download hung forever waiting on
    acquire_slot(). manager.start_job() itself is called from inside the
    try/finally now, so any exception there must still release the slot.
    """
    cfg = make_config(download_dir=tmp_path)
    manager = DownloadManager(cfg)

    def boom(job_id, task):
        raise RuntimeError("simulated failure in start_job")

    monkeypatch.setattr(manager, "start_job", boom)

    downloader = MagicMock()
    uploader = MagicMock()
    status = make_status()

    # The exception is now caught by run_download_job's own handler (it's
    # inside the try/finally), reported to the user, and swallowed here —
    # it must NOT propagate out to the caller (the Application-level task).
    ok = await run_download_job(
        config=cfg,
        manager=manager,
        downloader=downloader,
        uploader=uploader,
        status=status,
        chat_id=1,
        user_id=1,
        url="https://x",
        format_key="best",
        title_hint="t",
        job_id="job1",
    )
    assert ok is False

    # the slot must be free again despite the exception, or the very next
    # acquire_slot() call for anyone would hang forever
    assert manager._global_sem._value == cfg.max_concurrent_downloads


@pytest.mark.asyncio
async def test_happy_path_returns_true(tmp_path):
    from bot.downloader import DownloadResult

    cfg = make_config(download_dir=tmp_path)
    manager = DownloadManager(cfg)

    fake_file = tmp_path / "fake.mp4"
    fake_file.write_bytes(b"x" * 10)

    downloader = MagicMock()
    downloader.download = MagicMock(
        return_value=DownloadResult(file_path=fake_file, thumbnail_path=None)
    )
    uploader = MagicMock()
    uploader.upload = AsyncMock()
    status = make_status()

    ok = await run_download_job(
        config=cfg,
        manager=manager,
        downloader=downloader,
        uploader=uploader,
        status=status,
        chat_id=1,
        user_id=1,
        url="https://x",
        format_key="best",
        title_hint="t",
        job_id="job1",
    )

    assert ok is True
    assert manager._global_sem._value == cfg.max_concurrent_downloads
