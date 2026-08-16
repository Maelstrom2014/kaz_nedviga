"""Tests for the search engine: _parse_params, _to_dict, _build_listings, sorting, API endpoints."""
import pytest
from unittest.mock import patch, MagicMock
from werkzeug.datastructures import MultiDict

from app import app, _parse_params, _to_dict, _build_listings_from_request, _dict
from parsers.models import Listing, SearchParams


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Isolated test client with temp results cache and settings."""
    monkeypatch.setattr("app._RESULTS_CACHE", tmp_path / "last_results.json")
    # Isolate settings too — _filter_no_photo reads hide_no_photo from here,
    # the real file must never influence tests
    monkeypatch.setattr("app.SETTINGS_PATH", tmp_path / "settings.json")
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


# ============================================================
# _parse_params — JSON dict input (as sent by frontend)
# ============================================================

class TestParseParamsJsonDict:
    """Test _parse_params when args is a plain dict (from request.json)."""

    def test_empty_dict(self):
        params = _parse_params({})
        assert params.city == "almaty"
        assert params.rooms == []
        assert params.price_min is None
        assert params.price_max is None
        assert params.limit == 20

    def test_rooms_as_list_in_dict(self):
        params = _parse_params({"rooms": [1, 2]})
        assert params.rooms == [1, 2]

    def test_rooms_as_single_int_string(self):
        params = _parse_params({"rooms": "2"})
        assert params.rooms == [2]

    def test_rooms_empty_list(self):
        params = _parse_params({"rooms": []})
        assert params.rooms == []

    def test_price_min_max(self):
        params = _parse_params({"price_min": "100000", "price_max": "300000"})
        assert params.price_min == 100000
        assert params.price_max == 300000

    def test_price_invalid_string(self):
        params = _parse_params({"price_min": "abc"})
        assert params.price_min is None

    def test_query(self):
        params = _parse_params({"query": "  2-комнатная  "})
        assert params.query == "2-комнатная"

    def test_query_none_value(self):
        params = _parse_params({"query": None})
        assert params.query == ""

    def test_district(self):
        params = _parse_params({"district": "Алмалинский"})
        assert params.district == "Алмалинский"

    def test_floor_min_max(self):
        params = _parse_params({"floor_min": "2", "floor_max": "5"})
        assert params.floor_min == 2
        assert params.floor_max == 5

    def test_area_min_max(self):
        params = _parse_params({"area_min": "40", "area_max": "80"})
        assert params.area_min == 40
        assert params.area_max == 80

    def test_limit_custom(self):
        params = _parse_params({"limit": "50"})
        assert params.limit == 50

    def test_limit_zero(self):
        params = _parse_params({"limit": "0"})
        assert params.limit == 0

    def test_limit_invalid(self):
        params = _parse_params({"limit": "abc"})
        assert params.limit == 20

    def test_limit_missing(self):
        params = _parse_params({})
        assert params.limit == 20

    def test_max_pages_default_zero(self):
        # 0 = "use each site's own default page count"
        assert _parse_params({}).max_pages == 0

    def test_max_pages_custom(self):
        params = _parse_params({"max_pages": "4"})
        assert params.max_pages == 4

    def test_max_pages_clamped_to_10(self):
        assert _parse_params({"max_pages": "99"}).max_pages == 10

    def test_max_pages_invalid(self):
        assert _parse_params({"max_pages": "abc"}).max_pages == 0
        assert _parse_params({"max_pages": "-3"}).max_pages == 0

    def test_all_params_combined(self):
        params = _parse_params({
            "query": "квартира",
            "rooms": [1, 2],
            "price_min": "50000",
            "price_max": "200000",
            "district": "Бостандыкский",
            "floor_min": "1",
            "floor_max": "9",
            "area_min": "30",
            "area_max": "100",
            "limit": "10",
            "max_pages": "3",
        })
        assert params.query == "квартира"
        assert params.rooms == [1, 2]
        assert params.price_min == 50000
        assert params.price_max == 200000
        assert params.district == "Бостандыкский"
        assert params.floor_min == 1
        assert params.floor_max == 9
        assert params.area_min == 30
        assert params.area_max == 100
        assert params.limit == 10
        assert params.max_pages == 3


# ============================================================
# _parse_params — MultiDict input (from request.form)
# ============================================================

class TestParseParamsMultiDict:
    """Test _parse_params when args is a MultiDict (from request.form)."""

    def test_rooms_multiple_values_multidict(self):
        form = MultiDict([("rooms", "1"), ("rooms", "2")])
        params = _parse_params(form)
        assert params.rooms == [1, 2]

    def test_rooms_single_value_multidict(self):
        form = MultiDict([("rooms", "3")])
        params = _parse_params(form)
        assert params.rooms == [3]

    def test_price_multidict(self):
        form = MultiDict([("price_min", "100000"), ("price_max", "200000")])
        params = _parse_params(form)
        assert params.price_min == 100000
        assert params.price_max == 200000

    def test_empty_multidict(self):
        params = _parse_params(MultiDict())
        assert params.city == "almaty"
        assert params.limit == 20


# ============================================================
# _to_dict
# ============================================================

class TestToDict:

    def test_includes_price_str(self):
        listing = Listing(title="test", price=150000)
        d = _to_dict(listing)
        assert d["price_str"] == "150 000 тг"
        assert d["price"] == 150000
        assert d["title"] == "test"

    def test_price_str_none(self):
        listing = Listing(title="test", price=None)
        d = _to_dict(listing)
        assert d["price_str"] == "—"

    def test_all_fields_present(self):
        listing = Listing(
            title="test", price=100000, currency="тг", rooms=2,
            area=50.0, floor=3, total_floors=9, address="addr",
            url="http://test", source="test.kz", phone="123",
            description="desc", photo="http://photo",
        )
        d = _to_dict(listing)
        expected_keys = {"title", "price", "currency", "rooms", "area", "floor",
                         "total_floors", "address", "url", "source", "phone",
                         "description", "photo", "price_str"}
        assert expected_keys.issubset(set(d.keys()))


# ============================================================
# _build_listings_from_request
# ============================================================

class TestBuildListings:

    def test_empty_results(self):
        with app.test_request_context(json={"results": []}):
            items = _build_listings_from_request()
        assert items == []

    def test_single_listing(self):
        with app.test_request_context(json={"results": [
            {"title": "apt", "price": 100000, "source": "krisha.kz", "rooms": 2}
        ]}):
            items = _build_listings_from_request()
        assert len(items) == 1
        assert items[0].title == "apt"
        assert items[0].price == 100000
        assert items[0].source == "krisha.kz"

    def test_listing_with_defaults(self):
        with app.test_request_context(json={"results": [
            {"title": "minimal"}
        ]}):
            items = _build_listings_from_request()
        assert items[0].title == "minimal"
        assert items[0].price is None
        assert items[0].currency == "тг"
        assert items[0].source == ""

    def test_multiple_listings(self):
        results = [{"title": f"apt{i}", "price": 100000 * i} for i in range(5)]
        with app.test_request_context(json={"results": results}):
            items = _build_listings_from_request()
        assert len(items) == 5
        assert items[3].price == 300000


# ============================================================
# _dict helper
# ============================================================

class TestDictHelper:

    def test_dict_returns_all_fields(self):
        params = SearchParams(rooms=[1], price_min=50000)
        d = _dict(params)
        assert d["rooms"] == [1]
        assert d["price_min"] == 50000
        assert d["city"] == "almaty"

    def test_dict_can_reconstruct_params(self):
        params = SearchParams(rooms=[1, 2], price_min=50000, price_max=200000, limit=10)
        d = _dict(params)
        new_params = SearchParams(**d)
        assert new_params.rooms == [1, 2]
        assert new_params.price_min == 50000
        assert new_params.limit == 10


# ============================================================
# API search endpoint — mocked parsers
# ============================================================

class TestApiSearch:

    @patch("app.get_all_parsers")
    def test_search_returns_results(self, mock_get, client):
        mock_parser = MagicMock()
        mock_parser.name = "test.kz"
        mock_parser.run.return_value = [
            Listing(title="apt1", price=100000, source="test.kz", url="http://t/1"),
            Listing(title="apt2", price=200000, source="test.kz", url="http://t/2"),
        ]
        mock_get.return_value = [mock_parser]

        resp = client.post("/api/search", json={})
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["total"] == 2
        assert data["results"][0]["price_str"] == "100 000 тг"

    @patch("app.get_all_parsers")
    def test_search_sorts_by_price(self, mock_get, client):
        mock_parser = MagicMock()
        mock_parser.name = "test.kz"
        mock_parser.run.return_value = [
            Listing(title="expensive", price=300000, source="test.kz", url="http://t/e"),
            Listing(title="cheap", price=50000, source="test.kz", url="http://t/c"),
            Listing(title="mid", price=150000, source="test.kz", url="http://t/m"),
        ]
        mock_get.return_value = [mock_parser]

        resp = client.post("/api/search", json={})
        data = resp.get_json()
        prices = [r["price"] for r in data["results"]]
        # Exclude None-price entries from sort check
        priced = [p for p in prices if p is not None]
        assert priced == sorted(priced)
        assert 50000 in prices
        assert 300000 in prices
        assert prices.index(50000) < prices.index(300000)

    @patch("app.get_all_parsers")
    def test_search_none_price_sorted_last(self, mock_get, client):
        mock_parser = MagicMock()
        mock_parser.name = "test.kz"
        mock_parser.run.return_value = [
            Listing(title="no_price", price=None, source="test.kz", url="http://t/n"),
            Listing(title="with_price", price=150000, source="test.kz", url="http://t/w"),
        ]
        mock_get.return_value = [mock_parser]

        resp = client.post("/api/search", json={})
        data = resp.get_json()
        # The with_price listing should come before any None-price listing
        prices = [r["price"] for r in data["results"]]
        assert 150000 in prices
        assert None in prices
        assert prices.index(150000) < prices.index(None)

    @patch("app.get_all_parsers")
    def test_search_filters_by_source(self, mock_get, client):
        mock_parser1 = MagicMock()
        mock_parser1.name = "krisha.kz"
        mock_parser1.run.return_value = [Listing(title="krisha", source="krisha.kz", url="http://t/k")]

        mock_parser2 = MagicMock()
        mock_parser2.name = "olx.kz"
        mock_parser2.run.return_value = [Listing(title="olx", source="olx.kz", url="http://t/o")]

        mock_get.return_value = [mock_parser1, mock_parser2]

        resp = client.post("/api/search", json={"sources": ["krisha.kz"]})
        data = resp.get_json()
        assert data["total"] == 1
        assert data["results"][0]["source"] == "krisha.kz"
        mock_parser2.run.assert_not_called()

    @patch("app.get_all_parsers")
    def test_search_parser_exception_handled(self, mock_get, client):
        mock_parser = MagicMock()
        mock_parser.name = "broken.kz"
        mock_parser.run.side_effect = Exception("Network error")
        mock_get.return_value = [mock_parser]

        resp = client.post("/api/search", json={})
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["total"] == 0

    @patch("app.get_all_parsers")
    def test_search_with_rooms_filter(self, mock_get, client):
        mock_parser = MagicMock()
        mock_parser.name = "test.kz"
        mock_parser.run.return_value = []
        mock_get.return_value = [mock_parser]

        client.post("/api/search", json={"rooms": [1, 2], "price_min": "50000"})
        called_params = mock_parser.run.call_args[0][0]
        assert called_params.rooms == [1, 2]
        assert called_params.price_min == 50000

    @patch("app.get_all_parsers")
    def test_search_form_data(self, mock_get, client):
        """Test that form data (not JSON) also works."""
        mock_parser = MagicMock()
        mock_parser.name = "test.kz"
        mock_parser.run.return_value = [Listing(title="form", source="test.kz", url="http://t/f")]
        mock_get.return_value = [mock_parser]

        resp = client.post("/api/search", data={"query": "test", "price_min": "100000"})
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["total"] == 1


# ============================================================
# API heatmap endpoint — mocked
# ============================================================

class TestApiHeatmap:

    @patch("app.get_all_parsers")
    def test_heatmap_returns_structure(self, mock_get, client):
        mock_parser = MagicMock()
        mock_parser.name = "test.kz"
        mock_parser.run.return_value = [
            Listing(title="apt", price=150000, source="test.kz",
                     address="Алмалинский район"),
        ]
        mock_get.return_value = [mock_parser]

        resp = client.post("/api/heatmap", json={"property_type": "all"})
        data = resp.get_json()
        assert resp.status_code == 200
        assert "heat_points" in data
        assert "district_stats" in data
        assert "property_type" in data
        assert "total_listings" in data
        assert len(data["heat_points"]) == 8

    @patch("app.get_all_parsers")
    def test_heatmap_avg_price_calculation(self, mock_get, client):
        mock_parser = MagicMock()
        mock_parser.name = "test.kz"
        mock_parser.run.return_value = [
            Listing(title="1", price=100000, address="Алмалинский"),
            Listing(title="2", price=200000, address="Алмалинский"),
            Listing(title="3", price=300000, address="Бостандыкский"),
        ]
        mock_get.return_value = [mock_parser]

        resp = client.post("/api/heatmap", json={"property_type": "all"})
        data = resp.get_json()
        almalinsky = next(d for d in data["district_stats"] if d["name"] == "Алмалинский")
        assert almalinsky["avg_price"] == 150000  # (100k+200k)/2
        assert almalinsky["count"] == 2

    @patch("app.get_all_parsers")
    def test_heatmap_intensity_range(self, mock_get, client):
        mock_parser = MagicMock()
        mock_parser.name = "test.kz"
        mock_parser.run.return_value = [
            Listing(title="1", price=100000, address="Алмалинский"),
            Listing(title="2", price=200000, address="Бостандыкский"),
        ]
        mock_get.return_value = [mock_parser]

        resp = client.post("/api/heatmap", json={"property_type": "all"})
        data = resp.get_json()
        for stat in data["district_stats"]:
            assert 0 <= stat["intensity"] <= 1.0
        # The district with highest price should have intensity 1.0
        max_intensity_district = max(data["district_stats"], key=lambda x: x["avg_price"])
        assert max_intensity_district["intensity"] == 1.0

    @patch("app.get_all_parsers")
    def test_heatmap_no_data(self, mock_get, client):
        mock_parser = MagicMock()
        mock_parser.name = "test.kz"
        mock_parser.run.return_value = []
        mock_get.return_value = [mock_parser]

        resp = client.post("/api/heatmap", json={"property_type": "all"})
        data = resp.get_json()
        for stat in data["district_stats"]:
            assert stat["avg_price"] == 0
            assert stat["count"] == 0
            assert stat["intensity"] == 0

    @patch("app.get_all_parsers")
    @pytest.mark.parametrize("ptype,expected_rooms", [
        ("studio", [0]),
        ("1", [1]),
        ("2", [2]),
    ])
    def test_heatmap_property_type_rooms(self, mock_get, ptype, expected_rooms, client):
        mock_parser = MagicMock()
        mock_parser.name = "test.kz"
        mock_parser.run.return_value = []
        mock_get.return_value = [mock_parser]

        client.post("/api/heatmap", json={"property_type": ptype})
        called_params = mock_parser.run.call_args[0][0]
        assert called_params.rooms == expected_rooms

    @patch("app.get_all_parsers")
    def test_heatmap_property_type_apartment(self, mock_get, client):
        mock_parser = MagicMock()
        mock_parser.name = "test.kz"
        mock_parser.run.return_value = []
        mock_get.return_value = [mock_parser]

        client.post("/api/heatmap", json={"property_type": "apartment"})
        called_params = mock_parser.run.call_args[0][0]
        assert "апартамент" in called_params.query

    @patch("app.get_all_parsers")
    def test_heatmap_property_type_all_no_filter(self, mock_get, client):
        mock_parser = MagicMock()
        mock_parser.name = "test.kz"
        mock_parser.run.return_value = []
        mock_get.return_value = [mock_parser]

        client.post("/api/heatmap", json={"property_type": "all"})
        called_params = mock_parser.run.call_args[0][0]
        assert called_params.rooms == []
        assert called_params.query == ""

    @patch("app.get_all_parsers")
    def test_heatmap_no_almalinsky_dump(self, mock_get, client):
        """Unmatched listings must NOT default to Алмалинский."""
        mock_parser = MagicMock()
        mock_parser.name = "test.kz"
        mock_parser.run.return_value = [
            Listing(title="unmatched", price=500000, source="test.kz",
                    address="г. Алматы, ул. Пушкина"),
        ]
        mock_get.return_value = [mock_parser]

        resp = client.post("/api/heatmap", json={"property_type": "all"})
        data = resp.get_json()
        almalinsky = next(d for d in data["district_stats"] if d["name"] == "Алмалинский")
        assert almalinsky["count"] == 0
        assert data["unmatched_count"] == 1

    @patch("app.get_all_parsers")
    def test_heatmap_point_in_polygon(self, mock_get, client):
        """Listings with coords matched by point-in-polygon, not text."""
        mock_parser = MagicMock()
        mock_parser.name = "test.kz"
        # Bare address, but coords land inside Жетысуский polygon (~43.330, 76.965)
        mock_parser.run.return_value = [
            Listing(title="apt", price=100000, source="test.kz",
                    address="Алматы", lat=43.330, lon=76.965),
        ]
        mock_get.return_value = [mock_parser]

        resp = client.post("/api/heatmap", json={"property_type": "all"})
        data = resp.get_json()
        zhetysu = next(d for d in data["district_stats"] if d["name"] == "Жетысуский")
        assert zhetysu["count"] == 1
        assert data["unmatched_count"] == 0

    @patch("app.get_all_parsers")
    def test_heatmap_word_boundary_not_street_name(self, mock_get, client):
        """'ул. Ауэзова' must NOT match 'Ауэзовский' district."""
        mock_parser = MagicMock()
        mock_parser.name = "test.kz"
        mock_parser.run.return_value = [
            Listing(title="apt", price=100000, source="test.kz",
                    address="г. Алматы, ул. Ауэзова"),
        ]
        mock_get.return_value = [mock_parser]

        resp = client.post("/api/heatmap", json={"property_type": "all"})
        data = resp.get_json()
        auezov = next(d for d in data["district_stats"] if d["name"] == "Ауэзовский")
        assert auezov["count"] == 0
        assert data["unmatched_count"] == 1

    def test_point_in_polygon_inside(self):
        from app import _point_in_polygon
        # Point inside the Алмалинский polygon
        assert _point_in_polygon(43.264, 76.929, [
            [43.283, 76.915], [43.283, 76.944], [43.251, 76.944], [43.251, 76.914],
        ])

    def test_point_in_polygon_outside(self):
        from app import _point_in_polygon
        # Point clearly outside the polygon above
        assert not _point_in_polygon(43.400, 77.000, [
            [43.283, 76.915], [43.283, 76.944], [43.251, 76.944], [43.251, 76.914],
        ])


# ============================================================
# API index page
# ============================================================

class TestIndexPage:

    def test_index_returns_html(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"<html" in resp.data.lower()

    def test_index_contains_site_list(self, client):
        resp = client.get("/")
        assert b"krisha.kz" in resp.data
        assert b"olx.kz" in resp.data

    def test_index_contains_districts(self, client):
        resp = client.get("/")
        assert b"\xd0\x90\xd0\xbb\xd0\xbc\xd0\xb0\xd0\xbb\xd0\xb8\xd0\xbd\xd1\x81\xd0\xba\xd0\xb8\xd0\xb9" in resp.data

    def test_index_contains_map_div(self, client):
        resp = client.get("/")
        assert b'id="map"' in resp.data

    def test_index_contains_heatmap_controls(self, client):
        resp = client.get("/")
        assert b"propertyType" in resp.data
        assert b"\xd0\xa1\xd1\x82\xd1\x83\xd0\xb4\xd0\xb8\xd0\xb8" in resp.data  # "Студии"
