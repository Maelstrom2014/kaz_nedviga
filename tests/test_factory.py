"""Tests for the parser factory."""
from parsers.factory import (
    PARSER_CLASSES,
    PARSER_REGISTRY,
    get_all_parsers,
    get_parser,
    list_sites,
)


def test_registry_has_all_parsers():
    assert len(PARSER_CLASSES) == 6
    assert len(PARSER_REGISTRY) == 6


def test_all_parsers_unique_names():
    names = [p.name for p in get_all_parsers()]
    assert len(names) == len(set(names)), "Duplicate parser names"


def test_get_parser_by_name():
    p = get_parser("krisha.kz")
    assert p.name == "krisha.kz"


def test_get_parser_unknown_raises():
    try:
        get_parser("nonexistent.kz")
        assert False, "Should have raised"
    except ValueError:
        pass


def test_list_sites():
    sites = list_sites()
    assert len(sites) == 6
    for s in sites:
        assert "name" in s
        assert "url" in s
        assert s["url"].startswith("http")
