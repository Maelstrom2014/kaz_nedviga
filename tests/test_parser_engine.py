"""Tests for BaseParser engine: run(), apply(), filter_listing(), fetch() with mocks."""
import json
import pytest
from unittest.mock import patch, MagicMock, PropertyMock

from parsers.base import BaseParser, parse_int, parse_float, parse_rooms, parse_floor_pair
from parsers.models import Listing, SearchParams
from parsers.krisha import KrishaParser
from parsers.etagi import EtagiParser

from .loaders import load_fixture


class TestFilterListing:

    def setup_method(self):
        self.parser = KrishaParser()
        self.listing = Listing(
            title="2-комнатная квартира",
            price=200000, rooms=2, area=65.0, floor=3, total_floors=9,
            address="Бостандыкский район, мкр. 5",
            source="krisha.kz", url="http://test",
        )

    def test_no_filter_matches_all(self):
        params = SearchParams()
        assert self.parser.filter_listing(self.listing, params)

    def test_price_filter_match(self):
        params = SearchParams(price_min=100000, price_max=300000)
        assert self.parser.filter_listing(self.listing, params)

    def test_price_filter_too_low(self):
        params = SearchParams(price_min=300000)
        assert not self.parser.filter_listing(self.listing, params)

    def test_price_filter_too_high(self):
        params = SearchParams(price_max=100000)
        assert not self.parser.filter_listing(self.listing, params)

    def test_rooms_filter_match(self):
        params = SearchParams(rooms=[1, 2])
        assert self.parser.filter_listing(self.listing, params)

    def test_rooms_filter_no_match(self):
        params = SearchParams(rooms=[3, 4])
        assert not self.parser.filter_listing(self.listing, params)

    def test_area_filter_match(self):
        params = SearchParams(area_min=50, area_max=80)
        assert self.parser.filter_listing(self.listing, params)

    def test_area_filter_too_small(self):
        params = SearchParams(area_min=100)
        assert not self.parser.filter_listing(self.listing, params)

    def test_floor_filter_match(self):
        params = SearchParams(floor_min=1, floor_max=5)
        assert self.parser.filter_listing(self.listing, params)

    def test_floor_filter_too_high(self):
        params = SearchParams(floor_min=5)
        assert not self.parser.filter_listing(self.listing, params)

    def test_district_filter_match(self):
        params = SearchParams(district="Бостандык")
        assert self.parser.filter_listing(self.listing, params)

    def test_district_filter_no_match(self):
        params = SearchParams(district="Алмалинский")
        assert not self.parser.filter_listing(self.listing, params)

    def test_query_filter_match_in_title(self):
        params = SearchParams(query="комнат")
        assert self.parser.filter_listing(self.listing, params)

    def test_query_filter_match_in_description(self):
        self.listing.description = "хороший ремонт"
        params = SearchParams(query="ремонт")
        assert self.parser.filter_listing(self.listing, params)

    def test_query_filter_no_match(self):
        params = SearchParams(query="гараж")
        assert not self.parser.filter_listing(self.listing, params)

    def test_all_filters_combined_match(self):
        params = SearchParams(
            rooms=[2], price_min=100000, price_max=300000,
            area_min=50, area_max=100, floor_min=1, floor_max=5,
            district="Бостандык", query="квартира",
        )
        assert self.parser.filter_listing(self.listing, params)

    def test_empty_address_skips_district_filter(self):
        self.listing.address = ""
        params = SearchParams(district="Алмалинский")
        assert self.parser.filter_listing(self.listing, params)

    def test_listing_with_none_price_passes_filter(self):
        self.listing.price = None
        params = SearchParams(price_min=100000, price_max=300000)
        assert self.parser.filter_listing(self.listing, params)

    def test_listing_with_none_rooms_passes_filter(self):
        self.listing.rooms = None
        params = SearchParams(rooms=[2])
        assert self.parser.filter_listing(self.listing, params)

    def test_listing_with_none_area_passes_filter(self):
        self.listing.area = None
        params = SearchParams(area_min=40, area_max=80)
        assert self.parser.filter_listing(self.listing, params)

    def test_listing_with_none_floor_passes_filter(self):
        self.listing.floor = None
        params = SearchParams(floor_min=2, floor_max=5)
        assert self.parser.filter_listing(self.listing, params)


class TestApply:

    def setup_method(self):
        self.parser = KrishaParser()
        self.html = load_fixture("krisha")

    def test_apply_returns_all_when_no_limit(self):
        params = SearchParams(limit=0)
        results = self.parser.parse(self.html, params)
        filtered = self.parser.apply(results, params)
        assert len(filtered) == 2

    def test_apply_respects_limit(self):
        params = SearchParams(limit=1)
        results = self.parser.parse(self.html, params)
        filtered = self.parser.apply(results, params)
        assert len(filtered) == 1

    def test_apply_filters_by_price(self):
        params = SearchParams(price_max=200000, limit=0)
        results = self.parser.parse(self.html, params)
        filtered = self.parser.apply(results, params)
        assert len(filtered) == 1
        assert filtered[0].price == 150000

    def test_apply_returns_empty_when_no_match(self):
        params = SearchParams(price_min=999999, limit=0)
        results = self.parser.parse(self.html, params)
        filtered = self.parser.apply(results, params)
        assert filtered == []


class TestParserRun:

    @patch.object(BaseParser, "fetch")
    def test_run_parses_and_filters(self, mock_fetch):
        mock_fetch.return_value = load_fixture("krisha")
        parser = KrishaParser()
        params = SearchParams(limit=0)
        results = parser.run(params)
        assert len(results) == 2
        assert results[0].source == "krisha.kz"

    @patch.object(BaseParser, "fetch")
    def test_run_applies_limit(self, mock_fetch):
        mock_fetch.return_value = load_fixture("krisha")
        parser = KrishaParser()
        params = SearchParams(limit=1)
        results = parser.run(params)
        assert len(results) == 1

    @patch.object(BaseParser, "fetch")
    def test_run_applies_price_filter(self, mock_fetch):
        mock_fetch.return_value = load_fixture("krisha")
        parser = KrishaParser()
        params = SearchParams(price_max=200000, limit=0)
        results = parser.run(params)
        assert len(results) == 1
        assert results[0].price == 150000

    @patch.object(BaseParser, "fetch")
    def test_run_handles_fetch_exception(self, mock_fetch):
        mock_fetch.side_effect = ConnectionError("Network error")
        parser = KrishaParser()
        results = parser.run(SearchParams())
        assert results == []

    @patch.object(BaseParser, "fetch")
    def test_run_handles_parse_exception(self, mock_fetch):
        mock_fetch.return_value = "<<<malformed html>>>"
        parser = KrishaParser()
        results = parser.run(SearchParams())
        assert isinstance(results, list)

    @patch.object(BaseParser, "fetch")
    def test_run_handles_empty_response(self, mock_fetch):
        mock_fetch.return_value = ""
        parser = KrishaParser()
        results = parser.run(SearchParams())
        assert results == []

    @patch.object(BaseParser, "fetch")
    def test_run_build_url_called_with_params(self, mock_fetch):
        mock_fetch.return_value = load_fixture("krisha")
        parser = KrishaParser()
        parser.build_url = MagicMock(return_value="http://test.kz")
        params = SearchParams(rooms=[1], price_min=50000)
        parser.run(params)
        parser.build_url.assert_called_once_with(params, page=1)

    @patch.object(BaseParser, "fetch")
    def test_run_query_filter_applied(self, mock_fetch):
        mock_fetch.return_value = load_fixture("krisha")
        parser = KrishaParser()
        params = SearchParams(query="2-комнатная", limit=0)
        results = parser.run(params)
        assert len(results) == 1
        assert "2-комнатная" in results[0].title


class _FakePageParser(BaseParser):
    """Two-page fake parser: page 1 -> one listing, page 2 -> another."""
    name = "fake.page"
    base_url = "https://fake.test"
    min_page_size = 1
    max_pages = 3
    enrich_photo_count = 0

    def _build_url_base(self, params):
        return self.base_url + "/search"

    def parse(self, html, params):
        if html == "page1":
            return [Listing(title="one", url="https://fake.test/1",
                            source=self.name)]
        if html == "page2":
            return [Listing(title="two", url="https://fake.test/2",
                            source=self.name)]
        return []


class TestStickySession:

    def test_sticky_session_created_once_and_reused(self):
        p = EtagiParser()
        assert p.session_sticky is True
        with patch("curl_cffi.requests.Session") as session_cls:
            sess = MagicMock()
            sess.get.return_value = MagicMock(text="<html>",
                                              raise_for_status=lambda: None)
            session_cls.return_value = sess
            h1 = p._fetch_cffi("https://fake.test/1")
            h2 = p._fetch_cffi("https://fake.test/2")
        assert h1 == "<html>" and h2 == "<html>"
        session_cls.assert_called_once()  # one session, not one per request
        assert sess.get.call_count == 2

    def test_non_sticky_parser_creates_session_per_request(self):
        p = KrishaParser()  # session_sticky defaults to False
        assert p.session_sticky is False
        with patch("curl_cffi.requests.Session") as session_cls:
            sess = MagicMock()
            sess.get.return_value = MagicMock(text="<html>",
                                              raise_for_status=lambda: None)
            session_cls.return_value = sess
            p._fetch_cffi("https://fake.test/1")
            p._fetch_cffi("https://fake.test/2")
        assert session_cls.call_count == 2
        assert p._cffi_session is None


class TestFailFastWaf:

    def test_fail_fast_raises_without_fallback(self):
        import requests
        p = EtagiParser()
        assert p.fail_fast_on_waf is True
        err = requests.exceptions.HTTPError(
            response=MagicMock(status_code=403))
        with patch.object(p, "_fetch_cffi", side_effect=err), \
             patch.object(p, "_fetch_requests") as rq:
            with pytest.raises(requests.exceptions.HTTPError):
                p.fetch("https://fake.test/")
        rq.assert_not_called()  # no requests fallback, no escalation

    def test_cffi_httperror_normalized_to_requests(self):
        # curl_cffi raises its own HTTPError class; _fetch_cffi must
        # re-raise as requests' HTTPError (with .response) so run()'s
        # partial-results logic applies.
        import requests
        from curl_cffi.requests.exceptions import HTTPError as CffiHTTPError
        p = EtagiParser()
        with patch("curl_cffi.requests.Session") as session_cls:
            sess = MagicMock()
            bad = MagicMock()
            bad.status_code = 403
            bad.reason = "Forbidden"
            bad.raise_for_status.side_effect = CffiHTTPError("HTTP Error 403")
            sess.get.return_value = bad
            session_cls.return_value = sess
            with pytest.raises(requests.exceptions.HTTPError) as ei:
                p._fetch_cffi("https://fake.test/")
        assert ei.value.response.status_code == 403


class TestRunPacingAndPartial:

    def test_run_paces_between_pages(self):
        p = _FakePageParser()
        p.page_delay = (0.01, 0.02)
        sequence = iter(["page1", "page2", "page2"])
        with patch.object(BaseParser, "fetch",
                          side_effect=lambda url: next(sequence)), \
             patch("parsers.base.time.sleep") as mock_sleep:
            results = p.run(SearchParams(limit=0))
        assert [r.title for r in results] == ["one", "two"]
        # 3 pages fetched -> 2 inter-page pauses
        assert mock_sleep.call_count >= 2

    def test_run_keeps_partial_results_on_http_error(self):
        import requests
        p = _FakePageParser()
        p.page_delay = None  # no pacing in the test
        calls = {"n": 0}

        def fake_fetch(url):
            calls["n"] += 1
            if calls["n"] == 1:
                return "page1"
            raise requests.exceptions.HTTPError(
                response=MagicMock(status_code=403))

        with patch.object(BaseParser, "fetch", side_effect=fake_fetch):
            results = p.run(SearchParams(limit=0))
        assert [r.title for r in results] == ["one"]
        assert p.last_stats.status == "partial"
        assert p.last_stats.results_count == 1

    def test_run_no_partial_when_first_page_fails(self):
        import requests
        p = _FakePageParser()
        p.page_delay = None
        with patch.object(BaseParser, "fetch", side_effect=
                          requests.exceptions.HTTPError(
                              response=MagicMock(status_code=403))):
            results = p.run(SearchParams(limit=0))
        assert results == []
        assert p.last_stats.status == "http_error"


class TestEtagiConfig:

    def test_browser_like_settings(self):
        p = EtagiParser()
        assert p.use_cffi is True
        assert p.session_sticky is True
        assert p.fail_fast_on_waf is True
        # one consistent fingerprint — no rotation
        assert p.cffi_profiles == ("chrome",)
        assert p.cffi_impersonate == "chrome"
        # human-like pacing
        assert p.page_delay is not None
        lo, hi = p.page_delay
        assert 1.0 <= lo < hi
        # one clean request carries the full state batch — no pagination
        # (default 3, 3x the original 1)
        assert p.max_pages == 3

    # -- embedded JSON state (var data={...}) parsing --------------------

    @staticmethod
    def _state_html(rents):
        blob = json.dumps({"lists": {"rents": rents}}, ensure_ascii=False)
        return (f'<html><head><title>Снять квартиру в Алматы — '
                f'аренда квартир</title></head><body>'
                f'<script>var data={blob};</script></body></html>')

    def test_build_url_never_appends_query_string(self):
        # Any ?param= URL is WAF-challenged with a 403, so filters must be
        # applied locally and the URL stays clean even with filters set.
        p = EtagiParser()
        params = SearchParams(rooms=[1], price_min=100000,
                              price_max=300000, district="Алмалинский")
        url = p.build_url(params)
        assert url == "https://almaty.etagi.com/realty_rent/"
        assert "?" not in url

    def test_build_url_page2_is_same_clean_url(self):
        # ?page=N is gated; a higher page re-fetches the same clean URL.
        p = EtagiParser()
        assert p.build_url(SearchParams(), page=2) == \
            "https://almaty.etagi.com/realty_rent/"

    def test_parse_state_json_extracts_listings(self):
        rents = [
            {"_ticket_id": 111,
             "meta": {"city": "Алматы", "district": "Алмалинский",
                      "street": "Нурмакова"},
             "house_num": "56", "rooms": 1, "studio": False, "square": 45,
             "floor": 3, "floors": 5, "price": 230000, "type": "flat",
             "main_photo": "https://cdn.example/p1.jpg",
             "la": 43.25, "lo": 76.90},
            {"_ticket_id": 222,
             "meta": {"city": "Алматы", "district": "Медеуский",
                      "street": "Байтурсынова"},
             "house_num": "7", "rooms": 0, "studio": True, "square": 28,
             "floor": 9, "floors": 12, "price": 180000, "type": "apart",
             "main_photo": "https://cdn.example/p2.jpg",
             "la": 43.2, "lo": 76.8},
        ]
        p = EtagiParser()
        res = p.parse(self._state_html(rents), SearchParams(limit=0))
        assert len(res) == 2
        a = res[0]
        assert a.price == 230000 and a.currency == "тг"
        assert a.rooms == 1 and a.area == 45.0
        assert a.floor == 3 and a.total_floors == 5
        assert a.address == "Алматы, Алмалинский, Нурмакова, д. 56"
        assert a.url == "https://almaty.etagi.com/realty_rent/111/"
        assert a.photo == "https://cdn.example/p1.jpg"
        assert a.lat == 43.25 and a.lon == 76.90
        assert a.source == "etagi.com"
        assert "1-комн. кв." in a.title and "45 м²" in a.title
        # studio / apart
        b = res[1]
        assert b.rooms == 0
        assert "Студия" not in b.title and "Апартамент" in b.title

    def test_parse_state_json_studio_title(self):
        rents = [{"_ticket_id": 333,
                  "meta": {"city": "Алматы", "district": "Жетысуский",
                           "street": "Гоголя"},
                  "house_num": "1", "rooms": 0, "studio": True, "square": 25,
                  "floor": 2, "floors": 9, "price": 150000, "type": "flat",
                  "main_photo": "", "la": None, "lo": None}]
        p = EtagiParser()
        res = p.parse(self._state_html(rents), SearchParams(limit=0))
        assert len(res) == 1
        assert res[0].rooms == 0
        assert res[0].title.startswith("Студия")
        assert res[0].photo == ""

    def test_parse_state_json_skips_items_without_ticket(self):
        rents = [{"price": 100}, {"_ticket_id": 9, "price": 200,
                                   "meta": {"city": "Алматы"},
                                   "square": 30, "rooms": 2, "floor": 1,
                                   "floors": 4}]
        p = EtagiParser()
        res = p.parse(self._state_html(rents), SearchParams(limit=0))
        assert len(res) == 1
        assert res[0].url.endswith("/realty_rent/9/")

    def test_parse_state_json_malformed_falls_back(self):
        # Unbalanced / non-JSON state blob must not raise; falls back to SSR.
        p = EtagiParser()
        html = ('<html><head><title>аренда квартир</title></head>'
                '<body><script>var data={broken json;</script></body></html>')
        assert p._parse_state(html) == []
        assert p.parse(html, SearchParams(limit=0)) == []

    def test_parse_state_json_missing_returns_empty(self):
        p = EtagiParser()
        assert p._parse_state('<html><body>no state</body></html>') == []

    def test_balanced_json_stops_at_top_level_close(self):
        blob = '{"a": 1, "b": {"c": "}"}}'
        assert EtagiParser._balanced_json(blob) == blob
        # trailing junk after the top-level object is dropped
        assert EtagiParser._balanced_json(blob + ';junk') == blob
        # braces inside strings are not counted
        assert EtagiParser._balanced_json('{"s": "{ }"};x') == '{"s": "{ }"}'

    def test_ssr_fallback_apartment_title(self):
        """SSR fallback: 'X-комн. апарт.' must produce 'апартаменты' in title."""
        p = EtagiParser()
        html = ('<html><head><title>аренда квартир</title></head><body>'
                '<div data-testid="object_card">'
                '<a href="/realty_rent/555/">photo</a>'
                '<span data-testid="object_card_price">650 000 ₸</span>'
                '3-комн. апарт. 121 м² 10/19 эт.'
                '<div class="address">Алматы, ул. Аль-Фараби, д. 41</div>'
                '<div>12 августа 2024</div>'
                '</div></body></html>')
        res = p.parse(html, SearchParams(limit=0))
        assert len(res) == 1
        assert res[0].rooms == 3
        assert "апартамент" in res[0].title.lower()
        assert "121 м²" in res[0].title

    def test_ssr_fallback_flat_title(self):
        """SSR fallback: 'X-комн. кв.' must produce 'кв.' in title (not 'апартаменты')."""
        p = EtagiParser()
        html = ('<html><head><title>аренда квартир</title></head><body>'
                '<div data-testid="object_card">'
                '<a href="/realty_rent/556/">photo</a>'
                '<span data-testid="object_card_price">230 000 ₸</span>'
                '1-комн. кв. 45 м² 3/5 эт.'
                '<div class="address">Алматы, ул. Нурмакова, д. 56</div>'
                '<div>12 августа 2024</div>'
                '</div></body></html>')
        res = p.parse(html, SearchParams(limit=0))
        assert len(res) == 1
        assert res[0].rooms == 1
        assert "кв." in res[0].title
        assert "апартамент" not in res[0].title.lower()


class TestBaseParserInit:

    def test_default_headers(self):
        parser = KrishaParser()
        assert "User-Agent" in parser.headers
        assert "Accept-Language" in parser.headers

    def test_custom_headers_merge(self):
        parser = KrishaParser(headers={"X-Custom": "yes"})
        assert parser.headers["X-Custom"] == "yes"
        assert "User-Agent" in parser.headers

    def test_default_timeout(self):
        parser = KrishaParser()
        assert parser.timeout == 20

    def test_custom_timeout(self):
        parser = KrishaParser(timeout=30)
        assert parser.timeout == 30

    def test_name_attribute(self):
        parser = KrishaParser()
        assert parser.name == "krisha.kz"

    def test_base_url_attribute(self):
        parser = KrishaParser()
        assert parser.base_url == "https://krisha.kz"
