from unittest.mock import MagicMock

import pytest

from bot.torrentdl import TorrentDownloadError, download_torrent


class _FakeProc:
    def __init__(self, returncode, output=b"", poll_sequence=None):
        self.returncode = returncode
        self.stdout = MagicMock()
        self.stdout.read.return_value = output
        self._poll_sequence = poll_sequence if poll_sequence is not None else [None, returncode]
        self._i = 0
        self.killed = False

    def poll(self):
        val = self._poll_sequence[min(self._i, len(self._poll_sequence) - 1)]
        self._i += 1
        return val

    def kill(self):
        self.killed = True

    def wait(self):
        return self.returncode


def test_raises_when_aria2c_missing(monkeypatch, tmp_path):
    import bot.torrentdl as torrentdl_module

    monkeypatch.setattr(torrentdl_module.shutil, "which", lambda name: None)

    with pytest.raises(TorrentDownloadError, match="aria2c مش متثبت"):
        download_torrent(tmp_path / "x.torrent", tmp_path / "out")


def test_captures_stdout_in_error_message_on_failure(monkeypatch, tmp_path):
    """Regression test: aria2c writes its real diagnostic output to stdout,
    not stderr — capturing stderr alone (the previous behavior) silently
    discarded the actual reason for a failure, leaving a bare, useless
    error message for both the user and for debugging.
    """
    import bot.torrentdl as torrentdl_module

    monkeypatch.setattr(torrentdl_module.shutil, "which", lambda name: "/usr/bin/aria2c")
    fake_proc = _FakeProc(
        returncode=1,
        output=b"[ERROR] Could not connect to any peer. It means the download "
        b"file is not complete.\naria2 will resume download if the transfer is "
        b"restarted. If there are any files in the current directory, ...",
    )
    monkeypatch.setattr(torrentdl_module.subprocess, "Popen", lambda *a, **k: fake_proc)

    out_dir = tmp_path / "out"
    with pytest.raises(TorrentDownloadError, match="Could not connect to any peer"):
        download_torrent(tmp_path / "x.torrent", out_dir)


def test_passes_stdout_pipe_and_stderr_redirected_to_stdout(monkeypatch, tmp_path):
    import bot.torrentdl as torrentdl_module

    monkeypatch.setattr(torrentdl_module.shutil, "which", lambda name: "/usr/bin/aria2c")
    fake_proc = _FakeProc(returncode=1, output=b"some error")
    captured_kwargs = {}

    def fake_popen(cmd, **kwargs):
        captured_kwargs.update(kwargs)
        return fake_proc

    monkeypatch.setattr(torrentdl_module.subprocess, "Popen", fake_popen)

    with pytest.raises(TorrentDownloadError):
        download_torrent(tmp_path / "x.torrent", tmp_path / "out")

    assert captured_kwargs["stdout"] == torrentdl_module.subprocess.PIPE
    assert captured_kwargs["stderr"] == torrentdl_module.subprocess.STDOUT


def test_success_returns_largest_file(monkeypatch, tmp_path):
    import bot.torrentdl as torrentdl_module

    monkeypatch.setattr(torrentdl_module.shutil, "which", lambda name: "/usr/bin/aria2c")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "small.txt").write_bytes(b"x" * 10)
    (out_dir / "big.iso").write_bytes(b"x" * 1000)

    fake_proc = _FakeProc(returncode=0)
    monkeypatch.setattr(torrentdl_module.subprocess, "Popen", lambda *a, **k: fake_proc)

    result = download_torrent(tmp_path / "x.torrent", out_dir)

    assert result.name == "big.iso"


def test_no_output_file_raises(monkeypatch, tmp_path):
    import bot.torrentdl as torrentdl_module

    monkeypatch.setattr(torrentdl_module.shutil, "which", lambda name: "/usr/bin/aria2c")
    out_dir = tmp_path / "out"
    fake_proc = _FakeProc(returncode=0)
    monkeypatch.setattr(torrentdl_module.subprocess, "Popen", lambda *a, **k: fake_proc)

    with pytest.raises(TorrentDownloadError, match="من غير ما ينتج ملف"):
        download_torrent(tmp_path / "x.torrent", out_dir)


def test_cancel_event_kills_process(monkeypatch, tmp_path):
    import bot.torrentdl as torrentdl_module
    import threading

    monkeypatch.setattr(torrentdl_module.shutil, "which", lambda name: "/usr/bin/aria2c")
    fake_proc = _FakeProc(returncode=0, poll_sequence=[None, None, None])
    monkeypatch.setattr(torrentdl_module.subprocess, "Popen", lambda *a, **k: fake_proc)

    cancel_event = threading.Event()
    cancel_event.set()

    with pytest.raises(TorrentDownloadError, match="اتلغى"):
        download_torrent(tmp_path / "x.torrent", tmp_path / "out", cancel_event=cancel_event)

    assert fake_proc.killed is True
