from __future__ import annotations

import time

import yt_dlp
from telegram import Update
from telegram.ext import ContextTypes

from ..config import Config
from ..manager import DownloadManager, JobState
from ..settings_store import get_active_torrent_sources, site_label, torrent_site_label
from ..terms import is_terms_accepted, send_terms
from ..utils import disk_usage_mb, format_duration, format_size, system_stats
from ..version import BOT_VERSION

START_TEXT = (
    "أهلًا 👋\n"
    "<code>v{version}</code>\n\n"
    "ابعت لينك فيديو وهنزّلهولك (جودة لحد 4K).\n\n"
    "⚙️ <b>/settings</b> — اختيار موقع البحث\n"
    "🔍 <code>/secret search كلمة</code> أو inline\n"
    "🧲 <code>/torrent ubuntu</code> أو inline: <code>t ubuntu</code>\n\n"
    "أوامر: /stats /scan /pdf /torrent /settings /terms /speedtest /logs /killall\n"
    "حد الجزء: {limit}"
)

HELP_TEXT = (
    "📖 <b>الاستخدام</b> · <code>v{version}</code>\n\n"
    "1️⃣ ابعت لينك → اختار الجودة\n"
    "2️⃣ /settings لاختيار موقع البحث\n"
    "3️⃣ /secret search كلمة\n\n"
    "الموقع النشط: <b>{site}</b>\n"
    "حد الجزء: {limit}"
)


def _config(context: ContextTypes.DEFAULT_TYPE) -> Config:
    return context.bot_data["config"]


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    cfg = _config(context)
    user = update.effective_user
    if user is None or not is_terms_accepted(user.id):
        await send_terms(update.message)
        return
    await update.message.reply_text(
        START_TEXT.format(
            version=BOT_VERSION,
            limit=format_size(cfg.upload_limit_bytes),
        ),
        parse_mode="HTML",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    cfg = _config(context)
    await update.message.reply_text(
        HELP_TEXT.format(
            version=BOT_VERSION,
            limit=format_size(cfg.upload_limit_bytes),
            site=f"{site_label()} · تورنت: {', '.join(torrent_site_label(source) for source in get_active_torrent_sources(cfg.torrent_sources))}",
        ),
        parse_mode="HTML",
    )


_STATE_LABELS = {
    JobState.WAITING: "⏳ انتظار",
    JobState.DOWNLOADING: "📥 تنزيل",
    JobState.UPLOADING: "📤 رفع",
}


def _bar(percent: float, length: int = 12) -> str:
    filled = int(round(length * max(0.0, min(100.0, percent)) / 100))
    return "█" * filled + "░" * (length - filled)


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    cfg = _config(context)
    manager: DownloadManager = context.bot_data["manager"]
    stats = system_stats()
    disk_total, disk_used, disk_free = disk_usage_mb(cfg.download_dir)
    disk_percent = (disk_used * 100 / disk_total) if disk_total else 0.0

    start_time = context.bot_data.get("start_time")
    uptime = format_duration(time.monotonic() - start_time) if start_time else "؟"

    by_state = manager.active_by_state()
    active_total = manager.active_jobs()
    queue_lines = "\n".join(
        f"  {label}: <b>{by_state.get(state, 0)}</b>"
        for state, label in _STATE_LABELS.items()
        if by_state.get(state, 0) > 0
    ) or "  ✅ مفيش تحميلات شغالة"

    api_mode = "Local API · 2GB" if cfg.uses_local_api else "Cloud API · 50MB"
    run_mode = "Webhook" if cfg.effective_webhook_url else "Polling"

    text = (
        f"📊 <b>لوحة السيرفر</b> · <code>v{BOT_VERSION}</code>\n"
        "━━━━━━━━━━━━━━━━\n"
        f"🖥️ CPU\n<code>[{_bar(stats['cpu_percent'])}]</code> "
        f"<b>{stats['cpu_percent']:.0f}%</b>\n\n"
        f"💾 RAM\n<code>[{_bar(stats['memory_percent'])}]</code> "
        f"<b>{stats['memory_percent']:.0f}%</b>\n"
        f"   {stats['memory_used_mb']} / {stats['memory_total_mb']} MB\n\n"
        f"📀 Disk\n<code>[{_bar(disk_percent)}]</code> "
        f"<b>{disk_percent:.0f}%</b>\n"
        f"   مستخدم {disk_used} · فاضي <b>{disk_free}</b> MB\n\n"
        f"⏱ Uptime: <b>{uptime}</b>\n"
        "━━━━━━━━━━━━━━━━\n"
        f"🔌 تشغيل: <b>{run_mode}</b>\n"
        f"📤 رفع: <b>{api_mode}</b>\n"
        f"🧩 yt-dlp: <code>{yt_dlp.version.__version__}</code>\n"
        f"🔍 بحث: <b>{site_label()}</b>\n"
        "━━━━━━━━━━━━━━━━\n"
        f"⚙️ الطابور: <b>{active_total}</b>\n"
        f"{queue_lines}"
    )
    await update.message.reply_text(text, parse_mode="HTML")
