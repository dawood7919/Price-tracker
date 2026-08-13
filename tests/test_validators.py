from bot.validators import URLValidator


class TestValidate:
    def test_accepts_wellformed_url(self):
        ok, _ = URLValidator.validate("https://example.com/video/1")
        assert ok is True

    def test_rejects_empty(self):
        ok, msg = URLValidator.validate("")
        assert ok is False
        assert msg

    def test_rejects_no_scheme(self):
        ok, _ = URLValidator.validate("example.com/video")
        assert ok is False

    def test_rejects_ftp(self):
        ok, _ = URLValidator.validate("ftp://example.com/file")
        assert ok is False

    def test_rejects_localhost(self):
        ok, _ = URLValidator.validate("http://localhost:8080/x")
        assert ok is False

    def test_rejects_private_ip(self):
        ok, _ = URLValidator.validate("http://192.168.1.1/x")
        assert ok is False

    def test_rejects_loopback_ip(self):
        ok, _ = URLValidator.validate("http://127.0.0.1/x")
        assert ok is False


class TestExtractUrl:
    def test_finds_url_in_text(self):
        url = URLValidator.extract_url("شوف الفيديو ده https://youtu.be/abc123 حلو أوي")
        assert url == "https://youtu.be/abc123"

    def test_strips_trailing_punctuation(self):
        url = URLValidator.extract_url("(https://example.com/v).")
        assert url == "https://example.com/v"

    def test_returns_none_without_url(self):
        assert URLValidator.extract_url("مفيش لينك هنا") is None

    def test_returns_none_for_empty(self):
        assert URLValidator.extract_url("") is None
