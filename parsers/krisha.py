from __future__ import annotations

import re
from typing import Optional

from bs4 import BeautifulSoup

from .base import BaseParser, parse_floor_pair, parse_float, parse_int, parse_rooms
from .models import Listing, SearchParams


class KrishaParser(BaseParser):
    """krisha.kz — крупнейший портал недвижимости Казахстана."""
    name = "krisha.kz"
    base_url = "https://krisha.kz"
    search_path = "/arenda/kvartiry/almaty/"
    # Category page title: "Аренда квартир помесячно в Алматы: ... на Крыше"
    category_title_markers = ("квартир", "квартира", "недвижимост")
    max_pages = 9
    # Enrich EVERY listing with detail-page gallery photos (the search card
    # exposes only the first frame). Also pulls map coords for the heatmap.
    enrich_photo_count = 1000
    # ajaxPhones needs the session that loaded the detail page (cookies +
    # Referer); krisha.py passes it via fetch_session.
    phone_endpoint_cooldown = 4.0

    def _build_url_base(self, params: SearchParams) -> str:
        parts: list[str] = ["sort=submit_date"]
        for r in params.rooms:
            parts.append(f"das[_a.rooms][]={r}")
        if params.price_min:
            parts.append(f"das[_a.price:from]={params.price_min}")
        if params.price_max:
            parts.append(f"das[_a.price:to]={params.price_max}")
        if params.district:
            parts.append(f"das[_a.district]={params.district}")
        return f"{self.base_url}{self.search_path}?{'&'.join(parts)}"

    def build_url(self, params: SearchParams, page: int = 1) -> str:
        url = self._build_url_base(params)
        if page > 1:
            url += f"&page={page}"
        return url

    def detect_status(self, html: str) -> Optional[str]:
        """Krisha marks archived / possibly-relevant listings with a visible
        banner. Detect it so the favorites card can show (and persist) it."""
        if not html:
            return None
        low = html.lower()
        # Authoritative: krisha embeds the listing state in the page JSON
        # ("status": "archive" for archived ads, "live" for active ones).
        m = re.search(r'"status"\s*:\s*"(\w+)"', html)
        if m and m.group(1).lower() == "archive":
            return "В архиве"
        # Fallback: the visible "Объявление может быть неактуальным" banner.
        if "is-archived-text" in low or "может быть неактуальным" in low:
            return "Объявление может быть неактуальным"
        return super().detect_status(html)

    def extract_detail_price(self, html: str, url: str) -> tuple:
        """Krisha detail pages embed listing data in a ``<script id="jsdata">``
        JSON blob. Price is at ``"price":650000``."""
        m = re.search(r'"price"\s*:\s*(\d+)', html)
        price = int(m.group(1)) if m else None
        lat, lon = None, None
        m = re.search(r'"map"\s*:\s*\{[^}]*"lat"\s*:\s*([\d.]+)[^}]*"lon"\s*:\s*([\d.]+)', html)
        if m:
            try:
                lat, lon = float(m.group(1)), float(m.group(2))
            except (ValueError, TypeError):
                pass
        if price is None:
            return super().extract_detail_price(html, url)
        return (price, lat, lon)

    def _parse_soup(self, soup: BeautifulSoup, params: SearchParams) -> list[Listing]:
        listings: list[Listing] = []
        for card in soup.select("div.a-card"):
            a = card.select_one("a.a-card__title")
            if not a:
                continue
            title = a.get_text(strip=True)
            if not title:
                continue
            href = a.get("href", "")
            url = self.base_url + href if href.startswith("/") else href

            price_el = card.select_one(".a-card__price")
            price_text = price_el.get_text(" ", strip=True) if price_el else ""
            price = parse_int(price_text)

            subtitle_el = card.select_one(".a-card__subtitle")
            address = subtitle_el.get_text(" ", strip=True) if subtitle_el else ""

            info_text = card.get_text(" ")
            rooms = parse_rooms(title)
            area = self._extract_area(title) or self._extract_area(info_text)
            floor, total_f = parse_floor_pair(title)
            if not floor and not total_f:
                floor, total_f = parse_floor_pair(info_text)

            # Collect all photos from the card
            photos = self._extract_photos(card)

            desc_el = card.select_one(".a-card__text-preview")
            description = desc_el.get_text(strip=True) if desc_el else ""

            date_published, date_updated = self.extract_dates(info_text)

            listings.append(Listing(
                title=title, price=price, rooms=rooms, area=area, floor=floor,
                total_floors=total_f, url=url, source=self.name,
                photo=photos[0] if photos else "",
                address=address, description=description,
                date_published=date_published, date_updated=date_updated,
            ))
            # Store extra photos as comma-separated in description prefix
            if len(photos) > 1:
                listings[-1].photo = "|".join(photos)
        return listings

    @staticmethod
    def _extract_photos(card) -> list[str]:
        """Extract distinct listing photo URLs from a card.

        krisha search cards only carry one real photo, exposed in up to two
        formats (source srcset .webp + img .jpg). Older code grabbed those
        duplicates AND tooltip/UI sprites (tooltip-hot.svg etc.), which made the
        front-end carousel render the same frame three times plus identical
        icons — looking like "all photos are the same".
        """
        photos: list[str] = []
        seen_ids: set[str] = set()
        # Restrict to the main image container so UI icons elsewhere are skipped
        container = card.select_one(".a-card__image") or card
        for img in container.select("picture source, picture img, img"):
            src = (img.get("srcset")
                   or img.get("src")
                   or img.get("data-src")
                   or img.get("data-full-src")
                   or "")
            if not src or src.startswith("data:"):
                continue
            # srcset can be "url 1x, url 2x" — take first
            if " " in src:
                src = src.split(",")[0].strip().split(" ")[0]
            # Skip static UI sprites / icons (not listing photos)
            if "/static/" in src or src.lower().endswith(".svg"):
                continue
            # Dedupe the same photo in different formats (webp/jpg/png/avif)
            # by comparing the URL without its extension.
            photo_id = re.sub(r"\.(webp|jpe?g|png|avif)$", "", src, flags=re.I)
            if photo_id in seen_ids:
                continue
            seen_ids.add(photo_id)
            photos.append(src)
        return photos[:10]  # limit to 10 photos

    @staticmethod
    def _extract_area(text: str) -> float | None:
        # Superscript is required and a Cyrillic letter must not follow,
        # otherwise "5 мин" / "3 м" (distance) matched as area.
        m = re.search(r"([\d.,]+)\s*м[²^2·•](?![а-яё])", text)
        return parse_float(m.group(0)) if m else None

    def _fetch_detail_photos(self, listing: Listing) -> list[str]:
        """Fetch the krisha listing detail page and extract all gallery photos."""
        try:
            session, html = self.fetch_session(listing.url)
            soup = self.parse_html(html)
        except Exception:
            return []
        # Phone is hidden behind a click-to-show button; the raw value is
        # in the page source (data attrs / JSON state / text).
        self._enrich_phone(listing, html, session)
        # krisha embeds the map location in a JSON blob on the detail page.
        coords = re.search(r'"map":\{"lat":([\d.]+),"lon":([\d.]+)', html)
        if coords:
            try:
                listing.lat = float(coords.group(1))
                listing.lon = float(coords.group(2))
            except (ValueError, IndexError):
                pass
        photos: list[str] = []
        seen: set[str] = set()
        # krisha gallery uses .gallery__main (main image) + .gallery__small-item (thumbnails)
        for img in soup.select(".gallery__main img, .gallery__small-item img, .gallery__container img"):
            src = (img.get("src") or img.get("data-src") or "")
            if not src or src.startswith("data:") or "/static/" in src:
                continue
            if src.startswith("//"):
                src = "https:" + src
            # Upgrade thumbnail to full-size: replace size suffix like -120x90 with -750x470
            src = re.sub(r"-\d+x\d+\.(webp|jpe?g|png|avif)$", r"-750x470.\1",
                         src, flags=re.I)
            # Dedupe by photo number (same photo can appear in multiple sizes)
            photo_id = re.sub(r"\.(webp|jpe?g|png|avif)$", "", src, flags=re.I)
            if photo_id in seen:
                continue
            seen.add(photo_id)
            photos.append(src)
        return photos[:10]
