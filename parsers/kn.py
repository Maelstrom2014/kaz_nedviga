from __future__ import annotations

import re

from bs4 import BeautifulSoup

from .base import BaseParser, parse_float, parse_floor_pair, parse_int, parse_rooms
from .models import Listing, SearchParams


class KnParser(BaseParser):
    """kn.kz — Казахстанская недвижимость."""
    name = "kn.kz"
    base_url = "https://www.kn.kz"
    search_path = "/almaty/arenda-kvartir"
    # Category page title: "Аренда квартир помесячно в Алматы - снять квартиру"
    category_title_markers = ("квартир", "квартира", "недвижимост")
    max_pages = 6
    # Enrich every listing with the detail-page swiper gallery (the search
    # card only shows the first photo).
    enrich_photo_count = 1000

    def _build_url_base(self, params: SearchParams) -> str:
        parts: list[str] = []
        if params.rooms:
            parts.append(f"rooms={min(params.rooms)}")
        if params.price_min:
            parts.append(f"price_from={params.price_min}")
        if params.price_max:
            parts.append(f"price_to={params.price_max}")
        qs = ("?" + "&".join(parts)) if parts else ""
        return f"{self.base_url}{self.search_path}{qs}"

    def _parse_soup(self, soup: BeautifulSoup, params: SearchParams) -> list[Listing]:
        listings: list[Listing] = []
        seen_cards: set[int] = set()
        for card in soup.select("div[data-object-id], div[class*=kn-shadow-hover]"):
            if id(card) in seen_cards:
                continue
            seen_cards.add(id(card))
            # Find the title link - it has meaningful text, not just "16 фото"
            title_a = None
            for a in card.find_all('a', href=True):
                if '/card/' in a.get('href', ''):
                    text = a.get_text(strip=True)
                    if text and len(text) > 10:
                        title_a = a
                        break
            if not title_a:
                continue
            title = title_a.get_text(strip=True)
            href = title_a.get("href", "")
            url = self.base_url + href if href.startswith("/") else href

            # Price: look for spans with numbers + currency symbol
            price_el = None
            for span in card.find_all('span'):
                txt = span.get_text(strip=True)
                # Look for price pattern: digits + space + optional symbol
                if re.match(r"^[\d\s]{4,}\s*[₸т]", txt) or (len(txt) < 25 and re.search(r"\d{4,}", txt)):
                    price_el = span
                    break
            price_text = price_el.get_text(" ", strip=True) if price_el else ""
            price = parse_int(price_text)

            info_text = card.get_text(" ")
            rooms = parse_rooms(title)

            # Floor: the live card has a dedicated pair of spans
            # <span class="fw-bold">1/5</span><span>этаж</span>. The title
            # must NOT be regex-scraped for floors — house numbers like
            # "ул. Халиуллина, 196/17" look exactly like floor pairs.
            floor, total_f = None, None
            for span in card.select("span.fw-bold"):
                nxt = span.find_next_sibling("span")
                if nxt and "этаж" in nxt.get_text():
                    m = re.match(r"(\d{1,2})\s*/\s*(\d{1,2})", span.get_text(strip=True))
                    if m:
                        floor, total_f = int(m.group(1)), int(m.group(2))
                    break
            if not floor and not total_f:
                floor, total_f = parse_floor_pair(title)
                if not floor and not total_f:
                    floor, total_f = parse_floor_pair(info_text)

            # Area: dedicated "N м²" span (live) -> regex fallback (fixtures)
            area = None
            for span in card.select("span.fw-bold"):
                txt = span.get_text(strip=True)
                if "м²" in txt:
                    area = parse_float(txt)
                    break
            if area is None:
                area = self._extract_area(title) or self._extract_area(info_text)

            # The street is embedded in the title: "ул. Халиуллина, 196/17",
            # "1 мкр., 74". Strip area/floor fragments first so they don't
            # pollute the street match.
            address = self._extract_address(title)

            img = card.select_one("img")
            photo = (img.get("src") or img.get("data-src") or "") if img else ""

            # Collect all photos
            all_photos = []
            for img_tag in card.select("img"):
                src = img_tag.get("src") or img_tag.get("data-src") or ""
                if src and not src.startswith("data:") and src not in all_photos:
                    all_photos.append(src)
            if all_photos:
                photo = "|".join(all_photos[:10])

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

    @staticmethod
    def _extract_address(title: str) -> str:
        """Pull the street out of a title like
        "2-комнатная квартира, ул. Халиуллина, 196/17" or
        "2-комнатная квартира, 1 мкр., 74"."""
        if not title:
            return ""
        # Drop area/floor fragments so "45 м²" / "3/5 этаж" don't leak in.
        t = re.sub(r"[\d.,]+\s*м[²^2·•]\s*", " ", title)
        t = re.sub(r"\d{1,2}\s*/\s*\d{1,2}\s*эт\w*", " ", t)
        parts = [p.strip() for p in t.split(",") if p.strip()]
        street = ""
        for i, part in enumerate(parts):
            if re.search(r"ул(ица)?\.?|пр(оспект)?\.?|ш(оссе)|б-р\.?|мкр\.?", part, re.I):
                street = part
                # A following bare house number ("196/17", "54") belongs to
                # the street.
                if i + 1 < len(parts) and re.fullmatch(r"\d+(?:/\d+)?", parts[i + 1]):
                    street += ", " + parts[i + 1]
                break
        return street

    def _fetch_detail_photos(self, listing: Listing) -> list[str]:
        """Fetch the kn.kz listing detail page and extract gallery photos.

        kn.kz uses a swiper gallery with photos in two sizes:
        ``my_thumb`` (small) and ``gallery_big`` (large), sharing the same
        UUID. We collect the ``gallery_big`` variants only.

        Also extracts precise map coordinates: the detail page has a
        ``<turbo-frame id="card-map-{ID}" src="/card/map/{ID}">`` placeholder.
        Fetching that frame URL returns a Turbo Stream fragment with
        ``data-kn-simple-map-latitude-value`` / ``data-kn-simple-map-longitude-value``
        attributes carrying the exact pin coordinates.
        """
        try:
            html = self.fetch(listing.url)
            soup = self.parse_html(html)
        except Exception:
            return []

        # Extract photos
        photos: list[str] = []
        seen: set[str] = set()
        for img in soup.select(".swiper img, .gallery img, img"):
            src = (img.get("src") or img.get("data-src") or "")
            if not src or "/gallery_big/" not in src:
                continue
            if src in seen:
                continue
            seen.add(src)
            photos.append(src)

        # Extract coordinates from the lazy map frame
        self._extract_map_coords(listing, soup)

        return photos[:10]

    def _extract_map_coords(self, listing: Listing, soup: BeautifulSoup) -> None:
        """Fetch the /card/map/{ID} turbo-frame and set listing.lat/lon.

        kn.kz loads the map lazily: the detail page has a
        ``<turbo-frame id="card-map-{ID}" src="/card/map/{ID}">`` placeholder.
        Fetching that src returns a Turbo Stream HTML fragment with
        ``data-kn-simple-map-latitude-value`` and
        ``data-kn-simple-map-longitude-value`` attributes carrying the
        exact coordinates. Coordinates outside the Almaty bounding box
        are dropped as a safety net (malformed data, relocated listing).
        """
        frame = soup.select_one('turbo-frame[id^="card-map-"]')
        if not frame or not frame.get("src"):
            return
        map_url = frame["src"]
        if not map_url.startswith("http"):
            map_url = self.base_url + map_url
        try:
            map_html = self.fetch(map_url)
        except Exception:
            return
        m = re.search(
            r'data-kn-simple-map-latitude-value="(-?\d+\.\d+)"'
            r'.*?data-kn-simple-map-longitude-value="(-?\d+\.\d+)"',
            map_html, re.S,
        )
        if not m:
            return
        try:
            lat = float(m.group(1))
            lon = float(m.group(2))
        except (ValueError, TypeError):
            return
        # Almaty bounding box — reject coords that are clearly elsewhere.
        if 43.14 <= lat <= 43.37 and 76.78 <= lon <= 77.08:
            listing.lat = lat
            listing.lon = lon
