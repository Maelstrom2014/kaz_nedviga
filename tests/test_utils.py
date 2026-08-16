"""Tests for utility parsing functions in base.py"""
import pytest
from parsers.base import parse_int, parse_float, parse_rooms, parse_floor_pair


def test_parse_int():
    assert parse_int("150 000 ₸ /мес") == 150000
    assert parse_int("100,000 тг") == 100000
    assert parse_int(None) is None
    assert parse_int("нет цены") is None
    assert parse_int("0") == 0


def test_parse_float():
    assert parse_float("45 м²") == 45.0
    assert parse_float("65.5 м²") == 65.5
    assert parse_float("42,3") == 42.3
    assert parse_float(None) is None
    assert parse_float("abc") is None


def test_parse_rooms():
    assert parse_rooms("1-комнатная квартира") == 1
    assert parse_rooms("2-комн") == 2
    assert parse_rooms("3 комн.") == 3
    assert parse_rooms("студия") == 0
    assert parse_rooms("Студия") == 0
    assert parse_rooms(None) is None
    assert parse_rooms("квартира без комнат") is None


def test_parse_floor_pair():
    assert parse_floor_pair("3/5 этаж") == (3, 5)
    assert parse_floor_pair("5/9 эт.") == (5, 9)
    assert parse_floor_pair("2 этаж") == (2, None)
    assert parse_floor_pair("нет данных") == (None, None)
    assert parse_floor_pair(None) == (None, None)
