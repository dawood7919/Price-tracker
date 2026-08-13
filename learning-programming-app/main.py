"""Entry point.

Two run modes:
- Webhook mode (production / Render): when WEBHOOK_URL or RENDER_EXTERNAL_URL
  is set. Serves an aiohttp app with a real health check at ``/`` and a
  Telegram webhook at ``/webhook`` that verifies the secret token header.
- Polling mode (local development): when no public URL is configured.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import time

import yt_dlp
from aiohttp import web
from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    InlineQueryHandler,
    MessageHandler,
    filters,
)

from bot.config import Config, get_config
from bot.downloader import YTDLPDownloader
from bot.handlers import (
    ai_command,
    handle_callback,
    handle_document,
    handle_message,
    help_command,
    killall_command,
    logs_command,
    pdf_command,
    scan_command,
    secret_command,
    secret_inline_query,
    setcookies_command,
    speedtest_command,
    start_command,
    stats_command,
    torrent_document_handler,
    torrent_download_button,
    torrent_inline_query,
    torrent_search_command,
)
from bot.handlers.tools import make_log_file_handler
from bot.manager import DownloadManager
from bot.uploader import UploadManager
from bot.utils import (
    format_exception_for_owner,
    free_disk_mb,
    notify_owner,
    system_stats,
    write_cookies_file,
)

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(), make_log_file_handler()],
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("main")

WEBHOOK_PATH = "/webhook"
SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"


async def _routed_inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route inline queries: secret search (s …) first, else torrent search."""
    if await secret_inline_query(update, context):
        return
    await torrent_inline_query(update, context)


def build_application(config: Config) -> Application:
    builder = (
        ApplicationBuilder()
        .token(config.telegram_bot_token)
        .connect_timeout(30)
        .read_timeout(60)
        .write_timeout(60)
        .media_write_timeout(1800)
        .pool_timeout(60)
    )
    if config.telegram_base_url:
        base = config.telegram_base_url.rstrip("/")
        builder = builder.base_url(f"{base}/bot").base_file_url(f"{base}/file/bot")
        logger.info("Using Local Bot API Server at %s (upload limit ~2GB)", base)
    if config.effective_webhook_url:
        builder = builder.updater(None)

    application = builder.build()

    application.bot_data["config"] = config
    application.bot_data["manager"] = DownloadManager(config)
    application.bot_data["downloader"] = YTDLPDownloader(config)
    application.bot_data["uploader"] = UploadManager(config, application.bot)
    application.bot_data["start_time"] = time.monotonic()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("setcookies", setcookies_command))
    application.add_handler(CommandHandler("scan", scan_command))
    application.add_handler(CommandHandler("secret", secret_command))
    application.add_handler(CommandHandler("ai", ai_command))
    application.add_handler(CommandHandler("pdf", pdf_command))
    application.add_handler(CommandHandler("killall", killall_command))
    application.add_handler(CommandHandler("logs", logs_command))
    application.add_handler(CommandHandler("speedtest", speedtest_command))
    application.add_handler(CommandHandler("torrent", torrent_search_command))
    application.add_handler(InlineQueryHandler(_routed_inline_query))
    application.add_handler(CallbackQueryHandler(torrent_download_button, pattern=r"^torrentdl:"))
    application.add_handler(
        MessageHandler(filters.Document.FileExtension("torrent"), torrent_document_handler)
    )
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(_error_handler)
    return application


async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled error while processing update", exc_info=context.error)
    chat_id = None
    user_id = None
    if isinstance(update, Update):
        if update.effective_chat is not None:
            chat_id = update.effective_chat.id
        if update.effective_user is not None:
            user_id = update.effective_user.id
    if chat_id is not None:
        with contextlib.suppress(Exception):
            await context.bot.send_message(chat_id, "❌ حصل خطأ غير متوقع. جرب تاني.")

    if context.error is not None:
        config: Config | None = context.bot_data.get("config")
        if config is not None:
            ctx_bits = []
            if user_id is not None:
                ctx_bits.append(f"user={user_id}")
            if chat_id is not None:
                ctx_bits.append(f"chat={chat_id}")
            ctx_str = ", ".join(ctx_bits) if ctx_bits else None
            text = format_exception_for_owner(context.error, context=ctx_str)
            await notify_owner(context.bot, config, text)


def make_web_app(application: Application, config: Config) -> web.Application:
    async def health(_request: web.Request) -> web.Response:
        free = free_disk_mb(config.download_dir)
        manager: DownloadManager = application.bot_data["manager"]
        payload = {
            "status": "ok" if free >= config.min_free_disk_mb else "degraded",
            "free_disk_mb": free,
            "active_jobs": manager.active_jobs(),
            **system_stats(),
        }
        return web.json_response(payload)

    async def telegram_webhook(request: web.Request) -> web.Response:
        if request.headers.get(SECRET_HEADER) != config.webhook_secret:
            return web.Response(status=403, text="forbidden")
        try:
            data = await request.json()
        except Exception:
            return web.Response(status=400, text="bad request")
        update = Update.de_json(data, application.bot)
        if update is not None:
            await application.update_queue.put(update)
        return web.Response(text="ok")

    aio_app = web.Application(client_max_size=1024 * 1024)
    aio_app.router.add_get("/", health)
    aio_app.router.add_get("/health", health)
    aio_app.router.add_post(WEBHOOK_PATH, telegram_webhook)
    return aio_app


async def run_webhook(application: Application, config: Config) -> None:
    public_url = config.effective_webhook_url
    assert public_url is not None
    aio_app = make_web_app(application, config)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    async with application:
        me = await application.bot.get_me()
        logger.info("Bot @%s starting in webhook mode", me.username)
        await application.bot.set_webhook(
            url=f"{public_url}{WEBHOOK_PATH}",
            secret_token=config.webhook_secret,
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )
        await application.start()

        runner = web.AppRunner(aio_app)
        await runner.setup()
        site = web.TCPSite(runner, host="0.0.0.0", port=config.port)
        await site.start()
        logger.info("Listening on port %s — webhook set to %s%s", config.port, public_url, WEBHOOK_PATH)

        await stop_event.wait()

        logger.info("SIGTERM received — shutting down gracefully")
        await runner.cleanup()
        await application.stop()


def run_polling(application: Application) -> None:
    logger.info("No public URL configured — starting in polling mode (local dev)")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


def _write_cookies_file(config: Config) -> None:
    if not config.ytdlp_cookies_content or config.ytdlp_cookies_file:
        return
    path = write_cookies_file(config.download_dir, config.ytdlp_cookies_content)
    config.ytdlp_cookies_file = str(path)
    logger.info("Wrote YTDLP_COOKIES_CONTENT to %s", path)


def main() -> None:
    config = get_config()
    config.download_dir.mkdir(parents=True, exist_ok=True)
    _write_cookies_file(config)
    logger.info("yt-dlp version: %s", yt_dlp.version.__version__)
    application = build_application(config)
    if config.effective_webhook_url:
        asyncio.run(run_webhook(application, config))
    else:
        run_polling(application)


if __name__ == "__main__":
    main()
