from __future__ import annotations

import re

from bs4 import BeautifulSoup

from .base import BaseParser, parse_float, parse_floor_pair, parse_int, parse_rooms
from .models import Listing, SearchParams


class KvartirkaParser(BaseParser):
    """kvartirka.kz — портал аренды квартир."""
    enrich_photo_count = 0  # no detail-page gallery enrichment
    name = "kvartirka.kz"
    base_url = "https://kvartirka.kz"
    search_path = "/search/rent/apartment"
    # Category page title: "Kvartirka.kz - Недвижимости"
    category_title_markers = ("квартир", "квартира", "недвижимост")
    max_pages = 3

    def _build_url_base(self, params: SearchParams) -> str:
        parts: list[str] = []
        if params.rooms:
            parts.append(f"rooms={min(params.rooms)}")
        if params.price_min:
            parts.append(f"price_from={params.price_min}")
        if params.price_max:
            parts.append(f"price_to={params.price_max}")
        # region_id=2 is Алматы (1 = Шымкент; the site lists only these two
        # cities). Must be set, otherwise the feed defaults to Шымкент.
        # As of 2026-08 the site has no Алматы listings at all — the parser
        # then honestly returns 0 results (see _parse_soup filters).
        parts.append("region_id=2")
        qs = ("?" + "&".join(parts)) if parts else "?region_id=2"
        return f"{self.base_url}{self.search_path}{qs}"

    def _parse_soup(self, soup: BeautifulSoup, params: SearchParams) -> list[Listing]:
        listings: list[Listing] = []
        for card in soup.select("div.box-house"):
            # The "Аренда квартир" feed mixes in other cities (Шымкент) and
            # daily/hourly rentals — the app only wants Алматы monthly ones.
            city_el = (card.select_one("ul.meta-list li")
                       or card.select_one("ul li"))
            city = city_el.get_text(strip=True).lower() if city_el else ""
            if city and "алматы" not in city:
                continue

            # Price unit: "₸/сут", "сутки", "/час" → daily/hourly rental.
            price_el = (card.select_one("h5.price") or card.select_one(".price")
                        or card.select_one("span[class*=price]"))
            price_text = price_el.get_text(" ", strip=True) if price_el else ""
            if re.search(r"сут|час", price_text, re.IGNORECASE):
                continue

            # Title: h5.title on current markup, .description on old markup.
            # Chained select_one: a comma group returns the first match in
            # DOCUMENT order, which can pick h3 above the .description div.
            title = ""
            desc_el = (card.select_one("h5.title") or card.select_one(".title")
                       or card.select_one(".description")
                       or card.select_one("h4") or card.select_one("h3"))
            if desc_el:
                title = desc_el.get_text(strip=True)
            if not title:
                # Fallback: find link with text
                for a in card.find_all('a', href=True):
                    t = a.get_text(strip=True)
                    if t and len(t) > 3:
                        title = t
                        break
            if not title:
                # Last resort: any text node
                for el in card.find_all(string=True):
                    t = el.strip()
                    if t and len(t) > 5 and t not in ('', ' '):
                        title = t
                        break
            if not title:
                continue

            # URL from first link
            a = card.find('a', href=True)
            href = a.get("href", "") if a else ""
            url = href if href.startswith("http") else self.base_url + href

            price = parse_int(price_text)

            info_text = card.get_text(" ")
            rooms = parse_rooms(title) or parse_rooms(info_text)
            area = self._extract_area(info_text)
            floor, total_f = parse_floor_pair(info_text)

            # Address from li elements or .city (chained: explicit priority)
            addr_el = (card.select_one(".city") or card.select_one(".address")
                       or card.select_one(".location") or card.select_one("li.text-1"))
            address = addr_el.get_text(strip=True) if addr_el else ""

            img = card.select_one("img")
            photo = (img.get("src") or img.get("data-src") or "") if img else ""

            date_published, date_updated = self.extract_dates(info_text)

            listings.append(Listing(
                title=title, price=price, rooms=rooms, area=area, floor=floor,
                total_floors=total_f, url=url, source=self.name, photo=photo,
                address=address,
                date_published=date_published, date_updated=date_updated,
            ))
        return listings

    @staticmethod
    def _extract_area(text: str) -> float | None:
        # Superscript is required and a Cyrillic letter must not follow,
        # otherwise "5 мин" / "3 м" (distance) matched as area.
        m = re.search(r"([\d.,]+)\s*м[²^2·•](?![а-яё])", text)
        return parse_float(m.group(0)) if m else None
