"""Tests for date extraction: helper functions and parser integration."""
from datetime import date, timedelta

import pytest

from parsers.base import parse_russian_date, extract_dates
from parsers.models import SearchParams
from parsers.krisha import KrishaParser
from parsers.olx import OlxParser
from parsers.kn import KnParser
from parsers.etagi import EtagiParser

from .loaders import load_fixture


class TestParseRussianDate:

    def test_none_empty(self):
        assert parse_russian_date("") is None
        assert parse_russian_date(None) is None
        assert parse_russian_date("нет даты") is None

    def test_today(self):
        assert parse_russian_date("сегодня") == date.today().isoformat()
        assert parse_russian_date("Сегодня") == date.today().isoformat()

    def test_yesterday(self):
        expected = (date.today() - timedelta(days=1)).isoformat()
        assert parse_russian_date("вчера") == expected

    def test_day_before_yesterday(self):
        expected = (date.today() - timedelta(days=2)).isoformat()
        assert parse_russian_date("позавчера") == expected

    def test_full_month_year(self):
        assert parse_russian_date("12 августа 2024") == "2024-08-12"

    def test_short_month(self):
        assert parse_russian_date("10 авг 2024") == "2024-08-10"

    def test_month_implicit_year(self):
        assert parse_russian_date("5 марта") == f"{date.today().year}-03-05"

    def test_dot_date(self):
        assert parse_russian_date("10.08.2024") == "2024-08-10"

    def test_dash_date(self):
        assert parse_russian_date("10-08-2024") == "2024-08-10"

    def test_iso_date(self):
        assert parse_russian_date("2024-08-05") == "2024-08-05"

    def test_two_digit_year(self):
        assert parse_russian_date("10.08.24") == "2024-08-10"

    def test_invalid_day(self):
        assert parse_russian_date("35 августа 2024") is None

    def test_not_a_month(self):
        assert parse_russian_date("12 алматы 2024") is None

    def test_embedded_in_text(self):
        assert parse_russian_date("Сегодня в 14:30") == date.today().isoformat()


class TestExtractDates:

    def test_empty(self):
        assert extract_dates("") == ("", "")

    def test_published_only(self):
        assert extract_dates("Алматы 12 августа 2024") == ("2024-08-12", "")

    def test_updated_keyword(self):
        assert extract_dates("обновлено 10 авг 2024") == ("", "2024-08-10")

    def test_published_and_updated(self):
        text = "Опубликовано 5 авг 2024 Обновлено 10 авг 2024"
        pub, upd = extract_dates(text)
        assert pub == "2024-08-05"
        assert upd == "2024-08-10"

    def test_two_unlabelled_dates(self):
        text = "12 августа 2024 и 10 авг 2024"
        pub, upd = extract_dates(text)
        assert pub == "2024-08-12"
        assert upd == "2024-08-10"

    def test_does_not_parse_partial_year(self):
        # "августа 2024" must not be read as "августа 20" (day 20 of year 2024)
        assert extract_dates("августа 2024") == ("", "")

    def test_word_then_digits_not_misread(self):
        # "Алматы 12" alone is not a date (no month), "12 августа 2024" is
        pub, upd = extract_dates("Алматы 12 августа 2024")
        assert pub == "2024-08-12"
        assert upd == ""


ALL = [
    (KrishaParser, "krisha", "2024-08-12"),
    (OlxParser, "olx", "2024-08-12"),
    (EtagiParser, "etagi", "2024-08-12"),
]


@pytest.mark.parametrize("parser_cls,fixture,expected_pub", ALL)
def test_parser_extracts_publication_date(parser_cls, fixture, expected_pub):
    html = load_fixture(fixture)
    parser = parser_cls()
    results = parser.parse(html, SearchParams())
    assert len(results) == 2
    assert results[0].date_published == expected_pub, (
        f"{parser.name}: expected {expected_pub}, got {results[0].date_published}"
    )
    # Second card has an "обновлено" date -> date_updated populated
    assert results[1].date_updated != "", f"{parser.name}: expected update date"


def test_kn_extracts_relative_date():
    html = load_fixture("kn")
    parser = KnParser()
    results = parser.parse(html, SearchParams())
    assert len(results) == 2
    assert results[0].date_published == date.today().isoformat()
    assert results[1].date_updated == "2024-08-10"


    results = parser.parse(html, SearchParams())
    assert len(results) == 2
    assert results[0].date_published == date.today().isoformat()
    assert results[1].date_updated == "2024-08-10"
