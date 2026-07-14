import logging

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


def main():
    database.init_db()

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
