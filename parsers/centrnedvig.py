from __future__ import annotations

import re

from bs4 import BeautifulSoup

from .base import BaseParser, parse_float, parse_floor_pair, parse_int, parse_rooms
from .models import Listing, SearchParams


class CentrnedvigParser(BaseParser):
    """centrnedvig.kz — агентство недвижимости."""
    name = "centrnedvig.kz"
    base_url = "https://centrnedvig.kz"
    search_path = "/arenda/almaty/kvartiry/"

    max_pages = 1

    def _build_url_base(self, params: SearchParams) -> str:
        parts: list[str] = []
        if params.rooms:
            parts.append(f"room={min(params.rooms)}")
        if params.price_min:
            parts.append(f"price_from={params.price_min}")
        if params.price_max:
            parts.append(f"price_to={params.price_max}")
        qs = ("?" + "&".join(parts)) if parts else ""
        return f"{self.base_url}{self.search_path}{qs}"

    def _parse_soup(self, soup: BeautifulSoup, params: SearchParams) -> list[Listing]:
        listings: list[Listing] = []
        for card in soup.select("div.object-card, div.card-item, .catalog-item"):
            a = card.select_one("a.title, .object-title a, h3 a, a[href*='/object/']")
            if not a:
                continue
            title = a.get_text(strip=True)
            href = a.get("href", "")
            url = href if href.startswith("http") else self.base_url + href
            price = self._extract_price(card)
            info = card.get_text(" ")
            rooms = parse_rooms(title)
            area = self._extract_area(info)
            floor, total_f = parse_floor_pair(info)
            loc = card.select_one(".object-address, .address, .location")
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
        el = card.select_one(".price, .object-price")
        return parse_int(el.get_text(" ")) if el else None

    @staticmethod
    def _extract_area(text: str) -> float | None:
        # Superscript required, no Cyrillic letter after: "5 мин" is not an area.
        m = re.search(r"([\d.,]+)\s*м[²^2·•](?![а-яё])", text)
        return parse_float(m.group(0)) if m else None
