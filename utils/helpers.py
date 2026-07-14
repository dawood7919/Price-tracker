from urllib.parse import urlparse


def extract_domain(url: str) -> str:
    netloc = urlparse(url).netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


def format_price(price: float, currency: str = "AED") -> str:
    return f"{price:,.2f} {currency}"


def parse_price_text(text: str | None) -> float | None:
    if not text:
        return None
    cleaned = "".join(ch for ch in text if ch.isdigit() or ch in ".,")
    cleaned = cleaned.replace(",", "")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None
