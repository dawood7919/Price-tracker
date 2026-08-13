import asyncio
import importlib

import pytest

from bot.config import Config
from bot.manager import DownloadManager, JobState


def make_config(**overrides) -> Config:
    defaults = dict(
        telegram_bot_token="123456:dummy-token",
        max_concurrent_downloads=2,
    )
    defaults.update(overrides)
    return Config(**defaults)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_same_user_can_run_unlimited_parallel_downloads():
    """No per-user cap anymore — one user can hold many slots at once."""
    manager = DownloadManager(make_config(max_concurrent_downloads=5))
    for _ in range(5):
        await manager.acquire_slot(1)
    assert manager.active_for_user(1) == 5
    for _ in range(5):
        await manager.release_slot(1)
    assert manager.active_for_user(1) == 0


@pytest.mark.asyncio
async def test_global_cap_still_throttles_all_users_combined():
    """The only remaining limit is the global semaphore, shared across users."""
    manager = DownloadManager(make_config(max_concurrent_downloads=2))
    await manager.acquire_slot(1)
    await manager.acquire_slot(2)

    third = asyncio.ensure_future(manager.acquire_slot(3))
    await asyncio.sleep(0.05)
    assert not third.done()  # blocked until a slot frees up

    await manager.release_slot(1)
    await asyncio.wait_for(third, timeout=1.0)
    assert third.done()

    await manager.release_slot(2)
    await manager.release_slot(3)


@pytest.mark.asyncio
async def test_users_are_independent():
    manager = DownloadManager(make_config())
    await manager.acquire_slot(1)
    await manager.acquire_slot(2)  # a different user is not blocked
    await manager.release_slot(1)
    await manager.release_slot(2)


@pytest.mark.asyncio
async def test_start_job_tracks_state_and_returns_cancel_flag():
    manager = DownloadManager(make_config())
    task = asyncio.ensure_future(asyncio.sleep(10))
    event = manager.start_job("job1", task)

    assert manager.get_state("job1") == JobState.WAITING
    assert not event.is_set()

    manager.set_state("job1", JobState.DOWNLOADING)
    assert manager.get_state("job1") == JobState.DOWNLOADING
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_cancel_sets_flag_and_cancels_task():
    """The flag is what actually stops the worker thread; task.cancel() alone
    only stops the asyncio side (verified separately: a to_thread-wrapped
    blocking call keeps running after its awaiting task is cancelled)."""
    manager = DownloadManager(make_config())
    task = asyncio.ensure_future(asyncio.sleep(10))
    event = manager.start_job("job1", task)

    assert manager.cancel("job1") is True
    assert event.is_set()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_cancel_unknown_job_returns_false():
    manager = DownloadManager(make_config())
    assert manager.cancel("does-not-exist") is False


@pytest.mark.asyncio
async def test_cancel_all_cancels_every_active_job():
    manager = DownloadManager(make_config(max_concurrent_downloads=5))
    tasks = [asyncio.ensure_future(asyncio.sleep(10)) for _ in range(3)]
    for i, task in enumerate(tasks):
        manager.start_job(f"job{i}", task)

    cancelled = manager.cancel_all()

    assert cancelled == 3
    for task in tasks:
        with pytest.raises(asyncio.CancelledError):
            await task


def test_cancel_all_with_no_jobs_returns_zero():
    manager = DownloadManager(make_config())
    assert manager.cancel_all() == 0


@pytest.mark.asyncio
async def test_job_forgotten_after_task_completes():
    manager = DownloadManager(make_config())

    async def noop():
        return None

    task = asyncio.ensure_future(noop())
    manager.start_job("job1", task)
    await task
    await asyncio.sleep(0)  # let the done_callback run
    assert manager.active_jobs() == 0
    assert manager.cancel("job1") is False


@pytest.mark.asyncio
async def test_active_by_state_breaks_down_current_jobs_only():
    manager = DownloadManager(make_config())

    task1 = asyncio.ensure_future(asyncio.sleep(10))
    manager.start_job("job1", task1)
    manager.set_state("job1", JobState.DOWNLOADING)

    task2 = asyncio.ensure_future(asyncio.sleep(10))
    manager.start_job("job2", task2)
    manager.set_state("job2", JobState.UPLOADING)

    task3 = asyncio.ensure_future(asyncio.sleep(10))
    manager.start_job("job3", task3)  # left at the default WAITING state

    assert manager.active_by_state() == {
        JobState.DOWNLOADING: 1,
        JobState.UPLOADING: 1,
        JobState.WAITING: 1,
    }

    task1.cancel()
    task2.cancel()
    task3.cancel()
    for t in (task1, task2, task3):
        with pytest.raises(asyncio.CancelledError):
            await t
    await asyncio.sleep(0)  # let done_callbacks run
    assert manager.active_by_state() == {}


class TestConfig:
    def test_cloud_limit_default(self):
        cfg = make_config()
        assert cfg.upload_limit_bytes == 49 * 1024 * 1024

    def test_local_api_raises_limit(self):
        cfg = make_config(telegram_base_url="http://localhost:8081")
        assert cfg.upload_limit_bytes == 2000 * 1024 * 1024

    def test_explicit_override_wins(self):
        cfg = make_config(safe_upload_limit=10_000_000)
        assert cfg.upload_limit_bytes == 10_000_000

    def test_empty_token_rejected(self):
        with pytest.raises(Exception):
            make_config(telegram_bot_token="   ")

    def test_webhook_url_fallback_to_render(self):
        cfg = make_config(render_external_url="https://x.onrender.com/")
        assert cfg.effective_webhook_url == "https://x.onrender.com"

    def test_webhook_url_fallback_to_railway(self):
        cfg = make_config(railway_public_domain="myapp.up.railway.app")
        assert cfg.effective_webhook_url == "https://myapp.up.railway.app"

    def test_webhook_url_explicit_wins_over_railway(self):
        cfg = make_config(
            webhook_url="https://custom.example.com",
            railway_public_domain="myapp.up.railway.app",
        )
        assert cfg.effective_webhook_url == "https://custom.example.com"

    def test_api_id_and_hash_unset_by_default(self):
        cfg = make_config()
        assert cfg.telegram_api_id is None
        assert cfg.telegram_api_hash is None

    def test_api_id_and_hash_read_from_env(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_API_ID", "12345")
        monkeypatch.setenv("TELEGRAM_API_HASH", "abcdef")
        cfg = make_config()
        assert cfg.telegram_api_id == "12345"
        assert cfg.telegram_api_hash == "abcdef"


class TestWriteCookiesFile:
    def test_writes_content_and_sets_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:dummy-token")
        import main

        cfg = make_config(download_dir=tmp_path, ytdlp_cookies_content="# Netscape HTTP Cookie File\nfoo\tbar\n")
        main._write_cookies_file(cfg)

        assert cfg.ytdlp_cookies_file == str(tmp_path / "cookies.txt")
        assert (tmp_path / "cookies.txt").read_text() == "# Netscape HTTP Cookie File\nfoo\tbar\n"

    def test_noop_when_no_content(self, tmp_path):
        import main

        cfg = make_config(download_dir=tmp_path)
        main._write_cookies_file(cfg)

        assert cfg.ytdlp_cookies_file is None
        assert not (tmp_path / "cookies.txt").exists()

    def test_does_not_override_explicit_cookies_file(self, tmp_path):
        import main

        cfg = make_config(
            download_dir=tmp_path,
            ytdlp_cookies_content="ignored",
            ytdlp_cookies_file="/some/other/path.txt",
        )
        main._write_cookies_file(cfg)

        assert cfg.ytdlp_cookies_file == "/some/other/path.txt"
        assert not (tmp_path / "cookies.txt").exists()


def test_smoke_full_import(monkeypatch):
    """Design doc §6 level 3: `import main` must succeed with a dummy token.

    This is the test that would have caught the truncate_text import bug.
    """
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:dummy-token")
    import main  # noqa: F401

    importlib.reload(main)
