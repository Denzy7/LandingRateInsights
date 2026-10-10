import io
import json

from landingrate import updates


def test_parse_version():
    assert updates.parse_version("v1.2.10") == (1, 2, 10)
    assert updates.parse_version("1.0") == (1, 0)
    assert updates.parse_version("nightly") is None


def test_newer():
    assert updates._newer("v1.0.1", "1.0.0")
    assert updates._newer("1.1", "1.0.9")
    assert not updates._newer("1.0", "1.0.0")
    assert not updates._newer("v0.9.9", "1.0.0")
    assert not updates._newer("junk", "1.0.0")


def _fake_urlopen(payload=None, exc=None):
    def urlopen(req, timeout=None):
        assert req.full_url == updates.LATEST_API
        if exc:
            raise exc
        return io.BytesIO(json.dumps(payload).encode())
    return urlopen


def test_check_available(monkeypatch):
    monkeypatch.setattr(updates.urllib.request, "urlopen",
                        _fake_urlopen({"tag_name": "v9.9.9", "html_url": "https://example/rel"}))
    info = updates.check(current="1.0.0")
    assert info == {"status": "available", "current": "1.0.0", "latest": "9.9.9", "url": "https://example/rel"}


def test_check_current(monkeypatch):
    monkeypatch.setattr(updates.urllib.request, "urlopen", _fake_urlopen({"tag_name": "v1.0.0"}))
    assert updates.check(current="1.0.0")["status"] == "current"


def test_check_offline_is_quiet(monkeypatch):
    monkeypatch.setattr(updates.urllib.request, "urlopen", _fake_urlopen(exc=OSError("no network")))
    info = updates.check(current="1.0.0")
    assert info["status"] == "error" and info["url"] == updates.RELEASES_URL
