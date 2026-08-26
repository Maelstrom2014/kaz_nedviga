"""Tests for randomized HTTP headers."""
import pytest
from parsers.base import _random_headers, USER_AGENTS, ACCEPT_LANGUAGES, BaseParser
from parsers.krisha import KrishaParser
from parsers.olx import OlxParser


class TestRandomHeaders:

    def test_returns_dict(self):
        h = _random_headers()
        assert isinstance(h, dict)
        assert "User-Agent" in h
        assert "Accept" in h
        assert "Accept-Language" in h

    def test_user_agent_from_pool(self):
        for _ in range(20):
            h = _random_headers()
            assert h["User-Agent"] in USER_AGENTS

    def test_accept_language_from_pool(self):
        for _ in range(20):
            h = _random_headers()
            assert h["Accept-Language"] in ACCEPT_LANGUAGES

    def test_different_calls_different_headers(self):
        # Probabilistically, 50 calls should produce at least 2 different UAs
        uas = set()
        for _ in range(50):
            uas.add(_random_headers()["User-Agent"])
        assert len(uas) >= 2

    def test_sec_fetch_present(self):
        h = _random_headers()
        assert "Sec-Fetch-Dest" in h
        assert "Sec-Fetch-Mode" in h

    def test_has_connection(self):
        h = _random_headers()
        assert h["Connection"] in ("keep-alive", "close")

    def test_has_upgrade_insecure(self):
        h = _random_headers()
        assert h["Upgrade-Insecure-Requests"] == "1"


class TestFetchWafFallback:
    """WAFs block by TLS fingerprint: plain requests gets 403 while a
    browser-like TLS (curl_cffi impersonation) passes. fetch() must try
    both, in the right order, per parser config."""

    def test_requests_403_falls_back_to_cffi(self):
        from unittest import mock
        import requests
        p = KrishaParser()
        err = requests.exceptions.HTTPError(
            response=mock.Mock(status_code=403))
        with mock.patch.object(p, "_fetch_requests", side_effect=err), \
             mock.patch.object(p, "_fetch_cffi_any",
                               return_value="<html>ok</html>") as cf:
            html = p.fetch("https://example.test/page")
        assert html == "<html>ok</html>"
        cf.assert_called_once()

    def test_unrelated_error_reraised_without_cffi(self):
        from unittest import mock
        import requests
        p = KrishaParser()
        err = requests.exceptions.Timeout("timed out")
        with mock.patch.object(p, "_fetch_requests", side_effect=err), \
             mock.patch.object(p, "_fetch_cffi_any") as cf:
            with pytest.raises(requests.exceptions.Timeout):
                p.fetch("https://example.test/page")
        cf.assert_not_called()

    def test_cffi_enabled_uses_cffi_first(self):
        from unittest import mock
        p = OlxParser()
        p.fail_fast_on_waf = False  # exercise the rotate+fallback path
        assert p.use_cffi is True
        with mock.patch.object(p, "_fetch_cffi_any",
                               return_value="cffi-html") as cf, \
             mock.patch.object(p, "_fetch_requests") as rq:
            html = p.fetch("https://example.test/page")
        assert html == "cffi-html"
        cf.assert_called_once()
        rq.assert_not_called()

    def test_cffi_all_profiles_fail_falls_back_to_requests(self):
        from unittest import mock
        p = OlxParser()
        p.fail_fast_on_waf = False  # exercise the rotate+fallback path
        with mock.patch.object(p, "_fetch_cffi_any", return_value=None), \
             mock.patch.object(p, "_fetch_requests",
                               return_value="plain-html") as rq:
            html = p.fetch("https://example.test/page")
        assert html == "plain-html"
        rq.assert_called_once()

    def test_fail_fast_uses_single_cffi_session(self):
        from unittest import mock
        p = OlxParser()
        assert p.fail_fast_on_waf is True
        with mock.patch.object(p, "_fetch_cffi",
                               return_value="ff-html") as fc, \
             mock.patch.object(p, "_fetch_cffi_any") as cfa, \
             mock.patch.object(p, "_fetch_requests") as rq:
            html = p.fetch("https://example.test/page")
        assert html == "ff-html"
        fc.assert_called_once()
        cfa.assert_not_called()
        rq.assert_not_called()

    def test_waf_block_detection(self):
        from unittest import mock
        import requests
        p = KrishaParser()
        assert p._is_waf_block(requests.exceptions.HTTPError(
            response=mock.Mock(status_code=403)))
        assert p._is_waf_block(requests.exceptions.HTTPError(
            response=mock.Mock(status_code=429)))
        assert not p._is_waf_block(requests.exceptions.HTTPError(
            response=mock.Mock(status_code=404)))
        assert p._is_waf_block(requests.exceptions.ConnectionError("x"))
        assert not p._is_waf_block(ValueError("nope"))


class TestParserHeaders:

    def test_krisha_has_random_ua(self):
        p1 = KrishaParser()
        p2 = KrishaParser()
        # Each parser instance gets fresh headers
        assert "User-Agent" in p1.headers
        assert "User-Agent" in p2.headers
        assert p1.headers["User-Agent"] in USER_AGENTS

    def test_custom_headers_merge(self):
        p = KrishaParser(headers={"X-Custom": "yes"})
        assert p.headers.get("X-Custom") == "yes"
        assert "User-Agent" in p.headers

    def test_all_parsers_have_headers(self):
        from parsers.factory import get_all_parsers
        for p in get_all_parsers():
            assert "User-Agent" in p.headers
            assert "Accept-Language" in p.headers
            assert "Accept" in p.headers
