import asyncio
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram.ext import Application, CommandHandler

import database
from bot.handlers import add_command, help_command, list_command, remove_command, start_command
from config import BOT_TOKEN
from scheduler import start_scheduler

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


class _HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        pass


def start_health_check_server():
    """Render's Web Service plan requires an open HTTP port to pass health checks.
    The bot itself only needs Telegram polling, so this just keeps the deploy alive."""
    port = int(os.getenv("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), _HealthCheckHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logger.info("Health check server listening on port %s", port)


def main():
    start_health_check_server()
    database.init_db()

    # python-telegram-bot's run_polling() calls asyncio.get_event_loop() internally,
    # which raises on Python 3.14+ if no loop has been set on the main thread yet.
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    application = Application.builder().token(BOT_TOKEN).post_init(start_scheduler).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("add", add_command))
    application.add_handler(CommandHandler("list", list_command))
    application.add_handler(CommandHandler("remove", remove_command))

    logger.info("Bot is starting...")
    application.run_polling(allowed_updates=None)


if __name__ == "__main__":
    main()
