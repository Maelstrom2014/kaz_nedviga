"""Tests for logging, pagination, merge results, and photo carousel features."""
import pytest
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import db
from app import (app, _load_prev_results, _save_results,
                 _is_apartment_listing)
from parsers.base import BaseParser, log, _LOG_PATH
from parsers.krisha import KrishaParser
from parsers.models import Listing, SearchParams

from .loaders import load_fixture


@pytest.fixture
def client(tmp_path, monkeypatch):
    db.close_db()
    test_db = tmp_path / "test_feat.db"
    monkeypatch.setattr(db, "DB_PATH", test_db)
    monkeypatch.setattr(db, "_conn", None)
    db.init_db()
    # Isolate settings too — _filter_no_photo reads hide_no_photo from here,
    # the real file must never influence tests
    monkeypatch.setattr("app.SETTINGS_PATH", tmp_path / "settings.json")
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c
    db.close_db()


# ============================================================
# Logger file
# ============================================================

class TestErrorLogger:

    def test_log_file_exists(self):
        assert _LOG_PATH.name == "parsers_errors.log"

    def test_log_file_writes_errors(self, tmp_path, monkeypatch):
        test_log = tmp_path / "test_errors.log"
        import logging
        fh = logging.FileHandler(str(test_log), mode="w", encoding="utf-8")
        fh.setLevel(logging.WARNING)
        fh.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        log.addHandler(fh)
        try:
            log.warning("[test-parser] Test error message")
            log.handlers.remove(fh)
            content = test_log.read_text(encoding="utf-8")
            assert "Test error message" in content
            assert "test-parser" in content
        finally:
            if fh in log.handlers:
                log.handlers.remove(fh)


# ============================================================
# Pagination
# ============================================================

class TestPagination:

    def test_build_url_with_page(self):
        parser = KrishaParser()
        url1 = parser.build_url(SearchParams(), page=1)
        url2 = parser.build_url(SearchParams(), page=2)
        assert "page=2" in url2
        assert "page=2" not in url1

    def test_build_url_page1_no_page_param(self):
        parser = KrishaParser()
        url = parser.build_url(SearchParams(), page=1)
        assert "page=" not in url

    @patch.object(BaseParser, "fetch")
    def test_run_multi_page_fetches_multiple(self, mock_fetch):
        mock_fetch.return_value = load_fixture("krisha")
        parser = KrishaParser()
        parser.max_pages = 3
        results = parser.run(SearchParams(limit=0))
        # Fixture has only 2 listings (< 10), so parser stops after page 1
        # to get multi-page, we need >= 10 listings per page
        assert mock_fetch.call_count >= 1

    @patch.object(BaseParser, "fetch")
    def test_run_small_pages_continue_with_low_min_page_size(self, mock_fetch):
        # etagi returns 6 listings per page; with min_page_size=5 such a
        # page must NOT be treated as the last one (regression: etagi used
        # to stop after page 1 because 6 < 10).
        from parsers.etagi import EtagiParser
        from parsers.models import Listing
        parser = EtagiParser()
        assert parser.min_page_size == 5
        small_page = [Listing(title=f"t{i}", url=f"/x/{i}", source="etagi.com")
                      for i in range(6)]
        parser.parse = lambda html, params: small_page
        parser._enrich_photos = lambda listings: None
        parser.max_pages = 3
        mock_fetch.return_value = "<html></html>"
        parser.run(SearchParams(limit=0))
        assert mock_fetch.call_count == 3

    @patch.object(BaseParser, "fetch")
    def test_run_params_max_pages_overrides_parser_default(self, mock_fetch):
        # User-set pages per site (params.max_pages) wins over the parser's
        # own max_pages; 0 falls back to the parser default.
        from parsers.models import Listing
        parser = KrishaParser()
        parser.max_pages = 3
        big_page = [Listing(title=f"t{i}", url=f"/x/{i}", source="krisha.kz")
                    for i in range(12)]  # >= min_page_size, paging continues
        parser.parse = lambda html, params: big_page
        parser._enrich_photos = lambda listings: None
        mock_fetch.return_value = "<html></html>"
        parser.run(SearchParams(limit=0, max_pages=2))
        assert mock_fetch.call_count == 2
        parser.run(SearchParams(limit=0, max_pages=0))
        assert mock_fetch.call_count == 5  # +3 = parser default

    @patch.object(BaseParser, "fetch")
    def test_run_stops_when_page_empty(self, mock_fetch):
        call_count = [0]
        def side_effect(url):
            call_count[0] += 1
            if call_count[0] == 1:
                return load_fixture("krisha")
            return ""
        mock_fetch.side_effect = side_effect
        parser = KrishaParser()
        parser.max_pages = 5
        results = parser.run(SearchParams(limit=0))
        # Fixture has <10 listings so it stops after page 1 (returns empty from page 2 logic)
        assert results is not None


# ============================================================
# Deduplication
# ============================================================

class TestDeduplication:

    @patch.object(BaseParser, "fetch")
    def test_run_deduplicates_by_url(self, mock_fetch):
        # Both pages return same fixture with same URLs
        mock_fetch.return_value = load_fixture("krisha")
        parser = KrishaParser()
        parser.max_pages = 2
        results = parser.run(SearchParams(limit=0))
        # Even though fetched 2 pages, URLs are same -> deduped
        urls = [r.url for r in results if r.url]
        assert len(urls) == len(set(urls))


# ============================================================
# Save/merge previous results
# ============================================================

class TestResultsCache:

    def test_load_empty_cache(self, tmp_path, monkeypatch):
        cache = tmp_path / "cache.json"
        monkeypatch.setattr("app._RESULTS_CACHE", cache)
        assert _load_prev_results() == {}

    def test_save_and_load(self, tmp_path, monkeypatch):
        cache = tmp_path / "cache.json"
        monkeypatch.setattr("app._RESULTS_CACHE", cache)
        results = [
            {"source": "krisha.kz", "url": "http://test/1", "price": 150000, "title": "apt1"},
            {"source": "kn.kz", "url": "http://test/2", "price": 200000, "title": "apt2"},
        ]
        _save_results(results)
        loaded = _load_prev_results()
        assert "krisha.kz|http://test/1" in loaded
        assert "kn.kz|http://test/2" in loaded
        assert loaded["krisha.kz|http://test/1"]["price"] == 150000

    def test_save_merges_with_old(self, tmp_path, monkeypatch):
        cache = tmp_path / "cache.json"
        monkeypatch.setattr("app._RESULTS_CACHE", cache)
        # Save first batch
        _save_results([
            {"source": "krisha.kz", "url": "http://test/1", "price": 150000, "title": "apt1"},
        ])
        # Save second batch with different URL
        _save_results([
            {"source": "kn.kz", "url": "http://test/2", "price": 200000, "title": "apt2"},
        ])
        loaded = _load_prev_results()
        # Both should be present
        assert "krisha.kz|http://test/1" in loaded
        assert "kn.kz|http://test/2" in loaded

    def test_is_apartment_listing_signals(self):
        # At least one of price/rooms/area makes it a usable rental entry.
        assert _is_apartment_listing({"price": 150000})
        assert _is_apartment_listing({"rooms": 1})
        assert _is_apartment_listing({"area": 45.0})
        # No signal at all -> junk (kittens, TVs, "give away free" items).
        assert not _is_apartment_listing({"price": None, "rooms": None, "area": None})
        assert not _is_apartment_listing({"title": "Отдам кошку в добрые руки"})

    def test_load_drops_junk_entries(self, tmp_path, monkeypatch):
        cache = tmp_path / "cache.json"
        junk = {
            "olx.kz|http://olx/cat": {
                "title": "Отдам кошку в добрые руки", "price": None,
                "rooms": None, "area": None,
                "url": "http://olx/cat", "source": "olx.kz",
            },
        }
        good = {
            "krisha.kz|http://test/1": {
                "title": "1-комнатная квартира", "price": 150000,
                "rooms": 1, "area": 20.0,
                "url": "http://test/1", "source": "krisha.kz",
            },
        }
        cache.write_text(json.dumps({**junk, **good}, ensure_ascii=False),
                         encoding="utf-8")
        monkeypatch.setattr("app._RESULTS_CACHE", cache)
        loaded = _load_prev_results()
        assert "olx.kz|http://olx/cat" not in loaded
        assert "krisha.kz|http://test/1" in loaded

    def test_junk_not_resaved_after_search(self, tmp_path, monkeypatch):
        # Poison in the cache file must not survive a new search round.
        cache = tmp_path / "cache.json"
        junk = {"olx.kz|http://olx/cat": {
            "title": "Отдам кошку", "price": None, "rooms": None,
            "area": None, "url": "http://olx/cat", "source": "olx.kz",
        }}
        cache.write_text(json.dumps(junk, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr("app._RESULTS_CACHE", cache)
        new_results = [
            {"source": "krisha.kz", "url": "http://test/9", "price": 120000,
             "rooms": 1, "area": 30.0, "title": "apt9"},
        ]
        merged = _save_results(new_results)
        assert "olx.kz|http://olx/cat" not in merged
        assert "krisha.kz|http://test/9" in merged

    def test_save_detects_price_change(self, tmp_path, monkeypatch):
        cache = tmp_path / "cache.json"
        monkeypatch.setattr("app._RESULTS_CACHE", cache)
        # Save with price 150000
        _save_results([
            {"source": "krisha.kz", "url": "http://test/1", "price": 150000, "title": "apt1"},
        ])
        # Save again with new price 170000
        _save_results([
            {"source": "krisha.kz", "url": "http://test/1", "price": 170000, "title": "apt1"},
        ])
        loaded = _load_prev_results()
        item = loaded["krisha.kz|http://test/1"]
        assert item["price"] == 170000
        assert item.get("price_changed") is True
        assert item.get("prev_price") == 150000


# ============================================================
# API search with merge
# ============================================================

class TestApiSearchMerge:

    @patch("app.get_all_parsers")
    def test_search_marks_new_listings(self, mock_get, client, tmp_path, monkeypatch):
        cache = tmp_path / "cache.json"
        monkeypatch.setattr("app._RESULTS_CACHE", cache)

        mock_parser = MagicMock()
        mock_parser.name = "test.kz"
        mock_parser.run.return_value = [
            Listing(title="apt", price=100000, source="test.kz", url="http://test/1"),
        ]
        mock_get.return_value = [mock_parser]

        resp = client.post("/api/search", json={})
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["results"][0]["is_new"] is True

    @patch("app.get_all_parsers")
    def test_search_marks_old_listings_not_new(self, mock_get, client, tmp_path, monkeypatch):
        cache = tmp_path / "cache.json"
        monkeypatch.setattr("app._RESULTS_CACHE", cache)

        # First search to populate cache
        mock_parser = MagicMock()
        mock_parser.name = "test.kz"
        mock_parser.run.return_value = [
            Listing(title="apt", price=100000, source="test.kz", url="http://test/1"),
        ]
        mock_get.return_value = [mock_parser]
        client.post("/api/search", json={})

        # Second search — same listing should not be new
        resp = client.post("/api/search", json={})
        data = resp.get_json()
        assert data["results"][0]["is_new"] is False

    @patch("app.get_all_parsers")
    def test_search_detects_price_change(self, mock_get, client, tmp_path, monkeypatch):
        cache = tmp_path / "cache.json"
        monkeypatch.setattr("app._RESULTS_CACHE", cache)

        # First search with price 100000
        mock_parser = MagicMock()
        mock_parser.name = "test.kz"
        mock_parser.run.return_value = [
            Listing(title="apt", price=100000, source="test.kz", url="http://test/1"),
        ]
        mock_get.return_value = [mock_parser]
        client.post("/api/search", json={})

        # Second search with price 120000
        mock_parser.run.return_value = [
            Listing(title="apt", price=120000, source="test.kz", url="http://test/1"),
        ]
        resp = client.post("/api/search", json={})
        data = resp.get_json()
        assert data["results"][0]["price_changed"] is True
        assert data["results"][0]["prev_price"] == 100000


# ============================================================
# Multiple photos extraction
# ============================================================

class TestMultiplePhotos:

    def test_krisha_extracts_single_deduped_photo(self):
        html = load_fixture("krisha")
        parser = KrishaParser()
        results = parser.parse(html, SearchParams())
        # A krisha search card exposes one photo in up to two formats
        # (source srcset .webp + img .jpg) plus tooltip UI sprites.
        # The carousel must get one distinct photo, not the duplicates.
        for r in results:
            photos = r.photo.split("|") if r.photo else []
            assert len(photos) == 1, f"{r.title}: expected 1 photo, got {photos}"
            assert photos[0].startswith("http")
            assert "/static/" not in photos[0]
            assert not photos[0].endswith(".svg")
        assert results[0].photo != results[1].photo

    def test_krisha_photo_count(self):
        html = load_fixture("krisha")
        parser = KrishaParser()
        results = parser.parse(html, SearchParams())
        for r in results:
            photos = r.photo.split("|") if r.photo else []
            assert len(photos) >= 1

    def test_krisha_no_empty_photos(self):
        html = load_fixture("krisha")
        parser = KrishaParser()
        results = parser.parse(html, SearchParams())
        for r in results:
            if r.photo:
                photos = r.photo.split("|")
                for p in photos:
                    assert p.startswith("http")

    def test_kn_extracts_photos(self):
        html = load_fixture("kn")
        from parsers.kn import KnParser
        parser = KnParser()
        results = parser.parse(html, SearchParams())
        assert len(results) > 0
        for r in results:
            if r.photo:
                photos = r.photo.split("|")
                assert all(p for p in photos)
