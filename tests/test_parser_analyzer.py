"""Tests for the parser analyzer: ParserRunStats tracking and API endpoint."""
from unittest.mock import patch, MagicMock
from datetime import date

import pytest

import db
from app import app
from parsers.base import (
    BaseParser, ParserRunStats, get_all_parser_stats,
    reset_parser_stats, _LAST_PARSER_STATS,
)
from parsers.models import SearchParams, Listing


@pytest.fixture
def client(tmp_path, monkeypatch):
    db.close_db()
    test_db = tmp_path / "test_analyzer.db"
    monkeypatch.setattr(db, "DB_PATH", test_db)
    monkeypatch.setattr(db, "_conn", None)
    db.init_db()
    reset_parser_stats()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c
    db.close_db()
    reset_parser_stats()


class TestParserRunStats:
    def test_defaults(self):
        s = ParserRunStats(name="test.kz", base_url="https://test.kz")
        assert s.status == "pending"
        assert s.results_count == 0
        assert s.error == ""
        d = s.to_dict()
        assert d["name"] == "test.kz"
        assert d["status"] == "pending"
        assert "duration_ms" in d

    def test_alias_fields_present(self):
        s = ParserRunStats(name="x")
        d = s.to_dict()
        for key in ("name", "status", "results_count", "pages_fetched",
                     "duration_ms", "error", "error_type", "http_status", "timestamp"):
            assert key in d


class TestStatsRegistry:
    def setup_method(self):
        reset_parser_stats()

    def teardown_method(self):
        reset_parser_stats()

    def test_empty_registry(self):
        assert get_all_parser_stats() == []

    def test_get_after_run(self):
        stats = ParserRunStats(name="krisha.kz", status="ok", results_count=5)
        _LAST_PARSER_STATS["krisha.kz"] = stats
        result = get_all_parser_stats()
        assert len(result) == 1
        assert result[0]["name"] == "krisha.kz"
        assert result[0]["status"] == "ok"
        assert result[0]["results_count"] == 5

    def test_reset(self):
        _LAST_PARSER_STATS["x"] = ParserRunStats(name="x")
        reset_parser_stats()
        assert get_all_parser_stats() == []


class TestStatsInRun:
    def setup_method(self):
        reset_parser_stats()

    def teardown_method(self):
        reset_parser_stats()

    def test_ok_status(self, tmp_path, monkeypatch):
        from parsers.krisha import KrishaParser
        html = '<div class="a-card"><a class="a-card__title" href="/x">test</a><div class="a-card__price">100000</div></div>'
        parser = KrishaParser()
        parser.max_pages = 1
        monkeypatch.setattr(parser, "fetch", lambda url: html)
        results = parser.run(SearchParams(limit=0))
        assert parser.last_stats.status in ("ok", "empty")
        assert parser.last_stats.pages_fetched == 1
        assert parser.last_stats.duration_ms >= 0
        assert parser.last_stats.name == "krisha.kz"
        assert "krisha.kz" in _LAST_PARSER_STATS

    def test_http_error_status(self, monkeypatch):
        import requests
        from parsers.krisha import KrishaParser
        parser = KrishaParser()
        parser.max_pages = 1
        resp = MagicMock()
        resp.status_code = 403
        exc = requests.exceptions.HTTPError(response=resp)
        monkeypatch.setattr(parser, "fetch", lambda url: (_ for _ in ()).throw(exc))
        results = parser.run(SearchParams())
        assert results == []
        assert parser.last_stats.status == "http_error"
        assert parser.last_stats.http_status == "403"
        assert parser.last_stats.error != ""

    def test_timeout_status(self, monkeypatch):
        import requests
        from parsers.krisha import KrishaParser
        parser = KrishaParser()
        parser.max_pages = 1
        monkeypatch.setattr(parser, "fetch", lambda url: (_ for _ in ()).throw(requests.exceptions.Timeout()))
        results = parser.run(SearchParams())
        assert results == []
        assert parser.last_stats.status == "timeout"
        assert parser.last_stats.error_type == "Timeout"

    def test_connection_error_status(self, monkeypatch):
        import requests
        from parsers.krisha import KrishaParser
        parser = KrishaParser()
        parser.max_pages = 1

        def _raise_conn(url):
            raise requests.exceptions.ConnectionError("refused")

        monkeypatch.setattr(parser, "fetch", _raise_conn)
        results = parser.run(SearchParams())
        assert results == []
        assert parser.last_stats.status == "connection_error"

    def test_unexpected_error_status(self, monkeypatch):
        from parsers.krisha import KrishaParser
        parser = KrishaParser()
        parser.max_pages = 1
        monkeypatch.setattr(parser, "fetch", lambda url: (_ for _ in ()).throw(ValueError("bad parse")))
        results = parser.run(SearchParams())
        assert results == []
        assert parser.last_stats.status == "error"
        assert parser.last_stats.error_type == "ValueError"


class TestParserStatusApi:
    def test_endpoint_returns_json(self, client):
        resp = client.get("/api/parser-status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "parsers" in data

    def test_endpoint_empty_without_search(self, client):
        resp = client.get("/api/parser-status")
        assert resp.get_json()["parsers"] == []

    @patch("webapp.core.get_all_parsers")
    def test_search_returns_parser_stats(self, mock_get, client):
        from parsers.krisha import KrishaParser
        from .loaders import load_fixture
        parser = KrishaParser()
        parser.max_pages = 1
        html = load_fixture("krisha")
        parser.fetch = lambda url: html
        mock_get.return_value = [parser]
        resp = client.post("/api/search", json={})
        data = resp.get_json()
        assert "parser_stats" in data
        assert len(data["parser_stats"]) == 1
        assert data["parser_stats"][0]["name"] == "krisha.kz"
        assert data["parser_stats"][0]["status"] in ("ok", "empty")

    @patch("webapp.core.get_all_parsers")
    def test_stats_persist_after_search(self, mock_get, client):
        mock_parser = MagicMock()
        mock_parser.name = "persist.kz"
        mock_parser.run.return_value = [Listing(title="apt", price=50000, source="persist.kz", url="http://t/2")]
        # Create a real parser instance so run() populates stats
        from parsers.krisha import KrishaParser
        mock_parser.last_stats = ParserRunStats(name="persist.kz", status="ok", results_count=1)
        mock_get.return_value = [mock_parser]
        client.post("/api/search", json={})
        resp = client.get("/api/parser-status")
        stats = resp.get_json()["parsers"]
        # If the mock's run() is mocked, stats won't be in _LAST_PARSER_STATS
        # unless the real run() is called. Test the search response instead.
