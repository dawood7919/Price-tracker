"""Render a webpage to PDF using headless Chromium via subprocess.

Deliberately not Playwright: its bundled Chromium download only ships
glibc binaries, which would hit the exact same "cannot execute: required
file not found" mismatch that the telegram-bot-api binary did before this
project's base image was matched to Alpine (musl). Alpine's own `chromium`
package is musl-native and needs no such matching.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

_CHROMIUM_CANDIDATES = ("chromium-browser", "chromium", "chromium-browser-stable")


class PdfConvertError(RuntimeError):
    pass


def find_chromium() -> str | None:
    for name in _CHROMIUM_CANDIDATES:
        path = shutil.which(name)
        if path:
            return path
    return None


async def render_url_to_pdf(url: str, out_path: Path, timeout_seconds: float) -> None:
    """Render *url* to a PDF at *out_path*.

    --virtual-time-budget gives JS-heavy pages a window to run before the
    page is considered "loaded enough" to print — headless Chromium does
    execute JavaScript, unlike a plain HTML fetch, though very slow-loading
    single-page apps may still be incomplete by the time the budget expires.
    """
    chromium = find_chromium()
    if chromium is None:
        raise PdfConvertError("Chromium مش متثبت على السيرفر — مقدرش أحوّل الصفحة لـ PDF.")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        chromium,
        "--headless",
        "--disable-gpu",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        f"--print-to-pdf={out_path}",
        "--print-to-pdf-no-header",
        "--virtual-time-budget=15000",
        url,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise PdfConvertError("الصفحة خدت وقت أطول من المسموح.") from exc

    if proc.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
        detail = stderr.decode(errors="ignore").strip()[:200] if stderr else ""
        raise PdfConvertError(f"فشل تحويل الصفحة لـ PDF{': ' + detail if detail else ''}.")
