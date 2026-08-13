import pytest

from bot.netspeed import SpeedTestError, run_speedtest


class _FakeResults:
    def __init__(self, data):
        self._data = data

    def dict(self):
        return self._data


class _FakeSpeedtest:
    def __init__(self, download_bps, upload_bps, ping, server):
        self._download_bps = download_bps
        self._upload_bps = upload_bps
        self.results = _FakeResults(
            {
                "download": download_bps,
                "upload": upload_bps,
                "ping": ping,
                "server": server,
            }
        )

    def get_best_server(self):
        pass

    def download(self):
        pass

    def upload(self):
        pass


def test_converts_bps_to_mbps_and_extracts_server_info(monkeypatch):
    import bot.netspeed as netspeed_module

    fake = _FakeSpeedtest(
        download_bps=157_000_000,
        upload_bps=29_000_000,
        ping=12.5,
        server={"name": "Some ISP", "country": "Egypt"},
    )
    monkeypatch.setattr(netspeed_module.speedtest, "Speedtest", lambda: fake)

    result = run_speedtest()

    assert result["download_mbps"] == pytest.approx(157.0)
    assert result["upload_mbps"] == pytest.approx(29.0)
    assert result["ping_ms"] == 12.5
    assert result["server_name"] == "Some ISP"
    assert result["server_country"] == "Egypt"


def test_missing_server_info_falls_back_gracefully(monkeypatch):
    import bot.netspeed as netspeed_module

    fake = _FakeSpeedtest(download_bps=1_000_000, upload_bps=1_000_000, ping=1.0, server={})
    monkeypatch.setattr(netspeed_module.speedtest, "Speedtest", lambda: fake)

    result = run_speedtest()

    assert result["server_name"] == "؟"
    assert result["server_country"] == "؟"


def test_wraps_failures_as_speedtesterror(monkeypatch):
    import bot.netspeed as netspeed_module

    class BoomSpeedtest:
        def get_best_server(self):
            raise RuntimeError("no servers available")

    monkeypatch.setattr(netspeed_module.speedtest, "Speedtest", lambda: BoomSpeedtest())

    with pytest.raises(SpeedTestError, match="no servers available"):
        run_speedtest()
