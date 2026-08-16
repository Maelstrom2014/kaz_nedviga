from __future__ import annotations

import re

from bs4 import BeautifulSoup

from .base import BaseParser, log, parse_float, parse_int, parse_rooms
from .models import Listing, SearchParams


class OlxParser(BaseParser):
    """olx.kz — популярная доска объявлений."""
    # The search card shows only the first photo; the detail page has the
    # full swiper gallery (div[data-testid=ad-photo] img). Enrich every
    # listing so all gallery photos are available (not just the top N).
    enrich_photo_count = 1000
    name = "olx.kz"
    base_url = "https://www.olx.kz"
    search_path = "/nedvizhimost/arenda-kvartiry/alm/"
    # The category page title is "Аренда квартир ... на OLX.kz". If OLX ever
    # serves a generic search page instead (WAF redirect, path change), the
    # title won't mention apartments and the page is skipped rather than
    # parsed into unrelated classifieds (belongings, pets, ...).
    category_title_markers = ("квартир", "квартира", "недвижимост")

    max_pages = 6
    # OLX sits behind a CloudFront WAF that fingerprints TLS: plain
    # python-requests gets 403, browser-impersonated traffic passes.
    use_cffi = True

    def _build_url_base(self, params: SearchParams) -> str:
        parts: list[str] = []
        # NOTE: no rooms filter in the URL — OLX's
        # filter_float_number_of_rooms returns an EMPTY result page for
        # rent-apartment listings (verified live: 0 cards for from=0..1).
        # Rooms filtering happens client-side in BaseParser.parse() via
        # SearchParams.matches_rooms(), which also keeps listings whose
        # room count is unknown (OLX cards usually don't show it).
        if params.price_min:
            parts.append(f"search[filter_float_price:from]={params.price_min}")
        if params.price_max:
            parts.append(f"search[filter_float_price:to]={params.price_max}")
        qs = ("?" + "&".join(parts)) if parts else ""
        return f"{self.base_url}{self.search_path}{qs}"

    def _parse_soup(self, soup: BeautifulSoup, params: SearchParams) -> list[Listing]:
        listings: list[Listing] = []
        for card in soup.select("div[data-cy=l-card], div.offer-wrapper"):
            # Title link: OLX changed selectors multiple times.
            # New (2024+): the title <a> lives inside [data-testid=ad-card-title];
            # the first <a href*="/d/obyavlenie/"> is an image wrapper with empty text.
            # Old: a[data-cy=ad-href] wrapping <h6>title</h6>.
            a = card.select_one("[data-testid=ad-card-title] a") \
                or card.select_one("a[data-cy=ad-href]") \
                or card.select_one("h6 a")
            if not a:
                # Last resort: first /d/ link with non-empty text (skip image wrappers)
                for link in card.select("a[href*='/d/obyavlenie/']"):
                    if link.get_text(strip=True):
                        a = link
                        break
            if not a:
                continue
            title = a.get_text(strip=True)
            if not title:
                continue
            href = a.get("href", "")
            url = self.base_url + href if href.startswith("/") else href

            price = self._extract_price(card)
            info = card.get_text(" ")
            rooms = parse_rooms(title) or self._extract_rooms(info)
            area = self._extract_area(info)

            # Location: new uses data-testid=location-date, old uses data-cy=location.
            # The element also carries the posting date (" - Сегодня в 14:32");
            # keep only the place part.
            loc = card.select_one("[data-testid=location-date], [data-cy=location], p small, .l-height small")
            address = loc.get_text(" ", strip=True) if loc else ""
            if " - " in address:
                address = address.split(" - ")[0].strip()

            img = card.select_one("img")
            photo = ""
            if img:
                photo = self._canonical_photo(self._best_photo(img))

            date_published, date_updated = self.extract_dates(info)

            listings.append(Listing(
                title=title, price=price, rooms=rooms, area=area, url=url,
                source=self.name, photo=photo, address=address,
                date_published=date_published, date_updated=date_updated,
            ))
        return listings

    @staticmethod
    def _canonical_photo(url: str) -> str:
        """Strip the ``;s=WxH`` size suffix so the same image (card vs
        gallery) collapses to one entry when merged."""
        return url.split(";")[0] if url else url

    @staticmethod
    def _extract_price(card) -> int | None:
        el = card.select_one("[data-testid=ad-price], p.price strong, .price, span.css-1q7r6v9")
        if not el:
            return None
        return parse_int(el.get_text(" "))

    @staticmethod
    def _extract_rooms(text: str) -> int | None:
        m = re.search(r"(\d+)\s*комн", text, re.I)
        return int(m.group(1)) if m else None

    @staticmethod
    def _extract_area(text: str) -> float | None:
        # Superscript is required and a Cyrillic letter must not follow,
        # otherwise "5 мин" / "3 м" (distance) matched as area.
        m = re.search(r"([\d.,]+)\s*м[²^2·•](?![а-яё])", text)
        return parse_float(m.group(0)) if m else None

    @staticmethod
    def _best_photo(img) -> str:
        """Pick the largest srcset variant; skip 'no_thumbnail' placeholders."""
        srcset = img.get("srcset") or ""
        if srcset:
            candidates = [part.strip().split(" ")[0]
                          for part in srcset.split(",") if part.strip()]
            # Last entry is the largest (OLX lists widths ascending).
            for url in reversed(candidates):
                if "no_thumbnail" not in url:
                    return url
        src = (img.get("src") or img.get("data-src") or "")
        if "no_thumbnail" in src:
            return ""
        return src

    def _fetch_detail_photos(self, listing: Listing) -> list[str]:
        """Fetch the detail page and return all gallery photo URLs.

        Also extracts the map coordinates OLX embeds as an escaped JSON
        blob (``\"map\":{\"lat\":..,\"lon\":..}``). OLX hides the exact pin
        (``show_detailed:false``) so these are area-centre coords.

        Coordinates are only accepted when the listing is actually in
        Almaty — OLX search results include nearby towns (Тельмана,
        Узынагаш, Абай, ...) whose coords land far outside the city and
        put markers in wrong places on the map. We check ``cityName``
        from the JSON state and also clamp to an Almaty bounding box as
        a safety net.
        """
        try:
            html = self.fetch(listing.url)
        except Exception as exc:
            log.debug("[%s] detail fetch failed for %s: %s",
                      self.name, listing.url[:60], exc)
            return []

        # cityName from the escaped JSON state — the reliable way to tell
        # whether this listing is in Almaty or a nearby town.
        city_m = re.search(r'\\"cityName\\"\s*:\s*\\"([^"\\]+)\\"', html)
        city_name = city_m.group(1) if city_m else ""
        is_almaty = city_name.lower() in ("алматы", "almaty")

        # Map coords: escaped JSON (most common) or plain JSON.
        m = re.search(
            r'\\"?map\\"?\s*:\s*\{\s*\\"?zoom\\"?\s*:\s*\d+\s*,\s*'
            r'\\"?lat\\"?\s*:\s*([\d.]+)\s*,\s*\\"?lon\\"?\s*:\s*([\d.]+)',
            html)
        if m:
            try:
                lat = float(m.group(1))
                lon = float(m.group(2))
                # Almaty bounding box (covers all 8 districts + small margin):
                #   lat 43.14 – 43.37,  lon 76.78 – 77.08
                # Listings outside this box are not in Almaty (Тельмана,
                # Узынагаш, Абай, Караганда, ...). Drop their coords so
                # the heatmap/marker doesn't pin them in wrong locations.
                if is_almaty and 43.14 <= lat <= 43.37 and 76.78 <= lon <= 77.08:
                    listing.lat = lat
                    listing.lon = lon
                elif is_almaty:
                    # In Almaty but coords are off (geocoding glitch) —
                    # accept the cityName but drop the raw coords.
                    log.debug("[%s] Almaty listing but coords out of range (%.5f, %.5f) — %s",
                              self.name, lat, lon, listing.url[:60])
                else:
                    log.debug("[%s] non-Almaty listing (%s) — coords dropped (%.5f, %.5f) — %s",
                              self.name, city_name, lat, lon, listing.url[:60])
            except (ValueError, IndexError):
                pass

        soup = self.parse_html(html)
        photos: list[str] = []
        for slide in soup.select("div[data-testid=ad-photo] img"):
            src = slide.get("src") or slide.get("data-src") or ""
            if not src or "no_thumbnail" in src:
                continue
            # Drop the ";s=1000x818" size suffix — keep the canonical URL
            # so duplicates across the card/gallery collapse on merge.
            canon = src.split(";")[0]
            if canon not in photos:
                photos.append(canon)
        return photos
