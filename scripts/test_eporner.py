#!/usr/bin/env python3
"""Test Eporner search from the server.

  python3 scripts/test_eporner.py milf
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.sites.eporner import search  # noqa: E402


async def main() -> None:
    q = " ".join(sys.argv[1:]) or "milf"
    print(f"Searching Eporner for: {q!r}")
    results = await search(q, limit=8)
    print(f"Got {len(results)} results\n")
    for i, item in enumerate(results, 1):
        print(f"{i:2}. {item['title']}")
        print(f"    {item['url']}")
    if not results:
        print("FAILED: 0 results (age-gate or block?)")
        sys.exit(1)
    print("\nOK — try: yt-dlp -F <first_url>")


if __name__ == "__main__":
    asyncio.run(main())
