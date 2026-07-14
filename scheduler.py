import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.ext import Application

import database
from bot import messages
from config import CHECK_INTERVAL_HOURS
from scrapers import scrape_product

logger = logging.getLogger(__name__)


async def check_all_prices(application: Application):
    products = database.get_all_products()
    logger.info("Checking prices for %d products", len(products))

    for product in products:
        try:
            fresh_data = scrape_product(product["url"])
        except Exception:
            logger.exception("Failed to check price for product #%s", product["id"])
            continue

        new_price = fresh_data["price"]
        old_price = database.update_product_price(product["id"], new_price)

        if old_price is not None and new_price < old_price:
            text = messages.price_drop_message(
                product_name=product["product_name"],
                previous_price=old_price,
                current_price=new_price,
                currency=fresh_data.get("currency", "AED"),
                url=product["url"],
            )
            try:
                await application.bot.send_message(chat_id=product["user_id"], text=text)
            except Exception:
                logger.exception("Failed to notify user %s", product["user_id"])


async def start_scheduler(application: Application) -> AsyncIOScheduler:
    """Registered as the bot's post_init hook so it starts inside the running event loop."""
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        check_all_prices,
        trigger="interval",
        hours=CHECK_INTERVAL_HOURS,
        args=[application],
        id="price_check_job",
    )
    scheduler.start()
    logger.info("Price check scheduler started (every %s hour(s))", CHECK_INTERVAL_HOURS)
    return scheduler
