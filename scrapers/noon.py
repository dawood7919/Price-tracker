import json

import requests
from bs4 import BeautifulSoup

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

    raise ValueError(
        "Could not extract product data from the Noon page. "
        "This page likely requires JavaScript rendering, which isn't supported."
    )


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
