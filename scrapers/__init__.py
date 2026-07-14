from utils.helpers import extract_domain

from . import amazon, noon

_SCRAPER_MAP = {
    "amazon.ae": amazon,
    "amazon.com": amazon,
    "noon.com": noon,
}


def get_scraper(url: str):
    domain = extract_domain(url)
    for key, module in _SCRAPER_MAP.items():
        if key in domain:
            return module
    raise ValueError(f"No scraper available for domain: {domain}")


def scrape_product(url: str) -> dict:
    scraper = get_scraper(url)
    return scraper.scrape(url)
