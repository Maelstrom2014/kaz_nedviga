"""Tests for favorites API endpoints."""
import pytest
from unittest.mock import patch, MagicMock

from app import app, _compute_price_change
import db

from parsers.models import Listing


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Fresh Flask test client with isolated DB."""
    db.close_db()
    test_db = tmp_path / "test_api_fav.db"
    monkeypatch.setattr(db, "DB_PATH", test_db)
    monkeypatch.setattr(db, "_conn", None)
    db.init_db()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client
    db.close_db()


SAMPLE = {
    "title": "2-комнатная квартира, 65 м²",
    "price": 250000,
    "currency": "тг",
    "rooms": 2,
    "area": 65.0,
    "floor": 5,
    "total_floors": 9,
    "address": "Бостандыкский, мкр. 5",
    "url": "https://krisha.kz/arenda/123",
    "source": "krisha.kz",
}


class TestApiAddFavorite:

    def test_add_favorite_success(self, client):
        resp = client.post("/api/favorites", json=SAMPLE)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["already_exists"] is False
        assert data["favorite"]["title"] == SAMPLE["title"]

    def test_add_favorite_missing_url(self, client):
        data = {"title": "test", "source": "test.kz"}
        resp = client.post("/api/favorites", json=data)
        assert resp.status_code == 400

    def test_add_favorite_missing_source(self, client):
        data = {"title": "test", "url": "http://test"}
        resp = client.post("/api/favorites", json=data)
        assert resp.status_code == 400

    def test_add_favorite_missing_title(self, client):
        data = {"url": "http://t", "source": "t.kz"}
        resp = client.post("/api/favorites", json=data)
        assert resp.status_code == 400

    def test_add_duplicate_returns_already_exists(self, client):
        client.post("/api/favorites", json=SAMPLE)
        resp = client.post("/api/favorites", json=SAMPLE)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["already_exists"] is True


class TestApiListFavorites:

    def test_empty_list(self, client):
        resp = client.get("/api/favorites")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] == 0
        assert data["favorites"] == []

    def test_list_with_items(self, client):
        client.post("/api/favorites", json=SAMPLE)
        client.post("/api/favorites", json={**SAMPLE, "url": "https://test2.com", "title": "apt2"})
        resp = client.get("/api/favorites")
        data = resp.get_json()
        assert data["total"] == 2

    def test_includes_price_str(self, client):
        client.post("/api/favorites", json=SAMPLE)
        resp = client.get("/api/favorites")
        data = resp.get_json()
        assert data["favorites"][0]["price_str"] == "250 000 тг"

    def test_includes_price_history(self, client):
        client.post("/api/favorites", json=SAMPLE)
        resp = client.get("/api/favorites")
        data = resp.get_json()
        assert "price_history" in data["favorites"][0]
        assert len(data["favorites"][0]["price_history"]) == 1

    def test_stores_and_returns_lat_lon(self, client):
        payload = {**SAMPLE, "lat": 43.25, "lon": 76.95}
        client.post("/api/favorites", json=payload)
        resp = client.get("/api/favorites")
        fav = resp.get_json()["favorites"][0]
        assert fav["lat"] == 43.25
        assert fav["lon"] == 76.95

    def test_lat_lon_none_when_not_provided(self, client):
        client.post("/api/favorites", json=SAMPLE)
        resp = client.get("/api/favorites")
        fav = resp.get_json()["favorites"][0]
        assert fav["lat"] is None
        assert fav["lon"] is None


class TestApiDeleteFavorite:

    def test_delete_existing(self, client):
        client.post("/api/favorites", json=SAMPLE)
        resp = client.get("/api/favorites")
        key = resp.get_json()["favorites"][0]["listing_key"]

        del_resp = client.delete(f"/api/favorites/{key}")
        assert del_resp.status_code == 200
        assert del_resp.get_json()["ok"] is True

    def test_delete_nonexistent(self, client):
        resp = client.delete("/api/favorites/nonexistent_key")
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is False

    def test_list_after_delete(self, client):
        client.post("/api/favorites", json=SAMPLE)
        resp = client.get("/api/favorites")
        key = resp.get_json()["favorites"][0]["listing_key"]
        client.delete(f"/api/favorites/{key}")
        resp2 = client.get("/api/favorites")
        assert resp2.get_json()["total"] == 0


class TestApiRateFavorite:

    def test_rate_success(self, client):
        client.post("/api/favorites", json=SAMPLE)
        key = client.get("/api/favorites").get_json()["favorites"][0]["listing_key"]
        resp = client.put(f"/api/favorites/{key}/rate", json={"rating": 4})
        assert resp.status_code == 200
        assert resp.get_json()["favorite"]["rating"] == 4

    def test_rate_zero(self, client):
        client.post("/api/favorites", json=SAMPLE)
        key = client.get("/api/favorites").get_json()["favorites"][0]["listing_key"]
        resp = client.put(f"/api/favorites/{key}/rate", json={"rating": 0})
        assert resp.status_code == 200

    def test_rate_five(self, client):
        client.post("/api/favorites", json=SAMPLE)
        key = client.get("/api/favorites").get_json()["favorites"][0]["listing_key"]
        resp = client.put(f"/api/favorites/{key}/rate", json={"rating": 5})
        assert resp.status_code == 200

    def test_rate_invalid_too_high(self, client):
        client.post("/api/favorites", json=SAMPLE)
        key = client.get("/api/favorites").get_json()["favorites"][0]["listing_key"]
        resp = client.put(f"/api/favorites/{key}/rate", json={"rating": 6})
        assert resp.status_code == 400

    def test_rate_invalid_negative(self, client):
        client.post("/api/favorites", json=SAMPLE)
        key = client.get("/api/favorites").get_json()["favorites"][0]["listing_key"]
        resp = client.put(f"/api/favorites/{key}/rate", json={"rating": -1})
        assert resp.status_code == 400

    def test_rate_nonexistent(self, client):
        resp = client.put("/api/favorites/nonexistent/rate", json={"rating": 3})
        assert resp.status_code == 404

    def test_rate_invalid_type(self, client):
        client.post("/api/favorites", json=SAMPLE)
        key = client.get("/api/favorites").get_json()["favorites"][0]["listing_key"]
        resp = client.put(f"/api/favorites/{key}/rate", json={"rating": "abc"})
        assert resp.status_code == 400


class TestApiCommentFavorite:

    def test_comment_success(self, client):
        client.post("/api/favorites", json=SAMPLE)
        key = client.get("/api/favorites").get_json()["favorites"][0]["listing_key"]
        resp = client.put(f"/api/favorites/{key}/comment", json={"comment": "Good"})
        assert resp.status_code == 200
        assert resp.get_json()["favorite"]["comment"] == "Good"

    def test_empty_comment(self, client):
        client.post("/api/favorites", json=SAMPLE)
        key = client.get("/api/favorites").get_json()["favorites"][0]["listing_key"]
        resp = client.put(f"/api/favorites/{key}/comment", json={"comment": ""})
        assert resp.status_code == 200

    def test_comment_nonexistent(self, client):
        resp = client.put("/api/favorites/nonexistent/comment", json={"comment": "test"})
        assert resp.status_code == 404


class TestApiPriceHistory:

    def test_history_single_entry(self, client):
        client.post("/api/favorites", json=SAMPLE)
        key = client.get("/api/favorites").get_json()["favorites"][0]["listing_key"]
        resp = client.get(f"/api/favorites/{key}/history")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["history"]) == 1
        assert data["history"][0]["price"] == 250000

    def test_history_nonexistent(self, client):
        resp = client.get("/api/favorites/nonexistent/history")
        assert resp.status_code == 404

    def test_history_price_change_none_single_entry(self, client):
        client.post("/api/favorites", json=SAMPLE)
        key = client.get("/api/favorites").get_json()["favorites"][0]["listing_key"]
        resp = client.get(f"/api/favorites/{key}/history")
        assert resp.get_json()["price_change"] is None


class TestComputePriceChange:

    def test_empty_history(self):
        assert _compute_price_change([]) is None

    def test_single_entry(self):
        assert _compute_price_change([{"price": 100000}]) is None

    def test_two_entries_increase(self):
        history = [{"price": 100000}, {"price": 150000}]
        change = _compute_price_change(history)
        assert change["old_price"] == 100000
        assert change["new_price"] == 150000
        assert change["diff"] == 50000
        assert change["direction"] == "up"
        assert change["percent"] == 50.0

    def test_two_entries_decrease(self):
        history = [{"price": 200000}, {"price": 150000}]
        change = _compute_price_change(history)
        assert change["diff"] == -50000
        assert change["direction"] == "down"

    def test_two_entries_stable(self):
        history = [{"price": 100000}, {"price": 100000}]
        change = _compute_price_change(history)
        assert change["diff"] == 0
        assert change["direction"] == "stable"

    def test_multiple_entries(self):
        history = [
            {"price": 100000},
            {"price": 120000},
            {"price": 110000},
            {"price": 130000},
        ]
        change = _compute_price_change(history)
        assert change["old_price"] == 100000
        assert change["new_price"] == 130000
        assert change["diff"] == 30000

    def test_none_prices_skipped(self):
        history = [{"price": None}, {"price": 100000}, {"price": 150000}]
        change = _compute_price_change(history)
        assert change["old_price"] == 100000
        assert change["new_price"] == 150000


class TestApiCheckPrices:

    @patch("app.db_check_prices")
    def test_check_prices_returns_results(self, mock_check, client):
        mock_check.return_value = [
            {"listing_key": "k1", "title": "apt1", "old_price": 100000, "new_price": 110000, "changed": True},
            {"listing_key": "k2", "title": "apt2", "old_price": 200000, "new_price": 200000, "changed": False},
        ]
        resp = client.post("/api/favorites/check-prices")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["checked"] == 2
        assert data["changed_count"] == 1

    @patch("app.db_check_prices")
    def test_check_prices_empty(self, mock_check, client):
        mock_check.return_value = []
        resp = client.post("/api/favorites/check-prices")
        assert resp.status_code == 200
        assert resp.get_json()["checked"] == 0

    def test_check_prices_olx_unavailable(self, client):
        """OLX listing showing 'Объявление больше не доступно' is marked
        unavailable without attempting to parse the page."""
        from db import check_prices
        from parsers.olx import OlxParser

        fav = {
            **SAMPLE,
            "url": "https://www.olx.kz/d/obyavlenie/deleted-ad/",
            "source": "olx.kz",
        }
        db.add_favorite(fav)
        parser = OlxParser()
        with patch.object(parser, "fetch",
                          return_value="<html>Объявление больше не доступно</html>"):
            with patch("parsers.factory.get_all_parsers", return_value=[parser]):
                results = check_prices()
        assert len(results) == 1
        r = results[0]
        assert r["error"] == "объявление больше не доступно"
        assert r["new_price"] is None
        assert r["changed"] is False

    def test_check_prices_olx_unavailable_not_parsed(self, client):
        """When OLX returns the 'недоступно' page, extract_detail_price
        must NOT be called."""
        from db import check_prices
        from parsers.olx import OlxParser

        db.add_favorite({
            **SAMPLE,
            "url": "https://www.olx.kz/d/obyavlenie/deleted-ad/",
            "source": "olx.kz",
        })
        parser = OlxParser()
        with patch.object(parser, "fetch",
                          return_value="<html>Объявление больше не доступно</html>"):
            with patch.object(parser, "extract_detail_price") as mock_extract, \
                 patch("parsers.factory.get_all_parsers", return_value=[parser]):
                check_prices()
        mock_extract.assert_not_called()

    def test_check_prices_extracts_price_from_detail_page(self, client):
        """check_prices uses extract_detail_price(), not parse() — the detail
        page has a different structure than search cards."""
        from db import check_prices
        from parsers.krisha import KrishaParser

        db.add_favorite({
            **SAMPLE,
            "url": "https://krisha.kz/a/show/123456",
            "source": "krisha.kz",
        })
        parser = KrishaParser()
        detail_html = '<html><script id="jsdata">{"price":260000}</script></html>'
        with patch.object(parser, "fetch", return_value=detail_html), \
             patch.object(parser, "extract_detail_price", return_value=(260000, None, None)) as mock_extract, \
             patch.object(parser, "parse") as mock_parse, \
             patch("parsers.factory.get_all_parsers", return_value=[parser]):
            results = check_prices()
        assert len(results) == 1
        assert results[0]["new_price"] == 260000
        assert results[0]["changed"] is True  # 250000 -> 260000
        mock_extract.assert_called_once()
        mock_parse.assert_not_called()
