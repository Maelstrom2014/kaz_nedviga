from __future__ import annotations

import re

from bs4 import BeautifulSoup

from .base import BaseParser, parse_float, parse_floor_pair, parse_int, parse_rooms
from .models import Listing, SearchParams


class MarketParser(BaseParser):
    """market.kz — доска объявлений Kaspi."""
    name = "market.kz"
    base_url = "https://market.kz"
    search_path = "/announcements/almaty/q-kvartira/"

    max_pages = 1

    def _build_url_base(self, params: SearchParams) -> str:
        parts: list[str] = []
        if params.query:
            parts.append(f"q-{params.query}")
        if params.rooms:
            parts.append(f"rooms={min(params.rooms)}")
        qs = ("?" + "&".join(parts[1:])) if len(parts) > 1 else ""
        path = self.search_path if not params.query else f"/announcements/almaty/{parts[0].replace(' ','-')}/"
        return f"{self.base_url}{path}{qs}"

    def _parse_soup(self, soup: BeautifulSoup, params: SearchParams) -> list[Listing]:
        listings: list[Listing] = []
        for card in soup.select("div.cards-list__item, div.mk-card, div.list-item"):
            a = card.select_one("a.card__title, a.title, h3 a, a[href*='/announcements/']")
            if not a:
                continue
            title = a.get_text(strip=True)
            href = a.get("href", "")
            url = self.base_url + href if href.startswith("/") else href
            price = self._extract_price(card)
            info = card.get_text(" ")
            rooms = parse_rooms(title)
            area = self._extract_area(info)
            floor, total_f = parse_floor_pair(info)
            loc = card.select_one(".card__address, .address, .location")
            address = loc.get_text(" ", strip=True) if loc else ""
            img = card.select_one("img")
            photo = (img.get("src") or img.get("data-src") or "") if img else ""
            date_published, date_updated = self.extract_dates(info)
            listings.append(Listing(
                title=title, price=price, rooms=rooms, area=area, floor=floor,
                total_floors=total_f, url=url, source=self.name, photo=photo,
                address=address,
                date_published=date_published, date_updated=date_updated,
            ))
        return listings

    @staticmethod
    def _extract_price(card) -> int | None:
        el = card.select_one(".card__price, .price, .price-value")
        if not el:
            return None
        return parse_int(el.get_text(" "))

    @staticmethod
    def _extract_area(text: str) -> float | None:
        m = re.search(r"([\d.,]+)\s*м[²^2]?", text)
        return parse_float(m.group(0)) if m else None
