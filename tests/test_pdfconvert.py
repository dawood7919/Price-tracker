import asyncio

import pytest

from bot.pdfconvert import PdfConvertError, find_chromium, render_url_to_pdf


def test_find_chromium_returns_none_when_missing(monkeypatch):
    import bot.pdfconvert as pdfconvert_module

    monkeypatch.setattr(pdfconvert_module.shutil, "which", lambda name: None)
    assert find_chromium() is None


def test_find_chromium_returns_first_match(monkeypatch):
    import bot.pdfconvert as pdfconvert_module

    monkeypatch.setattr(
        pdfconvert_module.shutil, "which", lambda name: "/usr/bin/chromium-browser" if name == "chromium-browser" else None
    )
    assert find_chromium() == "/usr/bin/chromium-browser"


@pytest.mark.asyncio
async def test_raises_when_chromium_missing(monkeypatch, tmp_path):
    import bot.pdfconvert as pdfconvert_module

    monkeypatch.setattr(pdfconvert_module, "find_chromium", lambda: None)
    with pytest.raises(PdfConvertError):
        await render_url_to_pdf("https://example.com", tmp_path / "out.pdf", timeout_seconds=5)


class _FakeProc:
    def __init__(self, returncode=0, stderr=b"", hang=False):
        self.returncode = returncode
        self._stderr = stderr
        self._hang = hang
        self.killed = False

    async def communicate(self):
        if self._hang:
            await asyncio.sleep(10)
        return (b"", self._stderr)

    def kill(self):
        self.killed = True

    async def wait(self):
        return self.returncode


@pytest.mark.asyncio
async def test_writes_pdf_on_success(monkeypatch, tmp_path):
    import bot.pdfconvert as pdfconvert_module

    monkeypatch.setattr(pdfconvert_module, "find_chromium", lambda: "/usr/bin/chromium-browser")
    out_path = tmp_path / "out.pdf"

    async def fake_exec(*cmd, **kwargs):
        out_path.write_bytes(b"%PDF-1.4 fake content")
        return _FakeProc(returncode=0)

    monkeypatch.setattr(pdfconvert_module.asyncio, "create_subprocess_exec", fake_exec)

    await render_url_to_pdf("https://example.com", out_path, timeout_seconds=5)

    assert out_path.read_bytes().startswith(b"%PDF")


@pytest.mark.asyncio
async def test_raises_on_nonzero_exit(monkeypatch, tmp_path):
    import bot.pdfconvert as pdfconvert_module

    monkeypatch.setattr(pdfconvert_module, "find_chromium", lambda: "/usr/bin/chromium-browser")

    async def fake_exec(*cmd, **kwargs):
        return _FakeProc(returncode=1, stderr=b"some chromium error")

    monkeypatch.setattr(pdfconvert_module.asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(PdfConvertError, match="some chromium error"):
        await render_url_to_pdf("https://example.com", tmp_path / "out.pdf", timeout_seconds=5)


@pytest.mark.asyncio
async def test_raises_on_empty_output_file(monkeypatch, tmp_path):
    import bot.pdfconvert as pdfconvert_module

    monkeypatch.setattr(pdfconvert_module, "find_chromium", lambda: "/usr/bin/chromium-browser")
    out_path = tmp_path / "out.pdf"
    out_path.write_bytes(b"")  # exists but empty

    async def fake_exec(*cmd, **kwargs):
        return _FakeProc(returncode=0)

    monkeypatch.setattr(pdfconvert_module.asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(PdfConvertError):
        await render_url_to_pdf("https://example.com", out_path, timeout_seconds=5)


@pytest.mark.asyncio
async def test_kills_process_on_timeout(monkeypatch, tmp_path):
    import bot.pdfconvert as pdfconvert_module

    monkeypatch.setattr(pdfconvert_module, "find_chromium", lambda: "/usr/bin/chromium-browser")
    proc = _FakeProc(hang=True)

    async def fake_exec(*cmd, **kwargs):
        return proc

    monkeypatch.setattr(pdfconvert_module.asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(PdfConvertError):
        await render_url_to_pdf("https://example.com", tmp_path / "out.pdf", timeout_seconds=0.05)

    assert proc.killed is True
