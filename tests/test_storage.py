"""Tests for SQLite storage layer: favorites, ratings, comments, price history."""
import pytest
import tempfile
import os
from pathlib import Path

import db
from db import (
    add_favorite,
    get_favorite,
    get_favorite_by_id,
    list_favorites,
    update_rating,
    update_comment,
    update_price,
    update_coords,
    delete_favorite,
    get_price_history,
    listing_key,
    init_db,
    reset_db,
    close_db,
)


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Each test gets a fresh temp database."""
    close_db()
    test_db_path = tmp_path / "test_favorites.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    monkeypatch.setattr(db, "_conn", None)
    init_db()
    yield
    close_db()


SAMPLE_FAVORITE = {
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
    "phone": "+7 777 123 45 67",
    "description": "Хороший ремонт",
    "photo": "https://krisha.kz/photo.jpg",
}


class TestListingKey:

    def test_generates_hex_key(self):
        key = listing_key("https://test.com/1", "test.kz", "title")
        assert isinstance(key, str)
        assert len(key) == 16
        assert all(c in "0123456789abcdef" for c in key)

    def test_same_input_same_key(self):
        k1 = listing_key("url1", "src1", "t1")
        k2 = listing_key("url1", "src1", "t1")
        assert k1 == k2

    def test_different_input_different_key(self):
        k1 = listing_key("url1", "src1", "t1")
        k2 = listing_key("url2", "src1", "t1")
        assert k1 != k2

    def test_empty_inputs(self):
        key = listing_key("", "", "")
        assert isinstance(key, str)
        assert len(key) == 16


class TestAddFavorite:

    def test_add_returns_favorite(self):
        fav = add_favorite(SAMPLE_FAVORITE)
        assert fav["title"] == SAMPLE_FAVORITE["title"]
        assert fav["price"] == SAMPLE_FAVORITE["price"]
        assert fav["id"] is not None
        assert fav["listing_key"] is not None

    def test_add_creates_price_history(self):
        fav = add_favorite(SAMPLE_FAVORITE)
        history = get_price_history(fav["listing_key"])
        assert len(history) == 1
        assert history[0]["price"] == SAMPLE_FAVORITE["price"]

    def test_add_without_price_no_history(self):
        data = {**SAMPLE_FAVORITE, "price": None}
        fav = add_favorite(data)
        history = get_price_history(fav["listing_key"])
        assert len(history) == 0

    def test_add_with_rating(self):
        data = {**SAMPLE_FAVORITE, "rating": 4}
        fav = add_favorite(data)
        assert fav["rating"] == 4

    def test_add_with_comment(self):
        data = {**SAMPLE_FAVORITE, "comment": "Great value"}
        fav = add_favorite(data)
        assert fav["comment"] == "Great value"

    def test_add_with_minimal_data(self):
        data = {"title": "test", "url": "http://t", "source": "t.kz"}
        fav = add_favorite(data)
        assert fav["title"] == "test"
        assert fav["price"] is None

    def test_add_duplicate_raises_integrity_error(self):
        add_favorite(SAMPLE_FAVORITE)
        # UNIQUE constraint on listing_key prevents duplicates;
        # the API checks for existing favorites before calling add_favorite
        try:
            add_favorite(SAMPLE_FAVORITE)
            assert False, "Should raise IntegrityError"
        except Exception as exc:
            assert "UNIQUE" in str(exc) or "constraint" in str(exc).lower()


class TestGetFavorite:

    def test_get_existing(self):
        added = add_favorite(SAMPLE_FAVORITE)
        got = get_favorite(added["listing_key"])
        assert got is not None
        assert got["title"] == SAMPLE_FAVORITE["title"]

    def test_get_nonexistent(self):
        assert get_favorite("nonexistent_key") is None

    def test_get_by_id(self):
        added = add_favorite(SAMPLE_FAVORITE)
        got = get_favorite_by_id(added["id"])
        assert got is not None
        assert got["title"] == SAMPLE_FAVORITE["title"]

    def test_get_by_id_nonexistent(self):
        assert get_favorite_by_id(99999) is None


class TestListFavorites:

    def test_empty_list(self):
        assert list_favorites() == []

    def test_list_one(self):
        add_favorite(SAMPLE_FAVORITE)
        favs = list_favorites()
        assert len(favs) == 1

    def test_list_multiple(self):
        for i in range(5):
            data = {**SAMPLE_FAVORITE, "url": f"https://test.com/{i}", "title": f"apt{i}"}
            add_favorite(data)
        favs = list_favorites()
        assert len(favs) == 5

    def test_list_sorted_by_rating(self):
        add_favorite({**SAMPLE_FAVORITE, "url": "u1", "rating": 2})
        add_favorite({**SAMPLE_FAVORITE, "url": "u2", "rating": 5})
        add_favorite({**SAMPLE_FAVORITE, "url": "u3", "rating": 1})
        favs = list_favorites()
        assert favs[0]["rating"] == 5
        assert favs[1]["rating"] == 2
        assert favs[2]["rating"] == 1


class TestUpdateRating:

    def test_update_rating(self):
        fav = add_favorite(SAMPLE_FAVORITE)
        updated = update_rating(fav["listing_key"], 4)
        assert updated["rating"] == 4

    def test_rating_zero(self):
        fav = add_favorite(SAMPLE_FAVORITE)
        updated = update_rating(fav["listing_key"], 0)
        assert updated["rating"] == 0

    def test_rating_five(self):
        fav = add_favorite(SAMPLE_FAVORITE)
        updated = update_rating(fav["listing_key"], 5)
        assert updated["rating"] == 5

    def test_rating_invalid_negative(self):
        fav = add_favorite(SAMPLE_FAVORITE)
        try:
            update_rating(fav["listing_key"], -1)
            assert False, "Should raise"
        except ValueError:
            pass

    def test_rating_invalid_too_high(self):
        fav = add_favorite(SAMPLE_FAVORITE)
        try:
            update_rating(fav["listing_key"], 6)
            assert False, "Should raise"
        except ValueError:
            pass

    def test_rating_nonexistent_returns_none(self):
        result = update_rating("nonexistent", 3)
        assert result is None


class TestUpdateComment:

    def test_update_comment(self):
        fav = add_favorite(SAMPLE_FAVORITE)
        updated = update_comment(fav["listing_key"], "Good apartment")
        assert updated["comment"] == "Good apartment"

    def test_update_empty_comment(self):
        fav = add_favorite(SAMPLE_FAVORITE)
        updated = update_comment(fav["listing_key"], "")
        assert updated["comment"] == ""

    def test_update_long_comment(self):
        fav = add_favorite(SAMPLE_FAVORITE)
        long_comment = "Очень " * 100 + "длинный комментарий"
        updated = update_comment(fav["listing_key"], long_comment)
        assert long_comment in updated["comment"]

    def test_update_nonexistent_returns_none(self):
        result = update_comment("nonexistent", "test")
        assert result is None


class TestUpdatePrice:

    def test_update_price_creates_history(self):
        fav = add_favorite(SAMPLE_FAVORITE)
        update_price(fav["listing_key"], 260000)
        history = get_price_history(fav["listing_key"])
        assert len(history) == 2  # initial + update
        assert history[-1]["price"] == 260000

    def test_update_price_none(self):
        fav = add_favorite(SAMPLE_FAVORITE)
        result = update_price(fav["listing_key"], None)
        assert result["price"] is None

    def test_update_price_nonexistent(self):
        result = update_price("nonexistent", 100000)
        assert result is None

    def test_update_coords(self):
        fav = add_favorite(SAMPLE_FAVORITE)
        assert fav["lat"] is None
        assert fav["lon"] is None
        update_coords(fav["listing_key"], 43.25, 76.95)
        refreshed = get_favorite(fav["listing_key"])
        assert refreshed["lat"] == 43.25
        assert refreshed["lon"] == 76.95

    def test_update_coords_nonexistent(self):
        # Silent no-op on missing key (no exception)
        update_coords("nonexistent", 43.25, 76.95)

    def test_multiple_price_updates(self):
        fav = add_favorite(SAMPLE_FAVORITE)
        for p in [260000, 270000, 250000]:
            update_price(fav["listing_key"], p)
        history = get_price_history(fav["listing_key"])
        assert len(history) == 4
        prices = [h["price"] for h in history]
        assert prices == [250000, 260000, 270000, 250000]


class TestPriceHistory:

    def test_empty_history(self):
        fav = add_favorite({**SAMPLE_FAVORITE, "price": None})
        history = get_price_history(fav["listing_key"])
        assert history == []

    def test_history_chronological_order(self):
        fav = add_favorite(SAMPLE_FAVORITE)
        update_price(fav["listing_key"], 260000)
        update_price(fav["listing_key"], 240000)
        history = get_price_history(fav["listing_key"])
        assert len(history) == 3
        assert history[0]["price"] == 250000
        assert history[1]["price"] == 260000
        assert history[2]["price"] == 240000

    def test_history_has_timestamps(self):
        fav = add_favorite(SAMPLE_FAVORITE)
        history = get_price_history(fav["listing_key"])
        assert history[0]["checked_at"]


class TestDeleteFavorite:

    def test_delete_existing(self):
        fav = add_favorite(SAMPLE_FAVORITE)
        assert delete_favorite(fav["listing_key"]) is True

    def test_delete_nonexistent(self):
        assert delete_favorite("nonexistent") is False

    def test_delete_removes_price_history(self):
        fav = add_favorite(SAMPLE_FAVORITE)
        update_price(fav["listing_key"], 260000)
        delete_favorite(fav["listing_key"])
        history = get_price_history(fav["listing_key"])
        assert history == []

    def test_list_after_delete(self):
        add_favorite(SAMPLE_FAVORITE)
        data2 = {**SAMPLE_FAVORITE, "url": "https://test2.com", "title": "other"}
        fav2 = add_favorite(data2)
        delete_favorite(SAMPLE_FAVORITE["url"] and listing_key(
            SAMPLE_FAVORITE["url"], SAMPLE_FAVORITE["source"], SAMPLE_FAVORITE["title"]
        ))
        favs = list_favorites()
        assert len(favs) == 1
        assert favs[0]["title"] == "other"


class TestInitDB:

    def test_idempotent_init(self):
        init_db()
        init_db()
        assert list_favorites() is not None

    def test_reset_db(self):
        add_favorite(SAMPLE_FAVORITE)
        assert len(list_favorites()) == 1
        reset_db()
        assert len(list_favorites()) == 0
