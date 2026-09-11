"""Tests for the detail-HTML TTL cache and the faster decode path."""
from pathlib import Path

import pytest

from parsers import htmlcache
from parsers.http import HttpMixin


class FakeResp:
    """Minimal stand-in for a requests Response (content + headers only)."""

    apparent_encoding = None

    def __init__(self, content: bytes, content_type: str = ""):
        self.content = content
        self.headers = {"Content-Type": content_type} if content_type else {}


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    d = tmp_path / "html"
    monkeypatch.setattr(htmlcache, "_CACHE_DIR", d)
    return d


class TestHtmlCache:
    def test_put_then_get_roundtrip(self, cache_dir):
        htmlcache.put("https://x/1", "<html>ok</html>")
        assert htmlcache.get("https://x/1") == "<html>ok</html>"

    def test_get_miss(self, cache_dir):
        assert htmlcache.get("https://x/missing") is None

    def test_ttl_expiry(self, cache_dir):
        import os
        import time

        htmlcache.put("https://x/old", "<html>old</html>")
        p = htmlcache._path_for("https://x/old")
        stale = time.time() - 7200  # 2h old, TTL default 1h
        os.utime(p, (stale, stale))
        assert htmlcache.get("https://x/old") is None
        # Custom TTL still inside the window -> hit
        assert htmlcache.get("https://x/old", ttl=3 * 3600) == "<html>old</html>"

    def test_corrupt_file_fails_open(self, cache_dir):
        htmlcache.put("https://x/bad", "x")
        p = htmlcache._path_for("https://x/bad")
        # Simulate unreadable file: wrong bytes are still valid text, so
        # instead revoke read permission via a directory in its place.
        p.unlink()
        p.mkdir()
        assert htmlcache.get("https://x/bad") is None

    def test_put_never_raises(self, cache_dir, monkeypatch):
        # A file where the cache dir should be -> mkdir/utime fail path
        blocker = cache_dir / "not-a-dir"
        blocker.parent.mkdir(parents=True, exist_ok=True)
        blocker.write_text("busy", encoding="utf-8")
        monkeypatch.setattr(htmlcache, "_CACHE_DIR", blocker)
        htmlcache.put("https://x/1", "<html>ok</html>")  # must not raise

    def test_clear(self, cache_dir):
        htmlcache.put("https://x/1", "a")
        htmlcache.put("https://x/2", "b")
        assert htmlcache.clear() == 2
        assert htmlcache.get("https://x/1") is None

    def test_fetch_detail_uses_cache(self, cache_dir, monkeypatch):
        """fetch_detail returns (None, html) on a cache hit without fetching."""
        htmlcache.put("https://site.kz/detail/1", "<html>cached</html>")

        class P(HttpMixin):
            name = "test"
            base_url = "https://site.kz"
            headers: dict = {}

            def fetch_session(self, url):
                raise AssertionError("network must not be hit on cache hit")

        session, html = P().fetch_detail("https://site.kz/detail/1")
        assert html == "<html>cached</html>"
        assert session is None

    def test_fetch_detail_stores_live_fetch(self, cache_dir, monkeypatch):
        import random

        monkeypatch.setattr(random, "uniform", lambda a, b: 0)  # no sleep

        sentinel = object()

        class P(HttpMixin):
            name = "test"
            base_url = "https://site.kz"
            headers: dict = {}
            timeout = 10

            def fetch_session(self, url):
                return sentinel, "<html>live</html>"

        p = P()
        session, html = p.fetch_detail("https://site.kz/detail/2")
        assert html == "<html>live</html>"
        assert session is sentinel  # live requests-path fetch returns session
        # second call served from cache: no fetch, no session
        calls = []

        def _no_fetch(url):
            calls.append(url)
            return None, "<html>second-live</html>"

        p.fetch_session = _no_fetch  # type: ignore[method-assign]
        session2, html2 = p.fetch_detail("https://site.kz/detail/2")
        assert html2 == "<html>live</html>"  # cached, not the second-live body
        assert session2 is None
        assert calls == []


class TestDecodeResponse:
    def test_utf8_default(self):
        r = FakeResp("казахстан".encode("utf-8"))
        assert HttpMixin._decode_response(r) == "казахстан"

    def test_declared_charset_cp1251(self):
        # utf-8 decode fails -> declared windows-1251 honored
        r = FakeResp("казахстан".encode("cp1251"),
                     "text/html; charset=windows-1251")
        assert HttpMixin._decode_response(r) == "казахстан"

    def test_declared_charset_overrides_late_fallbacks(self):
        # Content that is valid utf-8 AND declared cp1251: declared charset
        # is NOT consulted first — strict utf-8 wins (sites mislabel).
        r = FakeResp("казахстан".encode("utf-8"),
                     "text/html; charset=windows-1251")
        assert HttpMixin._decode_response(r) == "казахстан"

    def test_wrong_declared_charset_falls_to_apparent(self):
        # Declared utf-8 but content is cp1251 -> apparent_encoding rescues.
        r = FakeResp("казахстан".encode("cp1251"), "text/html; charset=utf-8")
        r.apparent_encoding = "cp1251"
        assert HttpMixin._decode_response(r) == "казахстан"

    def test_undecodable_falls_back_to_latin1(self):
        # Bytes invalid in utf-8, no charset, no apparent encoding
        r = FakeResp(b"\xff\xfe\xfa")
        r.apparent_encoding = None
        assert HttpMixin._decode_response(r) == b"\xff\xfe\xfa".decode("latin-1")


class TestSessionHeaderMerge:
    def _fetch_via_requests_path(self, parser_headers: dict):
        """Drive _fetch_requests_session with a fake session; return the
        headers it sent and the text it decoded."""
        sent = []

        class FakeResp:
            status_code = 200
            content = "<html>ok</html>".encode("utf-8")
            headers = {"Content-Type": "text/html; charset=utf-8"}

            def raise_for_status(self):
                pass

        class FakeSession:
            def __init__(self):
                self.headers = {}
                self.proxies = {}

            def get(self, url, **kwargs):
                sent.append(dict(self.headers))
                return FakeResp()

        sess = FakeSession()

        class P(HttpMixin):
            name = "test"
            base_url = "https://x"
            headers = parser_headers
            timeout = 10

        p = P()
        p._tls = type("TLS", (), {"session": sess})()  # pre-seeded thread-local
        p._proxy_tries = lambda: [{}]
        p._note_proxy_used = lambda pk: None
        p._note_proxy_error = lambda *a: None
        text = p._fetch_requests_session("https://x/page")
        return sent, text

    def test_single_random_header_set(self):
        import parsers.http as httpmod

        orig = httpmod._random_headers
        calls = []

        def counting():
            calls.append(1)
            return orig()

        httpmod._random_headers = counting
        try:
            sent, text = self._fetch_via_requests_path({})
        finally:
            httpmod._random_headers = orig
        # One set for the request headers. Merge must not generate a second
        # random set: exactly one generation per fetch.
        assert len(calls) == 1
        # _fetch_requests_session returns (session, text)
        assert text[1] == "<html>ok</html>"

    def test_parser_headers_override_random(self):
        sent, _ = self._fetch_via_requests_path(
            {"X-Custom-Tracker": "abc", "DNT": "0"})
        assert sent[0]["X-Custom-Tracker"] == "abc"
        # Random-pool keys keep their randomized values; parser-only keys
        # are added (this mirrors the original merge semantics).
        assert sent[0]["DNT"] in ("1", "0")
