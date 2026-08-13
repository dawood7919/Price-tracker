#!/usr/bin/env python3
"""Run on the server to verify HQPorner search works from this network.

  python3 scripts/test_hqporner.py milf
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# allow `python3 scripts/test_hqporner.py` from repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.sites.hqporner import search  # noqa: E402


async def main() -> None:
    q = " ".join(sys.argv[1:]) or "milf"
    print(f"Searching HQPorner for: {q!r}")
    results = await search(q, limit=10)
    print(f"Got {len(results)} results\n")
    for i, item in enumerate(results, 1):
        print(f"{i:2}. {item['title']}")
        print(f"    {item['url']}")
    if not results:
        print("FAILED: 0 results — server may be blocked by the site/CDN.")
        sys.exit(1)
    print("\nOK")


if __name__ == "__main__":
    asyncio.run(main())
