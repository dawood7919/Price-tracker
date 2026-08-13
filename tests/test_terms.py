from bot import settings_store
from bot.terms import TERMS_VERSION, accept_terms, is_terms_accepted


def test_terms_acceptance_is_persisted_per_user(monkeypatch, tmp_path):
    monkeypatch.setattr(settings_store, "_SETTINGS_PATH", tmp_path / "settings.json")

    assert not is_terms_accepted(101)
    accept_terms(101)

    assert is_terms_accepted(101)
    data = settings_store._load()
    assert data["terms_acceptance"]["101"] == TERMS_VERSION
    assert not is_terms_accepted(202)
