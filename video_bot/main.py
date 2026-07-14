import asyncio
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

import database.repository as db
from bot.handlers import (
    about_command,
    download_callback_handler,
    help_command,
    start_command,
    url_message_handler,
)
from config import BOT_TOKEN
from utils.logging_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)


class _HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        pass


def start_health_check_server() -> None:
    """Hosting platforms like Render/Koyeb restart web services that never bind
    to $PORT, which would kill Telegram polling. This keeps the deploy alive."""
    port = int(os.getenv("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), _HealthCheckHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logger.info("Health check server listening on port %s", port)


def main() -> None:
    start_health_check_server()
    db.init_db()

    # Guards against a RuntimeError some newer Python versions raise when
    # run_polling() internally calls asyncio.get_event_loop() with no loop set.
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("about", about_command))
    application.add_handler(CallbackQueryHandler(download_callback_handler, pattern=r"^(dl|cancel):"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, url_message_handler))

    logger.info("Video bot is starting...")
    application.run_polling(allowed_updates=None)


if __name__ == "__main__":
    main()
