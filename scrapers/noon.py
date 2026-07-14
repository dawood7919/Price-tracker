import json

import requests
from bs4 import BeautifulSoup

from utils.helpers import parse_price_text

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def scrape(url: str) -> dict:
    response = requests.get(url, headers=HEADERS, timeout=15)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    data = _extract_structured_data(soup)
    if data:
        return data

    # Noon is a JS-heavy SPA; fall back to a headless browser when the
    # static HTML has no structured product data.
    return _scrape_with_playwright(url)


def _extract_structured_data(soup: BeautifulSoup) -> dict | None:
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            payload = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue

        candidates = payload if isinstance(payload, list) else [payload]
        for item in candidates:
            if not isinstance(item, dict) or item.get("@type") != "Product":
                continue
            offers = item.get("offers") or {}
            price = offers.get("price")
            if price is None:
                continue
            return {
                "name": item.get("name", "Unknown Product"),
                "price": float(price),
                "currency": offers.get("priceCurrency", "AED"),
                "image_url": item.get("image"),
                "store": "Noon",
            }
    return None


def _scrape_with_playwright(url: str) -> dict:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=HEADERS["User-Agent"])
        try:
            page.goto(url, timeout=30000, wait_until="networkidle")
            title = page.locator("h1").first.text_content(timeout=5000)
            price_text = page.locator("[class*='priceNow'], [class*='price']").first.text_content(
                timeout=5000
            )
            try:
                image_url = page.locator("img").first.get_attribute("src")
            except Exception:
                image_url = None
        finally:
            browser.close()

    price = parse_price_text(price_text)
    if price is None:
        raise ValueError("Could not extract price from the Noon page.")

    return {
        "name": (title or "Unknown Product").strip(),
        "price": price,
        "currency": "AED",
        "image_url": image_url,
        "store": "Noon",
    }
