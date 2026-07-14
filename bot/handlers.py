import logging

from telegram import Update
from telegram.ext import ContextTypes

import database
from bot import messages
from scrapers import get_scraper, scrape_product

logger = logging.getLogger(__name__)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    database.add_user(user.id, user.username)
    await update.message.reply_text(messages.WELCOME_MESSAGE)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(messages.HELP_MESSAGE)


async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not context.args:
        await update.message.reply_text(messages.usage_add_message())
        return

    url = context.args[0]
    database.add_user(user.id, user.username)

    try:
        get_scraper(url)
    except ValueError:
        await update.message.reply_text(messages.unsupported_site_message())
        return

    status_message = await update.message.reply_text("⏳ بجيب بيانات المنتج...")

    try:
        product = scrape_product(url)
    except Exception as exc:
        logger.exception("Failed to scrape product: %s", url)
        await status_message.edit_text(messages.scrape_error_message(str(exc)))
        return

    database.add_product(
        user_id=user.id,
        url=url,
        product_name=product["name"],
        price=product["price"],
        store_name=product["store"],
    )

    await status_message.edit_text(messages.product_added_message(product))


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    products = database.get_products_by_user(user.id)
    await update.message.reply_text(messages.product_list_message(products))


async def remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(messages.usage_remove_message())
        return

    product_id = int(context.args[0])
    product = database.get_product(product_id, user_id=user.id)
    if not product:
        await update.message.reply_text(messages.product_not_found_message())
        return

    database.remove_product(product_id, user.id)
    await update.message.reply_text(messages.product_removed_message(product["product_name"]))
