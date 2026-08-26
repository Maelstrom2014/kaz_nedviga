"""Parser fixture tests: verify each parser extracts listings from sample HTML."""
import pytest

from parsers.models import SearchParams
from parsers.krisha import KrishaParser
from parsers.olx import OlxParser
from parsers.kn import KnParser
from parsers.etagi import EtagiParser

from .loaders import load_fixture

ALL_PARSERS = [
    (KrishaParser, "krisha"),
    (OlxParser, "olx"),
    (KnParser, "kn"),
    (EtagiParser, "etagi"),
]


@pytest.mark.parametrize("parser_cls,fixture_name", ALL_PARSERS)
def test_parser_extracts_two_listings(parser_cls, fixture_name):
    html = load_fixture(fixture_name)
    parser = parser_cls()
    params = SearchParams()
    results = parser.parse(html, params)
    assert len(results) == 2, f"{parser.name}: expected 2, got {len(results)}"


@pytest.mark.parametrize("parser_cls,fixture_name", ALL_PARSERS)
def test_parser_listing_has_title_and_url(parser_cls, fixture_name):
    html = load_fixture(fixture_name)
    parser = parser_cls()
    params = SearchParams()
    results = parser.parse(html, params)
    for item in results:
        assert item.title, f"{parser.name}: empty title"
        assert item.url, f"{parser.name}: empty URL for '{item.title}'"
        assert item.source == parser.name


@pytest.mark.parametrize("parser_cls,fixture_name", ALL_PARSERS)
def test_parser_extracted_price(parser_cls, fixture_name):
    html = load_fixture(fixture_name)
    parser = parser_cls()
    params = SearchParams()
    results = parser.parse(html, params)
    assert results[0].price is not None, f"{parser.name}: price None"
    assert results[0].price > 0, f"{parser.name}: price not positive"


@pytest.mark.parametrize("parser_cls,fixture_name", ALL_PARSERS)
def test_parser_build_url(parser_cls, fixture_name):
    parser = parser_cls()
    params = SearchParams(rooms=[1], price_min=50000, price_max=200000, district="Алмалинский")
    url = parser.build_url(params)
    assert url.startswith("http"), f"{parser.name}: URL doesn't start with http"
    assert parser.name.split(".")[0] in url or parser.base_url in url


def test_krisha_rooms_parsed():
    html = load_fixture("krisha")
    parser = KrishaParser()
    results = parser.parse(html, SearchParams())
    assert results[0].rooms == 1
    assert results[1].rooms == 2


def test_krisha_area_parsed():
    html = load_fixture("krisha")
    parser = KrishaParser()
    results = parser.parse(html, SearchParams())
    assert results[0].area == 45.0
    assert results[1].area == 65.0


def test_krisha_floor_parsed():
    html = load_fixture("krisha")
    parser = KrishaParser()
    results = parser.parse(html, SearchParams())
    assert results[0].floor == 3
    assert results[0].total_floors == 5
    assert results[1].floor == 5
    assert results[1].total_floors == 9


def test_krisha_photo_not_duplicated():
    # Regression: a search card exposes the same frame twice (source srcset
    # .webp + img .jpg) plus tooltip UI sprites (tooltip-hot.svg etc.).
    # The carousel then rendered the same photo several times → "all photos
    # the same". Expect a single distinct photo per card, no UI icons.
    html = load_fixture("krisha")
    parser = KrishaParser()
    results = parser.parse(html, SearchParams())
    assert len(results) == 2
    for r in results:
        photos = r.photo.split("|") if r.photo else []
        assert len(photos) == 1, f"{r.title}: expected 1 photo, got {photos}"
        assert photos[0].endswith(".webp"), f"unexpected photo: {photos[0]}"
        assert "/static/" not in photos[0]
        assert not photos[0].endswith(".svg")
    assert results[0].photo != results[1].photo


def test_filter_by_price():
    html = load_fixture("krisha")
    parser = KrishaParser()
    params = SearchParams(price_max=200000)
    results = parser.parse(html, params)
    filtered = parser.apply(results, params)
    assert len(filtered) == 1
    assert filtered[0].price == 150000


def test_filter_by_rooms():
    html = load_fixture("krisha")
    parser = KrishaParser()
    params = SearchParams(rooms=[2])
    results = parser.parse(html, params)
    filtered = parser.apply(results, params)
    assert len(filtered) == 1
    assert filtered[0].rooms == 2


def test_filter_by_district():
    html = load_fixture("krisha")
    parser = KrishaParser()
    params = SearchParams(district="Бостандык")
    results = parser.parse(html, params)
    filtered = parser.apply(results, params)
    assert len(filtered) == 1
    assert "Бостандык" in filtered[0].address


def test_empty_html_returns_empty():
    parser = KrishaParser()
    results = parser.parse("<html><body></body></html>", SearchParams())
    assert results == []


JUNK_CARD = ('<div class="offer-wrapper" data-cy="l-card">'
             '<a data-cy="ad-href" href="/d/obyavlenie/1/">'
             '<h6>Отдам даром ковер</h6></a>'
             '<p data-testid="ad-price">0 тг</p>'
             '<span data-cy="location">Экибастуз</span>'
             '<img src="https://olx.kz/img/carpet.jpg"></div>')


@pytest.mark.parametrize("parser_cls,fixture_name", ALL_PARSERS)
def test_parser_rejects_generic_search_page(parser_cls, fixture_name):
    """A page whose title is not the estate category must yield nothing,
    even if it contains parseable cards (people's belongings, pets, ...)."""
    page = (f'<html><head><title>Котята в добрые руки - объявления</title></head>'
            f'<body><h1>Объявления</h1>{load_fixture(fixture_name)}</body></html>')
    parser = parser_cls()
    assert parser.parse(page, SearchParams()) == []


@pytest.mark.parametrize("parser_cls,fixture_name", ALL_PARSERS)
def test_parser_accepts_category_page(parser_cls, fixture_name):
    page = (f'<html><head><title>Аренда квартир в Алматы</title></head>'
            f'<body>{load_fixture(fixture_name)}</body></html>')
    parser = parser_cls()
    results = parser.parse(page, SearchParams())
    assert len(results) == 2, f"{parser.name}: category page rejected"


def test_parser_without_title_still_parses():
    # Fragments (no <title>/<h1>) have nothing to validate against.
    parser = OlxParser()
    assert len(parser.parse(load_fixture("olx"), SearchParams())) == 2
