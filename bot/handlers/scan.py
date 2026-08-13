"""/scan: crawl an index/listing page for sub-page links, probe each one
with yt-dlp, and let the user confirm a batch download of whichever links
actually resolve to a real video.

No site-specific scraping logic — bot/pagescan.py just finds same-domain
links, and yt-dlp's own extractors decide which of them are real videos by
trying each one. That probe is the filter.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from ..config import Config
from ..downloader import YTDLPDownloader
from ..jobs import StatusReporter, run_download_job
from ..manager import DownloadManager
from ..pagescan import PageScanError, extract_page_links
from ..uploader import UploadManager
from ..utils import error_message, truncate_text
from ..validators import URLValidator

logger = logging.getLogger(__name__)


async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    if not context.args:
        await update.message.reply_text(
            "استخدم: /scan <لينك الصفحة>\nهفحص الصفحة وأدوّر على كل الفيديوهات جواها."
        )
        return

    url = context.args[0]
    ok, err = URLValidator.validate(url)
    if not ok:
        await update.message.reply_text(f"⛔ {err}")
        return

    config: Config = context.bot_data["config"]
    downloader: YTDLPDownloader = context.bot_data["downloader"]
    status = await update.message.reply_text("🔍 بفحص الصفحة وبدوّر على فيديوهات جواها...")

    try:
        links = await extract_page_links(url, config.extract_timeout_seconds)
    except PageScanError as exc:
        await status.edit_text(f"❌ {exc}")
        return
    except Exception as exc:
        logger.exception("Page scan failed for %s", url)
        await status.edit_text(f"❌ حصل خطأ أثناء فحص الصفحة: {error_message(exc)}")
        return

    if not links:
        await status.edit_text("❌ مالقتش أي لينكات فرعية في الصفحة دي.")
        return

    found: list[dict] = []
    checked = 0
    for candidate in links:
        if checked >= config.max_scan_probe_links or len(found) >= config.max_scan_items:
            break
        checked += 1
        try:
            info = await asyncio.wait_for(
                asyncio.to_thread(downloader.extract_info, candidate),
                timeout=config.scan_probe_timeout_seconds,
            )
        except Exception:
            continue  # most links on a page aren't videos — skip silently
        if info.is_playlist:
            continue
        found.append({"url": candidate, "title": info.title})

    if not found:
        await status.edit_text("❌ مالقتش أي فيديو قابل للتحميل في اللينكات الفرعية.")
        return

    key = uuid.uuid4().hex[:8]
    scans: dict = context.user_data.setdefault("pending_scans", {})
    scans[key] = found

    lines = "\n".join(
        f"{i}. {truncate_text(item['title'], 45)}" for i, item in enumerate(found, start=1)
    )
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(f"⬇️ حمّل الكل ({len(found)}) بأفضل جودة", callback_data=f"scanrun:{key}")],
            [InlineKeyboardButton("❌ إلغاء", callback_data=f"scandrop:{key}")],
        ]
    )
    await status.edit_text(
        f"🎬 لقيت {len(found)} فيديو في الصفحة:\n\n{lines}\n\nتحمّلهم كلهم؟",
        reply_markup=keyboard,
    )


async def handle_scan_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handles scanrun:/scandrop: callback actions. Returns True if it
    consumed the update (caller must not process it any further)."""
    query = update.callback_query
    if query is None or query.data is None:
        return False
    action, _, key = query.data.partition(":")
    if action not in ("scanrun", "scandrop"):
        return False

    scans: dict = context.user_data.get("pending_scans", {})
    items = scans.pop(key, None)
    if query.message is None:
        return True
    if items is None:
        await query.message.edit_text("القائمة دي قديمة أو خلصت.")
        return True

    if action == "scandrop":
        await query.message.edit_text("👌 اتلغى.")
        return True

    config: Config = context.bot_data["config"]
    manager: DownloadManager = context.bot_data["manager"]
    downloader: YTDLPDownloader = context.bot_data["downloader"]
    uploader: UploadManager = context.bot_data["uploader"]
    chat_id = query.message.chat_id
    user_id = update.effective_user.id if update.effective_user else 0

    await query.message.edit_text(f"📃 بدء تحميل {len(items)} فيديو من الصفحة...")

    async def run() -> None:
        ok_count = 0
        for i, item in enumerate(items, start=1):
            job_id = uuid.uuid4().hex[:8]
            msg = await context.bot.send_message(
                chat_id, f"🕐 ({i}/{len(items)}) {truncate_text(item['title'], 50)}"
            )
            job_status = StatusReporter(msg, config.edit_throttle_seconds)
            try:
                ok = await run_download_job(
                    config=config,
                    manager=manager,
                    downloader=downloader,
                    uploader=uploader,
                    status=job_status,
                    chat_id=chat_id,
                    user_id=user_id,
                    url=item["url"],
                    format_key="best",
                    title_hint=item["title"],
                    job_id=job_id,
                )
                if ok:
                    ok_count += 1
            except asyncio.CancelledError:
                await context.bot.send_message(chat_id, "🚫 اتلغى باقي فيديوهات الصفحة.")
                return
            except Exception:
                # one failed entry must never stop the rest
                logger.exception("Scan batch entry %d failed", i)
        await context.bot.send_message(chat_id, f"📃 خلص — نجح {ok_count} من {len(items)}.")

    context.application.create_task(run())
    return True
