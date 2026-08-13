"""Network speed test for /speedtest, via the speedtest-cli library.

Blocking and network-bound (typically 10-30s) — always run via
asyncio.to_thread, never directly on the event loop.
"""

from __future__ import annotations

import speedtest


class SpeedTestError(RuntimeError):
    pass


def run_speedtest() -> dict:
    try:
        st = speedtest.Speedtest()
        st.get_best_server()
        st.download()
        st.upload()
    except Exception as exc:
        raise SpeedTestError(str(exc) or type(exc).__name__) from exc

    result = st.results.dict()
    server = result.get("server") or {}
    return {
        "download_mbps": result["download"] / 1_000_000,
        "upload_mbps": result["upload"] / 1_000_000,
        "ping_ms": result["ping"],
        "server_name": server.get("name", "؟"),
        "server_country": server.get("country", "؟"),
    }
