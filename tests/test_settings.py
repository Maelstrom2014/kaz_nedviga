"""Tests for settings API: theme persistence, DB reset, favorites clearing."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

import db
from app import app, load_settings, save_settings, SETTINGS_PATH, THEMES
from parsers.models import Listing

from .loaders import load_fixture


@pytest.fixture
def client(tmp_path, monkeypatch):
    db.close_db()
    test_db = tmp_path / "test_settings.db"
    monkeypatch.setattr(db, "DB_PATH", test_db)
    monkeypatch.setattr(db, "_conn", None)
    db.init_db()
    monkeypatch.setattr("webapp.core.SETTINGS_PATH", tmp_path / "settings.json")
    # Isolate the results cache so /api/database/reset and /api/search
    # can never touch the real data/last_results.json
    monkeypatch.setattr("webapp.core._RESULTS_CACHE", tmp_path / "last_results.json")
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c
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


class TestThemeApi:

    def test_get_settings_default(self, client):
        resp = client.get("/api/settings")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["theme"] == "midnight"

    @pytest.mark.parametrize("theme", THEMES)
    def test_set_each_theme(self, client, theme):
        resp = client.post("/api/settings", json={"theme": theme})
        assert resp.status_code == 200
        assert resp.get_json()["theme"] == theme
        # persisted
        assert load_settings()["theme"] == theme

    def test_set_invalid_theme(self, client):
        resp = client.post("/api/settings", json={"theme": "nope"})
        assert resp.status_code == 400

    def test_theme_survives_restart(self, client):
        client.post("/api/settings", json={"theme": "forest"})
        # reload from disk simulates a server restart
        assert load_settings()["theme"] == "forest"

    def test_index_injects_theme(self, client):
        client.post("/api/settings", json={"theme": "sand"})
        resp = client.get("/")
        assert resp.status_code == 200
        assert b'data-theme' in resp.data

    def test_corrupt_settings_falls_back(self, client, tmp_path):
        bad = tmp_path / "settings.json"
        bad.write_text("not json", encoding="utf-8")
        from app import load_settings as _ls
        import app as _app
        old = _app.SETTINGS_PATH
        _app.SETTINGS_PATH = bad
        try:
            assert _ls()["theme"] == "midnight"
        finally:
            _app.SETTINGS_PATH = old


class TestClearFavorites:

    def test_clear_empty(self, client):
        resp = client.post("/api/favorites/clear")
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
        assert resp.get_json()["removed"] == 0

    def test_clear_removes_all(self, client):
        client.post("/api/favorites", json=SAMPLE)
        client.post("/api/favorites", json={**SAMPLE, "url": "https://t2.com", "title": "apt2"})
        assert client.get("/api/favorites").get_json()["total"] == 2
        resp = client.post("/api/favorites/clear")
        assert resp.get_json()["removed"] == 2
        assert client.get("/api/favorites").get_json()["total"] == 0

    def test_clear_keeps_theme(self, client):
        client.post("/api/settings", json={"theme": "rose"})
        client.post("/api/favorites", json=SAMPLE)
        resp = client.post("/api/favorites/clear")
        assert resp.get_json()["theme"] == "rose"


class TestResetDatabase:

    def test_reset_returns_ok(self, client):
        client.post("/api/favorites", json=SAMPLE)
        resp = client.post("/api/database/reset")
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

    def test_reset_wipes_favorites(self, client):
        client.post("/api/favorites", json=SAMPLE)
        client.post("/api/database/reset")
        assert client.get("/api/favorites").get_json()["total"] == 0

    def test_reset_recreates_db_usable(self, client):
        client.post("/api/favorites", json=SAMPLE)
        client.post("/api/database/reset")
        # DB still accepts new favorites
        resp = client.post("/api/favorites", json={**SAMPLE, "url": "https://new.com"})
        assert resp.status_code == 200


class TestDatesStoredInFavorites:

    def test_favorite_preserves_dates(self, client):
        payload = {**SAMPLE, "date_published": "2024-08-12", "date_updated": "2024-08-13"}
        client.post("/api/favorites", json=payload)
        fav = client.get("/api/favorites").get_json()["favorites"][0]
        assert fav["date_published"] == "2024-08-12"
        assert fav["date_updated"] == "2024-08-13"

    def test_favorite_dates_default_empty(self, client):
        client.post("/api/favorites", json=SAMPLE)
        fav = client.get("/api/favorites").get_json()["favorites"][0]
        assert fav["date_published"] == ""
        assert fav["date_updated"] == ""


class TestDbClearFunctions:

    def test_clear_favorites_returns_count(self, tmp_path, monkeypatch):
        db.close_db()
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "x.db")
        monkeypatch.setattr(db, "_conn", None)
        db.init_db()
        try:
            db.add_favorite(SAMPLE)
            db.add_favorite({**SAMPLE, "url": "https://t2.com"})
            assert db.clear_favorites() == 2
            assert db.list_favorites() == []
            # idempotent
            assert db.clear_favorites() == 0
        finally:
            db.close_db()

    def test_clear_all_empties_tables(self, tmp_path, monkeypatch):
        db.close_db()
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "y.db")
        monkeypatch.setattr(db, "_conn", None)
        db.init_db()
        try:
            db.add_favorite(SAMPLE)
            db.clear_all()
            assert db.list_favorites() == []
            assert db.get_all_price_history() == {}
        finally:
            db.close_db()


class TestExportWithDates:

    def test_txt_includes_dates(self, client):
        payload = {"results": [
            {"title": "apt", "price": 150000, "source": "krisha.kz",
             "date_published": "2024-08-12", "date_updated": "2024-08-13"},
        ]}
        resp = client.post("/api/export/txt", json=payload)
        assert resp.status_code == 200
        assert b"2024-08-12" in resp.data
        assert b"2024-08-13" in resp.data


class TestSearchDefaults:

    def test_get_defaults(self, client):
        resp = client.get("/api/search-defaults")
        assert resp.status_code == 200
        sd = resp.get_json()
        assert sd["price_min"] == 100000
        assert sd["price_max"] == 300000
        assert sd["rooms"] == [0, 1]

    def test_post_defaults(self, client):
        resp = client.post("/api/search-defaults", json={
            "price_min": 50000, "price_max": 200000, "rooms": [2, 3],
            "district": "Бостандыкский", "floor_min": 2, "floor_max": 9,
            "area_min": 40, "area_max": 80
        })
        assert resp.status_code == 200
        sd = resp.get_json()["search_defaults"]
        assert sd["price_min"] == 50000
        assert sd["price_max"] == 200000
        assert sd["rooms"] == [2, 3]
        assert sd["district"] == "Бостандыкский"

    def test_defaults_persist(self, client):
        client.post("/api/search-defaults", json={"price_min": 70000, "rooms": [0]})
        sd = client.get("/api/search-defaults").get_json()
        assert sd["price_min"] == 70000
        assert sd["rooms"] == [0]

    def test_defaults_empty_values(self, client):
        resp = client.post("/api/search-defaults", json={
            "price_min": None, "price_max": None, "rooms": [],
            "district": "", "floor_min": None, "floor_max": None,
            "area_min": None, "area_max": None
        })
        assert resp.status_code == 200
        sd = resp.get_json()["search_defaults"]
        assert sd["price_min"] is None
        assert sd["rooms"] == []

    def test_defaults_full_replace(self, client):
        # Set full defaults first
        client.post("/api/search-defaults", json={
            "price_min": 100000, "price_max": 300000, "rooms": [0, 1],
            "district": "Медеуский"
        })
        # POST replaces ALL fields — omitted fields reset
        client.post("/api/search-defaults", json={"price_min": 150000})
        sd = client.get("/api/search-defaults").get_json()
        assert sd["price_min"] == 150000
        assert sd["price_max"] is None
        assert sd["rooms"] == []

    def test_index_page_contains_defaults_form(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert b'def-price_min' in resp.data
        assert b'def-rooms-0' in resp.data

    def test_defaults_in_settings_json(self, client):
        client.post("/api/search-defaults", json={"price_min": 120000, "rooms": [1]})
        settings = load_settings()
        assert "search_defaults" in settings
        assert settings["search_defaults"]["price_min"] == 120000
        assert settings["search_defaults"]["rooms"] == [1]


class _FakeNoPhotoParser:
    """Duck-typed parser: returns one listing with a photo, one without."""
    name = "fake.kz"

    def run(self, params):
        return [
            Listing(title="С фото", url="https://fake.kz/1", source="fake.kz",
                    price=200000, photo="https://fake.kz/1.jpg"),
            Listing(title="Без фото", url="https://fake.kz/2", source="fake.kz",
                    price=180000, photo=""),
        ]


class TestHideNoPhoto:

    @pytest.fixture
    def results_cache(self, tmp_path, monkeypatch):
        p = tmp_path / "last_results.json"
        monkeypatch.setattr("webapp.core._RESULTS_CACHE", p)
        return p

    def test_default_off(self, client):
        data = client.get("/api/settings").get_json()
        assert data.get("hide_no_photo") is False

    def test_set_on_and_off(self, client):
        resp = client.post("/api/settings", json={"hide_no_photo": True})
        assert resp.status_code == 200
        assert resp.get_json()["hide_no_photo"] is True
        assert load_settings()["hide_no_photo"] is True
        resp = client.post("/api/settings", json={"hide_no_photo": False})
        assert resp.get_json()["hide_no_photo"] is False
        assert load_settings()["hide_no_photo"] is False

    def test_toggle_without_theme_ok(self, client):
        # theme is optional when only the filter is toggled
        resp = client.post("/api/settings", json={"hide_no_photo": True})
        assert resp.status_code == 200
        # and theme-only POST still works
        resp = client.post("/api/settings", json={"theme": "forest"})
        assert resp.status_code == 200
        assert load_settings()["theme"] == "forest"

    def test_set_together_with_theme(self, client):
        resp = client.post("/api/settings",
                           json={"theme": "rose", "hide_no_photo": True})
        assert resp.status_code == 200
        assert load_settings()["theme"] == "rose"
        assert load_settings()["hide_no_photo"] is True

    def test_api_results_hides_no_photo(self, client, results_cache):
        results_cache.write_text(json.dumps({
            "krisha.kz|https://krisha.kz/1": {**SAMPLE, "photo": "https://x/1.jpg"},
            "krisha.kz|https://krisha.kz/2":
                {**SAMPLE, "url": "https://krisha.kz/2", "title": "b2",
                 "photo": ""},
        }), encoding="utf-8")
        assert client.get("/api/results").get_json()["total"] == 2
        client.post("/api/settings", json={"hide_no_photo": True})
        data = client.get("/api/results").get_json()
        assert data["total"] == 1
        assert data["results"][0]["photo"]
        # cache file itself is untouched — filter is reversible
        assert len(json.loads(
            results_cache.read_text(encoding="utf-8"))) == 2
        client.post("/api/settings", json={"hide_no_photo": False})
        assert client.get("/api/results").get_json()["total"] == 2

    def test_api_search_hides_no_photo(self, client, results_cache, monkeypatch):
        with patch("webapp.core.get_all_parsers",
                   return_value=[_FakeNoPhotoParser()]):
            data = client.post("/api/search", json={}).get_json()
            assert data["total"] == 2
            client.post("/api/settings", json={"hide_no_photo": True})
            data = client.post("/api/search", json={}).get_json()
            assert data["total"] == 1
            assert data["results"][0]["photo"]
        # cache still holds both entries
        assert len(json.loads(
            results_cache.read_text(encoding="utf-8"))) == 2

    def test_settings_tab_contains_toggle(self, client):
        resp = client.get("/")
        assert b'hideNoPhoto' in resp.data


class TestParserMaxPages:

    def test_defaults_present(self, client):
        data = client.get("/api/settings").get_json()
        pmp = data.get("parser_max_pages", {})
        assert pmp.get("krisha.kz") == 9
        assert pmp.get("olx.kz") == 6
        assert pmp.get("kn.kz") == 6
        assert pmp.get("telegram") == 3

    def test_set_per_parser(self, client):
        resp = client.post("/api/settings", json={
            "parser_max_pages": {"krisha.kz": 12, "olx.kz": 4}})
        assert resp.status_code == 200
        pmp = resp.get_json()["parser_max_pages"]
        assert pmp["krisha.kz"] == 12
        assert pmp["olx.kz"] == 4
        # persisted
        assert load_settings()["parser_max_pages"]["krisha.kz"] == 12

    def test_clamps_out_of_range(self, client):
        resp = client.post("/api/settings", json={
            "parser_max_pages": {"krisha.kz": 5000, "olx.kz": 0}})
        pmp = resp.get_json()["parser_max_pages"]
        # 5000 → clamped to the valid max (999); 0 → keep default
        assert pmp["krisha.kz"] == 9
        assert pmp["olx.kz"] == 6

    def test_accepts_new_max_999(self, client):
        resp = client.post("/api/settings", json={
            "parser_max_pages": {"krisha.kz": 999}})
        pmp = resp.get_json()["parser_max_pages"]
        assert pmp["krisha.kz"] == 999

    def test_rejects_non_object(self, client):
        resp = client.post("/api/settings", json={"parser_max_pages": "bad"})
        assert resp.status_code == 400

    def test_settings_tab_contains_grid(self, client):
        resp = client.get("/")
        assert b'parserMaxPagesGrid' in resp.data
        assert 'Страницы по сайтам' in resp.data.decode('utf-8')

    def test_apply_to_parsers(self, client, monkeypatch):
        from app import _apply_parser_max_pages
        from parsers.factory import get_all_parsers
        client.post("/api/settings", json={
            "parser_max_pages": {"krisha.kz": 7, "olx.kz": 5}})
        parsers = get_all_parsers()
        _apply_parser_max_pages(parsers)
        by_name = {p.name: p for p in parsers}
        assert by_name["krisha.kz"].max_pages == 7
        assert by_name["olx.kz"].max_pages == 5
        # untouched parsers keep their class default (3x)
        assert by_name["etagi.com"].max_pages == 3


class TestPhotoCacheMb:

    def test_default_present(self, client):
        data = client.get("/api/settings").get_json()
        assert data.get("photo_cache_mb") == 500

    def test_set_value(self, client):
        resp = client.post("/api/settings", json={"photo_cache_mb": 2000})
        assert resp.status_code == 200
        assert resp.get_json()["photo_cache_mb"] == 2000
        # persisted
        assert load_settings()["photo_cache_mb"] == 2000

    def test_clamps_too_small(self, client):
        resp = client.post("/api/settings", json={"photo_cache_mb": 10})
        assert resp.get_json()["photo_cache_mb"] == 50  # clamped to min

    def test_clamps_too_large(self, client):
        resp = client.post("/api/settings", json={"photo_cache_mb": 999999})
        assert resp.get_json()["photo_cache_mb"] == 10000  # clamped to max

    def test_settings_tab_contains_cache_controls(self, client):
        resp = client.get("/")
        assert b'photoCacheMbInput' in resp.data
        assert b'clearPhotoCache' in resp.data
        assert 'Кэш картинок' in resp.data.decode('utf-8')

    def test_photo_cache_status_endpoint(self, client):
        resp = client.get("/api/photo-cache")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "files" in data
        assert "size_bytes" in data
        assert "size_mb" in data
        assert "max_mb" in data

    def test_photo_cache_clear_endpoint(self, client):
        # Write a dummy file to the cache
        from export_utils import _CACHE_DIR
        dummy = _CACHE_DIR / "test_dummy.dat"
        dummy.write_bytes(b"x" * 100)
        assert dummy.exists()
        resp = client.post("/api/photo-cache/clear")
        assert resp.status_code == 200
        assert resp.get_json()["deleted"] >= 1
        assert not dummy.exists()
