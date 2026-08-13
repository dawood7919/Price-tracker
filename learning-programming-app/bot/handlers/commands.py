from __future__ import annotations

import time

import yt_dlp
from telegram import Update
from telegram.ext import ContextTypes

from ..config import Config
from ..manager import DownloadManager, JobState
from ..utils import disk_usage_mb, format_duration, format_size, system_stats

START_TEXT = (
    "أهلًا 👋\n\n"
    "ابعتلي لينك أي فيديو (يوتيوب، تويتر، فيسبوك، تيك توك...) وهنزّلهولك.\n\n"
    "• اختار الجودة اللي تناسبك (لغاية 4K) أو صوت فقط (MP3/M4A/WAV/FLAC) 🎵\n"
    "• الملفات الكبيرة بتتقسم تلقائيًا لأجزاء قابلة للتشغيل\n"
    "• البلايليست مدعومة (أول {playlist} فيديو)\n\n"
    "أقصى حجم للجزء الواحد: {limit}\n\n"
    "🛠 <b>كل الأوامر المتاحة:</b>\n"
    "/start — الرسالة دي\n"
    "/help — طريقة الاستخدام بالتفصيل\n"
    "/stats — حالة السيرفر كاملة\n"
    "/speedtest — قياس سرعة إنترنت السيرفر\n"
    "/scan &lt;لينك&gt; — تحميل كل الفيديوهات من صفحة قائمة\n"
    "/secret — لصاحب البوت بس (قائمة فيديوهات من مصدر خاص)\n"
    "/ai &lt;سؤال&gt; — لصاحب البوت بس (ذكاء اصطناعي)\n"
    "/pdf &lt;لينك&gt; — تحويل صفحة ويب لملف PDF\n"
    "/torrent &lt;كلمة بحث&gt; — تحميل من مصدر ثابت وشرعي (Ubuntu ISO)\n"
    "  (وكمان بحث inline: اكتب @اسم_البوت في أي محادثة)\n"
    "/setcookies — لصاحب البوت بس\n"
    "/killall — لصاحب البوت بس، إيقاف طارئ ومسح التخزين\n"
    "/logs — لصاحب البوت بس، إرسال ملف اللوج"
)

HELP_TEXT = (
    "📖 طريقة الاستخدام:\n\n"
    "1️⃣ ابعت لينك الفيديو في رسالة\n"
    "2️⃣ اختار الجودة من الأزرار\n"
    "3️⃣ استنى وهيوصلك الملف\n\n"
    "ℹ️ ملاحظات:\n"
    "• تقدر تشغّل أكتر من تحميل في نفس الوقت من غير حد يومي\n"
    "• لو الملف أكبر من {limit} بيتقسم لأجزاء، كل جزء شغال لوحده\n"
    "• تقدر تلغي أي طلب شغال بزرار الإلغاء 🚫"
)


def _config(context: ContextTypes.DEFAULT_TYPE) -> Config:
    return context.bot_data["config"]


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    cfg = _config(context)
    await update.message.reply_text(
        START_TEXT.format(
            playlist=cfg.max_playlist_items,
            limit=format_size(cfg.upload_limit_bytes),
        ),
        parse_mode="HTML",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    cfg = _config(context)
    await update.message.reply_text(
        HELP_TEXT.format(limit=format_size(cfg.upload_limit_bytes))
    )


_STATE_LABELS = {
    JobState.WAITING: "⏳ قيد الانتظار",
    JobState.DOWNLOADING: "📥 بيتنزّل",
    JobState.UPLOADING: "📤 بيترفع",
}


def _bar(percent: float, length: int = 10) -> str:
    filled = int(round(length * max(0.0, min(100.0, percent)) / 100))
    return "█" * filled + "░" * (length - filled)


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Full server status: CPU/RAM/disk/uptime/upload-mode/job queue breakdown.

    No live internet-speed test or ping here on purpose — those are slow
    and unreliable to run inline in a command; actual observed download/
    upload speed is already shown in each job's progress panel.
    """
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
        f"    {label}: <b>{by_state.get(state, 0)}</b>"
        for state, label in _STATE_LABELS.items()
        if by_state.get(state, 0) > 0
    ) or "    مفيش تحميلات شغالة دلوقتي"

    api_mode = "Local Bot API (2GB)" if cfg.uses_local_api else "Cloud API (50MB)"
    run_mode = "Webhook" if cfg.effective_webhook_url else "Polling"

    text = (
        "📊 <b>حالة السيرفر</b>\n"
        "――――――――――――――――\n"
        f"🖥️ المعالج: <code>[{_bar(stats['cpu_percent'])}]</code> {stats['cpu_percent']:.0f}%\n"
        f"💾 الرام: <code>[{_bar(stats['memory_percent'])}]</code> {stats['memory_percent']:.0f}% "
        f"({stats['memory_used_mb']}/{stats['memory_total_mb']} MB)\n"
        f"📀 الديسك: <code>[{_bar(disk_percent)}]</code> {disk_percent:.0f}% "
        f"({disk_used}/{disk_total} MB — فاضي {disk_free} MB)\n"
        f"⏱ شغال من: <b>{uptime}</b>\n\n"
        "🤖 <b>إعدادات البوت</b>\n"
        "――――――――――――――――\n"
        f"🔌 وضع التشغيل: <b>{run_mode}</b>\n"
        f"📤 حد الرفع: <b>{api_mode}</b>\n"
        f"🧩 نسخة yt-dlp: <b>{yt_dlp.version.__version__}</b>\n"
        f"🎬 أقصى فيديوهات بلايليست: <b>{cfg.max_playlist_items}</b>\n\n"
        "⚙️ <b>الطابور</b>\n"
        "――――――――――――――――\n"
        f"إجمالي التحميلات الشغالة: <b>{active_total}</b>\n"
        f"{queue_lines}"
    )
    await update.message.reply_text(text, parse_mode="HTML")
