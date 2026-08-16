from __future__ import annotations

from .base import BaseParser
from .krisha import KrishaParser
from .olx import OlxParser
from .kn import KnParser
from .etagi import EtagiParser
from .kvartirka import KvartirkaParser
from .telegram import TelegramParser

# Working parsers only. The following were removed because the sites are
# dead or have fundamentally changed:
#   MarketParser    — market.kz absorbed into kaspi.kz (404)
#   AllrealtyParser — allrealty.kz domain dead (SSL)
#   DomikParser     — domik.kz domain dead (SSL)
#   CentrnedvigParser — centrnedvig.kz domain dead (SSL)
#   ArendaParser    — arenda.kz is commercial real estate only (no apartments)

PARSER_CLASSES: list[type[BaseParser]] = [
    KrishaParser,
    OlxParser,
    KnParser,
    EtagiParser,
    KvartirkaParser,
    TelegramParser,
]

PARSER_REGISTRY: dict[str, type[BaseParser]] = {cls.name: cls for cls in PARSER_CLASSES}


def get_parser(name: str) -> BaseParser:
    cls = PARSER_REGISTRY.get(name)
    if cls is None:
        raise ValueError(f"Unknown parser: {name}. Available: {list(PARSER_REGISTRY)}")
    return cls()


def get_all_parsers() -> list[BaseParser]:
    return [cls() for cls in PARSER_CLASSES]


def list_sites() -> list[dict]:
    return [
        {"name": cls.name, "url": cls.base_url}
        for cls in PARSER_CLASSES
    ]
