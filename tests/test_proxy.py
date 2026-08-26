"""Tests for the free-proxy list pipeline (parsers/proxy.py + app endpoints).

Network is mocked so the suite stays fast and deterministic.
"""
import types

import pytest

import parsers.proxy as proxymod
from parsers.proxy import (
    DEFAULT_PROXY_SETTINGS,
    PROXY_SOURCES,
    ProxyEntry,
    ProxyPool,
    get_pool,
    parse_html,
    parse_json,
    parse_text,
    sanitize_proxy_settings,
    test_proxy,
)


@pytest.fixture(autouse=True)
def _reset_pool():
    """Reset the shared pool singleton around every test in this module."""
    pool = get_pool()
    pool.clear()
    pool.configure(DEFAULT_PROXY_SETTINGS)
    yield
    pool.clear()
    pool.configure(DEFAULT_PROXY_SETTINGS)


# ---------------------------------------------------------------- extractors

def test_parse_text_valid():
    text = "socks5://1.2.3.4:1080\nhttp://5.6.7.8:8080\nhttps://9.10.11.12:3128\n"
    entries = parse_text(text)
    assert len(entries) == 3
    assert (entries[0].host, entries[0].port, entries[0].scheme) == ("1.2.3.4", 1080, "socks5")
    assert entries[1].scheme == "http"


def test_parse_text_rejects_garbage():
    assert parse_text("") == []
    assert parse_text("foo bar\nnot a proxy\n") == []


def test_parse_text_dedupes():
    text = "http://1.2.3.4:8080\nhttp://1.2.3.4:8080\n"
    assert len(parse_text(text)) == 1


def test_parse_html_table():
    html = ("<table><tbody>"
            "<tr><td>1.2.3.4</td><td>1080</td></tr>"
            "<tr><td>5.6.7.8</td><td>8080</td></tr>"
            "</tbody></table>")
    entries = parse_html(html)
    assert len(entries) >= 2
    assert all(e.port for e in entries)
    assert entries[0].host == "1.2.3.4"


def test_parse_json_fields():
    j = '[{"ip": "1.2.3.4", "port": 1080}, {"proxy": "http://5.6.7.8:8080"}]'
    entries = parse_json(j)
    assert len(entries) == 2
    assert {e.host for e in entries} == {"1.2.3.4", "5.6.7.8"}


def test_default_sources_has_five():
    assert len(PROXY_SOURCES) == 5
    keys = {s["key"] for s in PROXY_SOURCES}
    assert {"proxyscrape", "freeproxylist", "openproxylist", "a8", "proxylistde"} <= keys


# ------------------------------------------------------------------ sanitize

def test_sanitize_none_returns_defaults():
    out = sanitize_proxy_settings(None)
    assert out["enabled"] is False
    assert out["max_proxies"] == DEFAULT_PROXY_SETTINGS["max_proxies"]
    assert out["rotation"] in ("random", "round_robin")


def test_sanitize_clamps_max_proxies():
    assert sanitize_proxy_settings({"max_proxies": 99999})["max_proxies"] <= 2000
    assert sanitize_proxy_settings({"max_proxies": 0})["max_proxies"] >= 1


def test_sanitize_filters_unknown_sources():
    out = sanitize_proxy_settings({"sources": ["proxyscrape", "bogus"]})
    assert "bogus" not in out["sources"]
    assert "proxyscrape" in out["sources"]


def test_sanitize_bad_rotation_falls_back():
    assert sanitize_proxy_settings({"rotation": "chaos"})["rotation"] in ("random", "round_robin")


# ----------------------------------------------------------------------- pool

def test_pool_disabled_returns_empty():
    p = ProxyPool()
    p.configure({"enabled": False})
    p.set_proxies([ProxyEntry("1.2.3.4", 8080, "http", "t")])
    assert p.get_proxies() == {}


def test_pool_enabled_returns_proxies():
    p = ProxyPool()
    p.configure({"enabled": True, "rotation": "random"})
    p.set_proxies([ProxyEntry("1.2.3.4", 8080, "http", "t")])
    got = p.get_proxies()
    assert got["http"] == "http://1.2.3.4:8080"
    assert got["https"] == "http://1.2.3.4:8080"


def test_pool_enabled_but_empty_returns_empty():
    p = ProxyPool()
    p.configure({"enabled": True})
    assert p.get_proxies() == {}


def test_pool_round_robin_cycles():
    p = ProxyPool()
    p.configure({"enabled": True, "rotation": "round_robin"})
    p.set_proxies([ProxyEntry("1.1.1.1", 80, "http", "t"),
                   ProxyEntry("2.2.2.2", 81, "http", "t")])
    a, b, c = p.next().host, p.next().host, p.next().host
    assert a != b
    assert c == a  # wraps around


def test_pool_next_empty_is_none():
    p = ProxyPool()
    p.set_proxies([])
    assert p.next() is None


def test_pool_len_snapshot_clear():
    p = ProxyPool()
    p.set_proxies([ProxyEntry("1.1.1.1", 80, "http", "t")])
    assert len(p) == 1
    assert len(p.snapshot()) == 1
    p.clear()
    assert len(p) == 0


def test_pool_status_shape():
    p = ProxyPool()
    p.configure({"enabled": True})
    st = p.status()
    for k in ("count", "enabled", "rotation", "last_refresh", "stats"):
        assert k in st


# ------------------------------------------------------------------- test_proxy

def test_test_proxy_success(monkeypatch):
    class FakeResp:
        status_code = 200

    class FakeSession:
        def get(self, url, timeout=None):
            return FakeResp()

    monkeypatch.setattr(proxymod.requests, "Session", FakeSession)
    assert test_proxy(ProxyEntry("1.2.3.4", 8080, "http", "t"), "https://example.com", 5) is True


def test_test_proxy_connection_error(monkeypatch):
    class FakeSession:
        def get(self, url, timeout=None):
            raise proxymod.requests.RequestException("boom")

    monkeypatch.setattr(proxymod.requests, "Session", FakeSession)
    assert test_proxy(ProxyEntry("1.2.3.4", 8080, "http", "t"), "https://example.com", 5) is False


# -------------------------------------------------------------------- base.py

def test_base_proxy_kwargs_reads_shared_pool():
    from parsers.base import BaseParser

    pool = get_pool()
    pool.configure({"enabled": True, "rotation": "random"})
    pool.set_proxies([ProxyEntry("9.9.9.9", 1080, "socks5", "t")])
    pk = BaseParser._proxy_kwargs(types.SimpleNamespace(name="etagi.com"))
    assert pk.get("http", "").startswith("socks5://9.9.9.9")


# -------------------------------------------------------------------- refresh

def test_refresh_uses_mocked_sources(monkeypatch):
    def fake_fetch(source, timeout=15):
        if source["key"] == "proxyscrape":
            return [ProxyEntry("1.1.1.1", 1080, "socks5", "proxyscrape"),
                    ProxyEntry("2.2.2.2", 8080, "http", "proxyscrape")]
        return []

    def fake_test(entry, test_url, timeout):
        return entry.host == "1.1.1.1"  # only the first one "works"

    monkeypatch.setattr(proxymod, "fetch_source", fake_fetch)
    monkeypatch.setattr(proxymod, "test_proxy", fake_test)

    pool = get_pool()
    stats = pool.refresh(dict(DEFAULT_PROXY_SETTINGS, sources=["proxyscrape"]))
    assert len(pool) == 1
    assert pool.snapshot()[0].host == "1.1.1.1"
    assert stats["proxyscrape"]["fetched"] == 2
    assert stats["proxyscrape"]["working"] == 1
    assert pool.last_refresh is not None


def test_refresh_no_sources_zero(monkeypatch):
    monkeypatch.setattr(proxymod, "fetch_source", lambda source, timeout=15: [])
    pool = get_pool()
    stats = pool.refresh(dict(DEFAULT_PROXY_SETTINGS))
    assert len(pool) == 0
    assert all(v.get("working", 0) == 0 for v in stats.values())


# ---------------------------------------------------- per-site + usage + errors

def test_get_proxies_respects_proxy_sites():
    pool = get_pool()
    pool.configure({"enabled": True, "proxy_sites": ["etagi.com", "olx.kz"]})
    pool.set_proxies([ProxyEntry("3.3.3.3", 8080, "http", "t")])
    assert pool.get_proxies("etagi.com")["https"].startswith("http://3.3.3.3")
    assert pool.get_proxies("olx.kz")["https"].startswith("http://3.3.3.3")
    assert pool.get_proxies("krisha.kz") == {}  # not selected -> direct


def test_usage_tracking_and_clear():
    pool = get_pool()
    pool.clear_usage()
    assert pool.usage() == {}
    pool.note_use("etagi", "http://1.1.1.1:1080")
    assert pool.usage()["etagi"]["url"] == "http://1.1.1.1:1080"
    pool.clear_usage()
    assert pool.usage() == {}


def test_error_log_and_clear():
    pool = get_pool()
    pool.clear_errors()
    assert pool.errors() == []
    pool.log_error("etagi", "https://x", "http://1.1.1.1:1080", "Timeout: read")
    errs = pool.errors()
    assert len(errs) == 1
    assert errs[0]["site"] == "etagi"
    assert "Timeout" in errs[0]["error"]
    assert pool.status()["error_count"] == 1
    pool.clear_errors()
    assert pool.errors() == []


def test_known_sites_and_default_proxy_sites():
    assert proxymod.DEFAULT_PROXY_SETTINGS["proxy_sites"] == list(proxymod.KNOWN_SITES)


def test_sanitize_filters_proxy_sites():
    cfg = proxymod.sanitize_proxy_settings({"proxy_sites": ["etagi", "bogus", "olx"]})
    assert cfg["proxy_sites"] == ["etagi.com", "olx.kz"]  # legacy keys migrated, bogus dropped


# ------------------------------------------------------------------ endpoints

def test_get_api_proxy(client):
    r = client.get("/api/proxy")
    assert r.status_code == 200
    d = r.get_json()
    assert "config" in d and "status" in d and "sources" in d
    assert "enabled" in d["config"]


def test_post_api_proxy(client):
    r = client.post("/api/proxy", json={"proxy": {
        "enabled": False, "max_proxies": 40, "rotation": "round_robin",
        "sources": ["proxyscrape"]}})
    assert r.status_code == 200
    d = r.get_json()
    assert d["ok"] is True
    assert d["config"]["max_proxies"] == 40
    assert d["config"]["rotation"] == "round_robin"
    assert get_pool().max_proxies == 40


def test_post_api_proxy_clear(client):
    get_pool().set_proxies([ProxyEntry("1.1.1.1", 80, "http", "t")])
    assert len(get_pool()) == 1
    r = client.post("/api/proxy/clear")
    assert r.status_code == 200
    assert r.get_json()["status"]["count"] == 0


def test_settings_includes_proxy(client):
    d = client.get("/api/settings").get_json()
    assert "proxy" in d


def test_post_api_proxy_bad_values_clamped(client):
    d = client.post("/api/proxy", json={"proxy": {"max_proxies": 99999, "test_workers": -5}}).get_json()
    assert d["config"]["max_proxies"] <= 2000
    assert d["config"]["test_workers"] >= 1


# --------------------------------------------------------------------- render

def test_index_renders_proxy_section(client):
    r = client.get("/")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "proxySources" in html
    assert "Прокси" in html
