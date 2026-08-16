"""Extended parser tests: malformed HTML, missing fields, edge cases."""
import pytest

from parsers.models import SearchParams, Listing
from parsers.krisha import KrishaParser
from parsers.olx import OlxParser
from parsers.base import parse_int, parse_float, parse_rooms, parse_floor_pair

from .loaders import load_fixture


class TestMalformedHTML:

    def setup_method(self):
        self.parser = KrishaParser()

    def test_empty_string(self):
        results = self.parser.parse("", SearchParams())
        assert results == []

    def test_only_html_tags(self):
        results = self.parser.parse("<html><body></body></html>", SearchParams())
        assert results == []

    def test_plain_text(self):
        results = self.parser.parse("just text no html", SearchParams())
        assert results == []

    def test_broken_html(self):
        results = self.parser.parse("<div class='a-card'><a href='/'>", SearchParams())
        assert isinstance(results, list)

    def test_html_without_cards(self):
        html = "<div><p>Some content</p><div>other</div></div>"
        results = self.parser.parse(html, SearchParams())
        assert results == []

    def test_card_without_title(self):
        html = '<div class="a-card"><div class="a-card__price">100 000</div></div>'
        results = self.parser.parse(html, SearchParams())
        assert results == []

    def test_card_without_link(self):
        html = '<div class="a-card"><div class="a-card__title">no link</div></div>'
        results = self.parser.parse(html, SearchParams())
        assert results == []

    def test_card_with_empty_title(self):
        html = '<div class="a-card"><div class="a-card__title"><a href="/test"></a></div></div>'
        results = self.parser.parse(html, SearchParams())
        if results:
            assert results[0].title == ""


class TestMissingFields:

    def setup_method(self):
        self.parser = KrishaParser()

    def test_card_without_price(self):
        html = """
        <div class="a-card">
          <a class="a-card__title" href="/test">2-комнатная, 50 м²</a>
          <div class="a-card__subtitle">Алмалинский</div>
        </div>
        """
        results = self.parser.parse(html, SearchParams())
        assert len(results) == 1
        assert results[0].price is None

    def test_card_without_address(self):
        html = """
        <div class="a-card">
          <a class="a-card__title" href="/test">2-комнатная, 50 м²</a>
          <div class="a-card__price">200 000</div>
        </div>
        """
        results = self.parser.parse(html, SearchParams())
        assert len(results) == 1
        assert results[0].address == ""

    def test_card_without_area(self):
        html = """
        <div class="a-card">
          <a class="a-card__title" href="/test">2-комнатная</a>
          <div class="a-card__price">200 000</div>
        </div>
        """
        results = self.parser.parse(html, SearchParams())
        assert len(results) == 1
        assert results[0].area is None

    def test_card_without_photo(self):
        html = """
        <div class="a-card">
          <a class="a-card__title" href="/test">2-комнатная, 50 м²</a>
          <div class="a-card__price">200 000</div>
        </div>
        """
        results = self.parser.parse(html, SearchParams())
        assert len(results) == 1
        assert results[0].photo == ""


class TestWhitespaceHandling:

    def setup_method(self):
        self.parser = KrishaParser()

    def test_title_with_extra_spaces(self):
        html = """
        <div class="a-card">
          <a class="a-card__title" href="/test">   2-комнатная    квартира   </a>
          <div class="a-card__price">200 000</div>
        </div>
        """
        results = self.parser.parse(html, SearchParams())
        assert "2-комнатная квартира" in results[0].title

    def test_title_with_newlines(self):
        html = """
        <div class="a-card">
          <a class="a-card__title" href="/test">
            2-комнатная
            квартира
          </a>
          <div class="a-card__price">200 000</div>
        </div>
        """
        results = self.parser.parse(html, SearchParams())
        assert "2-комнатная" in results[0].title


class TestUrlConstruction:

    def setup_method(self):
        self.parser = KrishaParser()

    def test_url_starts_with_http(self):
        params = SearchParams()
        url = self.parser.build_url(params)
        assert url.startswith("https://")

    def test_url_contains_search_path(self):
        params = SearchParams()
        url = self.parser.build_url(params)
        assert "/arenda/kvartiry/almaty/" in url

    def test_url_with_rooms_param(self):
        params = SearchParams(rooms=[1])
        url = self.parser.build_url(params)
        assert "rooms" in url.lower()

    def test_url_with_multiple_rooms(self):
        params = SearchParams(rooms=[1, 2, 3])
        url = self.parser.build_url(params)
        assert "rooms" in url.lower() or ".rooms" in url

    def test_url_with_price_params(self):
        params = SearchParams(price_min=50000, price_max=200000)
        url = self.parser.build_url(params)
        assert "50000" in url
        assert "200000" in url

    def test_url_with_district(self):
        params = SearchParams(district="Алмалинский")
        url = self.parser.build_url(params)
        assert "Алмалинский" in url

    def test_absolute_url_preserved(self):
        html = """
        <div class="a-card">
          <div class="a-card__title"><a href="https://full.url/listing">test</a></div>
          <div class="a-card__price">100000</div>
        </div>
        """
        results = self.parser.parse(html, SearchParams())
        if results:
            assert results[0].url == "https://full.url/listing" or results[0].url.startswith("https")


class TestParseUtilsExtended:

    def test_parse_int_undefined(self):
        assert parse_int("") is None
        assert parse_int(None) is None
        assert parse_int("abc") is None

    def test_parse_int_with_spaces(self):
        assert parse_int("150 000") == 150000
        assert parse_int("  100  ") == 100

    def test_parse_int_with_currency(self):
        assert parse_int("150 000 ₸ /мес") == 150000
        assert parse_int("$200") == 200

    def test_parse_float_comma_decimal(self):
        assert parse_float("42,5 м²") == 42.5

    def test_parse_float_dot_decimal(self):
        assert parse_float("42.5 м²") == 42.5

    def test_parse_float_no_decimal(self):
        assert parse_float("50 м²") == 50.0

    def test_parse_float_nbsp(self):
        assert parse_float("42\xa050") == 42.0  # nbsp treated as space, finds first number

    def test_parse_rooms_variations(self):
        assert parse_rooms("1-комнатная") == 1
        assert parse_rooms("2-комн") == 2
        assert parse_rooms("3 комн.") == 3
        assert parse_rooms("Студия") == 0
        assert parse_rooms("студия") == 0
        assert parse_rooms("4к") == 4
        assert parse_rooms("5-к") == 5

    def test_parse_rooms_negative_cases(self):
        assert parse_rooms("") is None
        assert parse_rooms(None) is None
        assert parse_rooms("квартира") is None

    def test_parse_floor_pair_variations(self):
        assert parse_floor_pair("3/5") == (3, 5)
        assert parse_floor_pair("3\\5") == (3, 5)
        assert parse_floor_pair("3/5 этаж") == (3, 5)
        assert parse_floor_pair("1 этаж") == (1, None)
        assert parse_floor_pair("5эт") == (5, None)
        assert parse_floor_pair("нет данных") == (None, None)

    def test_parse_floor_pair_none(self):
        assert parse_floor_pair(None) == (None, None)
        assert parse_floor_pair("") == (None, None)
