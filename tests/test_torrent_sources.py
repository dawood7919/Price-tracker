from bot import settings_store
from bot.torrent_sources import (
    _parse_debian_listing,
    _parse_fedora_payload,
    _parse_ubuntu_listing,
)


def test_debian_parser_keeps_only_iso_torrent_files():
    listing = (
        '<a href="debian-13.0.0-amd64-netinst.iso.torrent">torrent</a>'
        '<a href="SHA256SUMS">checksums</a>'
        '<a href="debian-13.0.0-amd64-netinst.iso.torrent">duplicate</a>'
    )

    items = _parse_debian_listing(listing)

    assert len(items) == 1
    assert items[0]["source"] == "Debian الرسمي"
    assert items[0]["torrent"].endswith("debian-13.0.0-amd64-netinst.iso.torrent")


def test_ubuntu_parser_labels_results_with_source():
    listing = '<a href="ubuntu-24.04.4-desktop-amd64.iso.torrent">torrent</a>'

    items = _parse_ubuntu_listing(listing, "24.04")

    assert items[0]["source"] == "Ubuntu الرسمي"
    assert items[0]["name"] == "Ubuntu 24.04.4 Desktop (amd64)"


def test_fedora_parser_rejects_non_torrent_records():
    payload = [{"torrents": [{"torrent": "README.txt"}]}]

    assert _parse_fedora_payload(payload) == []


def test_torrent_source_settings_use_env_default_then_persist_toggle(monkeypatch, tmp_path):
    monkeypatch.setattr(settings_store, "_SETTINGS_PATH", tmp_path / "settings.json")

    assert settings_store.get_active_torrent_sources("ubuntu,debian") == ("ubuntu", "debian")
    assert settings_store.toggle_torrent_source("debian", "ubuntu,debian") == ("ubuntu",)
    # Disabling the final provider is rejected and retains a usable search engine.
    assert settings_store.toggle_torrent_source("ubuntu", "ubuntu,debian") == ("ubuntu",)
    assert settings_store.toggle_torrent_source("fedora", "ubuntu,debian") == ("ubuntu", "fedora")
