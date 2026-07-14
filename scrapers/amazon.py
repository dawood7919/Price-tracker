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

    title_tag = soup.select_one("#productTitle")
    product_name = title_tag.get_text(strip=True) if title_tag else "Unknown Product"

    price_tag = (
        soup.select_one("span.a-price span.a-offscreen")
        or soup.select_one("#priceblock_ourprice")
        or soup.select_one("#priceblock_dealprice")
    )
    price = parse_price_text(price_tag.get_text(strip=True)) if price_tag else None

    if price is None:
        raise ValueError(
            "Could not extract price from the Amazon page. "
            "The page layout may have changed or the request was blocked."
        )

    image_tag = soup.select_one("#landingImage") or soup.select_one("#imgBlkFront")
    image_url = image_tag.get("src") if image_tag else None

    return {
        "name": product_name,
        "price": price,
        "currency": "AED",
        "image_url": image_url,
        "store": "Amazon",
    }
