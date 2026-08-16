"""Tests for SearchParams and Listing data models."""
from parsers.models import SearchParams, Listing


def test_search_params_defaults():
    p = SearchParams()
    assert p.city == "almaty"
    assert p.rooms == []
    assert p.price_min is None
    assert p.limit == 20


def test_matches_price():
    p = SearchParams(price_min=100000, price_max=200000)
    assert p.matches_price(150000)
    assert not p.matches_price(50000)
    assert not p.matches_price(300000)
    assert p.matches_price(None)  # unknown price passes


def test_matches_rooms():
    p = SearchParams(rooms=[1, 2])
    assert p.matches_rooms(1)
    assert p.matches_rooms(2)
    assert not p.matches_rooms(3)
    assert p.matches_rooms(None)  # unknown rooms passes


def test_matches_area():
    p = SearchParams(area_min=40, area_max=80)
    assert p.matches_area(50.0)
    assert not p.matches_area(30.0)
    assert not p.matches_area(90.0)
    assert p.matches_area(None)


def test_matches_floor():
    p = SearchParams(floor_min=2, floor_max=5)
    assert p.matches_floor(3)
    assert not p.matches_floor(1)
    assert not p.matches_floor(6)
    assert p.matches_floor(None)


def test_matches_district():
    p = SearchParams(district="Алмалинский")
    assert p.matches_district("Алмалинский район, ул. Абая")
    assert not p.matches_district("Бостандыкский район")
    p2 = SearchParams()
    assert p2.matches_district("любой адрес")


def test_listing_price_str():
    l = Listing(title="test", price=150000)
    assert l.price_str() == "150 000 тг"
    l2 = Listing(title="test", price=None)
    assert l2.price_str() == "—"


def test_listing_to_dict():
    l = Listing(title="test", price=100000, rooms=2)
    d = l.to_dict()
    assert d["title"] == "test"
    assert d["price"] == 100000
    assert d["rooms"] == 2
    assert "url" in d
