from __future__ import annotations

import json
import re

from bs4 import BeautifulSoup

from .base import BaseParser, parse_float, parse_floor_pair, parse_int, parse_rooms
from .models import Listing, SearchParams


class EtagiParser(BaseParser):
    """etagi.com (Этажи) — агентство недвижимости.

    WAF ("Security check" 403 page with a human-confirmation checkbox)
    findings (measured live):
    - the clean URL /realty_rent/ always serves a full SSR page whose
      embedded app state (``var data={...}``) holds ~30 complete listings
      in ``lists.rents`` — 5x more than the 6 cards rendered in HTML;
    - ANY query string (``?page=N``, ``?room=N``, ...) gets an instant
      403 challenge, and probing while blocked escalates the block.
    Strategy: one request to the clean URL, parse the embedded JSON state,
    apply filters locally (BaseParser.apply). SSR card parsing stays as a
    fallback. One sticky session, one fingerprint, no retries on 403 —
    behave like one calm browser tab.
    """
    enrich_photo_count = 1000  # fetch all photos from detail pages
    name = "etagi.com"
    base_url = "https://almaty.etagi.com"
    search_path = "/realty_rent/"
    # Category page title: "...Снять квартиру в Алматы...аренда квартир..."
    category_title_markers = ("квартир", "квартира", "недвижимост")

    # One clean request carries the full state batch (~30 listings);
    # ?page=N URLs are WAF-gated, so there is no usable pagination.
    max_pages = 3
    min_page_size = 5  # 6 SSR cards / ~30 state items — never "the last page"

    # The WAF escalates on request bursts and on changing TLS
    # fingerprints. Behave like one calm browser tab instead: a single
    # persistent session (cookies persist, one fingerprint), human-like
    # pacing between pages, and fail immediately when blocked — the block
    # lifts on its own, and retrying only escalates it.
    use_cffi = True
    cffi_impersonate = "chrome"
    cffi_profiles = ("chrome",)
    session_sticky = True
    fail_fast_on_waf = True
    page_delay = (3.0, 5.0)

    _STATE_RE = re.compile(r"var data=(\{.*)", re.S)

    def _build_url_base(self, params: SearchParams) -> str:
        # No query string at all: the WAF challenges any ?param= URL with
        # a 403 "Security check" page. Filters are applied locally by
        # BaseParser.apply() after parsing the full state batch.
        return f"{self.base_url}{self.search_path}"

    def build_url(self, params: SearchParams, page: int = 1) -> str:
        # ?page=N is WAF-gated (instant 403). Every page resolves to the
        # same clean URL; page 2+ (only when the user raises max_pages)
        # just re-fetches the same batch, which dedupe collapses.
        return self._build_url_base(params)

    def extract_detail_price(self, html: str, url: str) -> tuple:
        """Etagi detail pages embed the full listing data in ``var data={...}``.
        The state parser already handles this; reuse it and find the ticket
        ID from the URL to match the right listing."""
        m = self._STATE_RE.search(html)
        if not m:
            return super().extract_detail_price(html, url)
        try:
            data = json.loads(self._balanced_json(m.group(1)))
        except (ValueError, RecursionError):
            return super().extract_detail_price(html, url)
        # Detail page state: the listing itself is at top level (not in lists.rents)
        price = data.get("price")
        try:
            price = int(price) if price is not None else None
        except (TypeError, ValueError):
            price = None
        lat, lon = None, None
        la, lo = data.get("la"), data.get("lo")
        try:
            lat = float(la) if la not in (None, "") else None
        except (TypeError, ValueError):
            pass
        try:
            lon = float(lo) if lo not in (None, "") else None
        except (TypeError, ValueError):
            pass
        if price is None:
            # Try lists.rents (detail page sometimes includes the full state)
            rents = ((data.get("lists") or {}).get("rents")) or []
            for item in rents:
                if isinstance(item, dict):
                    listing = self._rent_to_listing(item)
                    if listing and listing.price is not None:
                        if url.rstrip("/").endswith(str(item.get("_ticket_id", ""))):
                            return (listing.price, listing.lat, listing.lon)
            return super().extract_detail_price(html, url)
        return (price, lat, lon)

    def parse(self, html: str, params: SearchParams) -> list[Listing]:
        """Prefer the embedded JSON state (~30 full listings); fall back
        to SSR card parsing when the state blob is missing or broken."""
        listings = self._parse_state(html)
        if listings:
            return listings
        return super().parse(html, params)

    def _parse_state(self, html: str) -> list[Listing]:
        m = self._STATE_RE.search(html)
        if not m:
            return []
        try:
            data = json.loads(self._balanced_json(m.group(1)))
        except (ValueError, RecursionError):
            return []
        rents = ((data.get("lists") or {}).get("rents")) or []
        listings: list[Listing] = []
        for item in rents:
            if isinstance(item, dict):
                listing = self._rent_to_listing(item)
                if listing:
                    listings.append(listing)
        return listings

    @staticmethod
    def _balanced_json(blob: str) -> str:
        """Cut the first balanced top-level {...} out of the blob."""
        depth = 0
        in_str = False
        esc = False
        for i, ch in enumerate(blob):
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return blob[: i + 1]
        return blob

    def _rent_to_listing(self, r: dict) -> Listing | None:
        ticket = r.get("_ticket_id")
        if not ticket:
            return None
        meta = r.get("meta") or {}
        city = meta.get("city") or "Алматы"
        district = meta.get("district") or ""
        street = meta.get("street") or ""
        house = str(r.get("house_num") or "").strip()

        address = ", ".join(p for p in
                            (city, district, street,
                             f"д. {house}" if house else "") if p)

        rooms = r.get("rooms")
        try:
            rooms = int(rooms) if rooms is not None else None
        except (TypeError, ValueError):
            rooms = None
        is_apart = r.get("type") == "apart"
        noun = "апартаменты" if is_apart else "кв."
        if rooms == 0 or r.get("studio"):
            rooms = 0
            rooms_part = ("Апартаменты" if is_apart else "Студия")
        elif rooms:
            rooms_part = f"{rooms}-комн. {noun}"
        else:
            rooms_part = ""

        area = r.get("square")
        try:
            area = float(area) if area not in (None, "") else None
        except (TypeError, ValueError):
            area = None

        floor = parse_int(r.get("floor"))
        total_f = parse_int(r.get("floors"))

        parts: list[str] = []
        if rooms_part:
            parts.append(rooms_part)
        if area:
            parts.append(f"{area:g} м²")
        if floor:
            parts.append(f"{floor}/{total_f} эт." if total_f else f"{floor} эт.")
        if address:
            parts.append(address)
        title = ", ".join(parts)
        if not title:
            return None

        price = parse_int(r.get("price"))
        la, lo = r.get("la"), r.get("lo")
        try:
            lat = float(la) if la not in (None, "") else None
        except (TypeError, ValueError):
            lat = None
        try:
            lon = float(lo) if lo not in (None, "") else None
        except (TypeError, ValueError):
            lon = None

        return Listing(
            title=title,
            price=price,
            currency="тг",
            rooms=rooms,
            area=area,
            floor=floor,
            total_floors=total_f,
            address=address,
            url=f"{self.base_url}/realty_rent/{ticket}/",
            source=self.name,
            photo=r.get("main_photo") or "",
            lat=lat,
            lon=lon,
        )

    def _parse_soup(self, soup: BeautifulSoup, params: SearchParams) -> list[Listing]:
        listings: list[Listing] = []
        # Etagi uses esoft.digital CMS with hashed CSS class names.
        # Select cards by data-testid and link pattern for robustness.
        for card in soup.select('[data-testid="object_card"], div.templates-object-card'):
            a = card.find("a", href=re.compile(r"/realty_rent/\d+"))
            if not a:
                continue
            href = a.get("href", "")
            url = href if href.startswith("http") else self.base_url + href

            info = card.get_text(" ")

            # The photo slider counter "1/14" is NOT a floor — drop it from
            # the text before any floor parsing.
            counter = card.select_one('[data-testid="slider_counter"]')
            if counter:
                ctext = counter.get_text(strip=True)
                if ctext:
                    info = info.replace(ctext, " ")

            # Price: dedicated testid (live site) -> regex fallback (fixtures)
            price_el = card.select_one('[data-testid="object_card_price"]')
            if price_el:
                price = parse_int(price_el.get_text(" ", strip=True))
            else:
                price = self._extract_price_from_text(info)

            rooms = parse_rooms(info)
            area = self._extract_area(info)

            # Floor: "3/5 эт." (live) / "2/5 этаж" (fixtures). The "эт"
            # suffix is required so photo counters can never match.
            floor, total_f = None, None
            m = re.search(r"(\d{1,2})\s*/\s*(\d{1,2})\s*эт", info, re.I)
            if m:
                floor, total_f = int(m.group(1)), int(m.group(2))
            else:
                floor, total_f = parse_floor_pair(info)

            # Address: div.address (fixtures) / .EDAsp (live) / regex
            address = ""
            addr_el = card.select_one("div.address") or card.select_one(".EDAsp")
            if addr_el:
                address = re.sub(r"\s+", " ", addr_el.get_text(" ", strip=True)).strip()
                address = address.replace(" ,", ",").rstrip(",.")
            else:
                address = self._extract_address(info)

            # Title: .card-title link (fixtures); the live card has no
            # title element, so build one from the extracted fields.
            title = ""
            title_a = card.select_one("a.card-title")
            if title_a:
                title = title_a.get_text(strip=True)
            if not title:
                parts: list[str] = []
                # Live SSR cards use "X-комн. кв." for flats and
                # "X-комн. апарт." for apartments — match both so the
                # title carries the property type.
                rooms_m = re.search(r"(\d+-комн\.?\s*)(кв|апарт)\.?", info, re.I)
                if rooms_m:
                    noun = "апартаменты" if rooms_m.group(2).lower().startswith("апарт") else "кв."
                    parts.append(f"{rooms_m.group(1)}{noun}")
                elif re.search(r"студи[ои]", info, re.I):
                    parts.append("Студия")
                if area:
                    parts.append(f"{area:g} м²")
                if floor:
                    parts.append(f"{floor}/{total_f} эт." if total_f else f"{floor} эт.")
                if address:
                    parts.append(address)
                title = ", ".join(parts)
            if not title:
                continue

            # Photo from the media slider (live) or any img/source
            img = card.select_one('img[data-testid="object_card_mediaslider"]') or card.select_one("img")
            photo = ""
            if img:
                photo = (img.get("src") or img.get("data-src") or "")
            if not photo:
                source = card.select_one("source")
                if source and source.get("srcSet"):
                    photo = source["srcSet"].split(",")[0].strip().split(" ")[0]
            if photo.startswith("//"):
                photo = "https:" + photo

            date_published, date_updated = self.extract_dates(info)

            listings.append(Listing(
                title=title, price=price, rooms=rooms, area=area, floor=floor,
                total_floors=total_f, url=url, source=self.name, photo=photo,
                address=address,
                date_published=date_published, date_updated=date_updated,
            ))
        return listings

    def _fetch_detail_photos(self, listing: Listing) -> list[str]:
        """Fetch all photos from the etagi detail page gallery.

        The detail page has a gallery slider (``[data-testid="object_page_gallery_slider"]``)
        whose ``<img>`` tags point at ``cdn.esoft.digital/*/cluster/photos/*.jpeg``.
        The same page also embeds the full app state in ``var data={...}``
        with a ``photos`` array. Collect from both, normalize to full-size
        ``/content/`` URLs, and dedup by the photo hash.
        """
        try:
            # Cached detail fetch (TTL disk cache) — same fetch stack
            # (cffi + WAF handling), repeat fetches within TTL are free.
            html = self.fetch_detail_html(listing.url)
        except Exception:
            return []

        # Phone: etagi hides it behind a "show number" button, but the raw
        # value sits in the page source (state JSON / data attrs / text).
        self._enrich_phone(listing, html)

        photos: list[str] = []
        seen: set[str] = set()

        # 1) Gallery slider: all <img> inside the slider div
        soup = BeautifulSoup(html, "html.parser")
        gallery = soup.select_one('[data-testid="object_page_gallery_slider"]')
        if gallery:
            for img in gallery.find_all("img"):
                src = img.get("src") or img.get("data-src") or ""
                # Only accept etagi CDN photos — skip 2gis maps, avatars, etc.
                if "cdn.esoft.digital" not in src:
                    continue
                url = self._normalize_photo_url(src)
                if url and url not in seen:
                    seen.add(url)
                    photos.append(url)

        # 2) Regex fallback: collect every cdn.esoft.digital photo URL on
        #    the page (thumbnails and larger shared by the slider).
        if not photos:
            for m in re.finditer(
                r"(?:https?:)?//cdn\.esoft\.digital/[^\"'<>\s]*?/cluster/photos/[^\"'<>\s]+\.jpe?g",
                html, re.I,
            ):
                url = self._normalize_photo_url(m.group(0))
                if url and url not in seen:
                    seen.add(url)
                    photos.append(url)

        # 3) Embedded state: the detail page's ``photos`` array has objects
        #    with ``url`` fields pointing at full-size images.
        state_m = self._STATE_RE.search(html)
        if state_m:
            try:
                data = json.loads(self._balanced_json(state_m.group(1)))
                photos_field = data.get("photos")
                if isinstance(photos_field, list):
                    for p in photos_field:
                        if isinstance(p, dict):
                            url = p.get("url") or p.get("big") or p.get("src") or ""
                            url = self._normalize_photo_url(url)
                            if url and url not in seen:
                                seen.add(url)
                                photos.append(url)
            except (ValueError, RecursionError):
                pass

        return photos

    @staticmethod
    def _normalize_photo_url(url: str) -> str:
        """Normalize an etagi CDN photo URL to full-size ``/content/``.

        Etagi serves the same photo at multiple sizes:
        ``cdn.esoft.digital/320240/cluster/photos/HASH/FILE.jpeg`` (thumb)
        ``cdn.esoft.digital/640480/cluster/photos/HASH/FILE.jpeg`` (medium)
        ``cdn.esoft.digital/content/cluster/photos/HASH/FILE.jpeg``       (full)

        Replace the size segment with ``/content/`` so every duplicate image
        normalizes to the same full-size URL and dedup works.
        """
        if not url:
            return ""
        if url.startswith("//"):
            url = "https:" + url
        # Replace /123456/ or /123x456/ (size segment) with /content/
        url = re.sub(
            r"//cdn\.esoft\.digital/\d+x?\d*/cluster/",
            "//cdn.esoft.digital/content/cluster/",
            url,
        )
        return url

    @staticmethod
    def _extract_price_from_text(text: str) -> int | None:
        # Look for "230 000 ₸" pattern
        m = re.search(r"(\d[\d\s]{3,})\s*[₸т]", text)
        if m:
            return parse_int(m.group(0))
        return None

    @staticmethod
    def _extract_area(text: str) -> float | None:
        # Superscript is required and a Cyrillic letter must not follow,
        # otherwise "5 мин" / "3 м" (distance) matched as area. The live
        # markup renders the sup as a separate text node ("45 м 2"), so a
        # space between "м" and the exponent is allowed.
        m = re.search(r"([\d.,]+)\s*м\s*[²^2·•](?![а-яё])", text)
        return parse_float(m.group(0)) if m else None

    @staticmethod
    def _extract_address(text: str) -> str:
        # Etagi addresses look like "Алматы, ул. Макатаева" — cut before date/time markers
        m = re.search(r"(Алматы,[^—|]*?)(?=\s*(?:сегодня|вчера|позавчера|обновл|\d{1,2}\s+(?:янв|февр|март|апр|ма[йя]|июн|июл|авг|сент|окт|ноя|дек)))", text, re.IGNORECASE)
        if m:
            return m.group(1).strip().rstrip(",.")
        m = re.search(r"(Алматы,[^—|]*)", text)
        if m:
            return m.group(0).strip().rstrip(",.")
        return ""
