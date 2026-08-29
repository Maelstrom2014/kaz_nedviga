from __future__ import annotations

import json
import re

from bs4 import BeautifulSoup

from .base import BaseParser, log, parse_float, parse_int, parse_rooms, phone_log
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
    # One persistent cffi session: the phone-reveal API answers for the
    # session that loaded the detail page (cookies), so reuse it.
    session_sticky = True
    # A WAF block here is IP-based and lifts on its own: fail fast (one
    # request) instead of rotating profiles + falling back to requests, and
    # back off with a bounded cooldown so a short block clears within the run.
    fail_fast_on_waf = True
    waf_max_retries = 2
    waf_cooldown = 20.0
    waf_max_cooldown = 60.0
    # /api/v1/offers/{id}/phones soft-blocks (400) rapid successive calls.
    # Serialize across the 6-thread detail pool + space calls ~1.2s apart so
    # OLX sees a steady single stream, then retry transient 400/429/503.
    phone_endpoint_retries = 2
    phone_endpoint_cooldown = 3.0
    phone_endpoint_min_interval = 1.2
    # Optional: reveal phones with a real headless browser (Playwright).
    # OLX masks the number and only reveals it after a click that passes
    # its device-ID bot challenge, which plain HTTP calls to the /phones
    # API cannot get past (they are blocked with 400 "suspicious activity").
    # Only works from residential/clean IPs; keep off by default.
    phone_playwright_enabled = False
    phone_max_attempts = 3
    phone_retry_delay = 2.0

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

    def extract_detail_price(self, html: str, url: str) -> tuple:
        """OLX detail pages have no search cards, so the base parse()-based
        fallback never finds a listing here. Price sources in priority
        order: the schema.org JSON-LD block (``offers.price`` — canonical
        for live ads), the rendered ``[data-testid=ad-price]`` /
        ``ad-price-container`` element, then the embedded JSON state
        (escaped ``\\"price\\":120000`` or plain ``"price":120000``).
        Coordinates come from the map JSON (Almaty-only, see
        _extract_map_coords)."""
        price = self._price_from_jsonld(html)
        if price is None:
            soup = self.parse_html(html)
            # Chained select_one for explicit priority: the main ad's
            # [data-testid=ad-price] (or its container) must win over any
            # ".price" element of a "similar listings" card further down.
            el = (soup.select_one("[data-testid=ad-price]")
                  or soup.select_one("[data-testid=ad-price-container]")
                  or soup.select_one("p.price strong, .price"))
            price = parse_int(el.get_text(" ")) if el else None
        if price is None:
            # JSON state: backslash optional — OLX ships the state both as
            # escaped JS string (\"price\":) and plain JSON-LD ("price":).
            m = re.search(r'\\?"price\\?"\s*:\s*(\d+)', html)
            if m:
                price = int(m.group(1))
        if price is None:
            return super().extract_detail_price(html, url)
        lat, lon = self._extract_map_coords(html)
        return (price, lat, lon)

    @staticmethod
    def _price_from_jsonld(html: str) -> int | None:
        """Price from the schema.org JSON-LD block in <head>.

        OLX embeds the live listing as ``Product`` with an ``offers``
        object carrying ``price`` + ``priceCurrency``. It is the canonical
        "this ad exists" signal: removed ads have no such block.
        """
        for m in re.finditer(
                r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>',
                html, re.S):
            try:
                data = json.loads(m.group(1))
            except ValueError:
                continue
            items = data if isinstance(data, list) else [data]
            # Some OLX variants wrap the graph in {"@graph": [...]}.
            for item in list(items):
                if isinstance(item, dict) and isinstance(item.get("@graph"), list):
                    items.extend(item["@graph"])
            for item in items:
                if not isinstance(item, dict):
                    continue
                offers = item.get("offers")
                if isinstance(offers, list):
                    offers = offers[0] if offers and isinstance(offers[0], dict) else None
                if not isinstance(offers, dict):
                    continue
                price = offers.get("price")
                if isinstance(price, (int, float)) and price > 0:
                    return int(price)
                if isinstance(price, str):
                    parsed = parse_int(price)
                    if parsed:
                        return parsed
        return None

    def is_unavailable(self, html: str) -> bool:
        """True when the detail page no longer holds a live ad.

        NOTE: the i18n string "Объявление больше не доступно" is embedded
        in the JS bundle of EVERY OLX page, so a raw substring match marks
        live ads as gone (regression of 1cfa3d8). Instead: a live ad always
        exposes its price — schema.org JSON-LD and/or the rendered
        ad-price element. A removed ad has neither.
        """
        if self._price_from_jsonld(html) is not None:
            return False
        soup = self.parse_html(html)
        el = (soup.select_one("[data-testid=ad-price]")
              or soup.select_one("[data-testid=ad-price-container]"))
        return not (el and parse_int(el.get_text(" ")))

    def _extract_map_coords(self, html: str) -> tuple:
        """Return (lat, lon) from the detail page's map JSON, or (None, None).

        OLX hides the exact pin (``show_detailed:false``) so these are
        area-centre coords. Accepted only when the listing is in Almaty —
        ``cityName`` from the JSON state plus a bounding-box clamp (covers
        all 8 districts + margin). OLX search results include nearby towns
        (Тельмана, Узынагаш, Абай, ...) whose coords would pin markers in
        wrong places on the map.
        """
        city_m = re.search(r'\\"cityName\\"\s*:\s*\\"([^"\\]+)\\"', html)
        city_name = city_m.group(1) if city_m else ""
        is_almaty = city_name.lower() in ("алматы", "almaty")
        m = re.search(
            r'\\"?map\\"?\s*:\s*\{\s*\\"?zoom\\"?\s*:\s*\d+\s*,\s*'
            r'\\"?lat\\"?\s*:\s*([\d.]+)\s*,\s*\\"?lon\\"?\s*:\s*([\d.]+)',
            html)
        if not m:
            return None, None
        try:
            lat, lon = float(m.group(1)), float(m.group(2))
        except (ValueError, IndexError):
            return None, None
        if is_almaty and 43.14 <= lat <= 43.37 and 76.78 <= lon <= 77.08:
            return lat, lon
        log.debug("[%s] coords dropped (city=%s, lat=%.5f, lon=%.5f) — %s",
                  self.name, city_name, lat, lon, "detail page")
        return None, None

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

    def _discover_phone_endpoints(self, html: str) -> list[str]:
        """OLX hides the number behind "Показать телефон" and keeps no
        endpoint string in the page source, but the ad ID is present
        (ad-id= links / JSON-LD sku). The number itself is served by the
        offers API for the page's own session.

        Two variants are tried in order: the dedicated /phones endpoint
        (small payload) and the offer-detail endpoint (fallback, in case
        /phones is soft-blocked or omits the number)."""
        found = super()._discover_phone_endpoints(html)
        ad_id = self._extract_ad_id(html)
        if ad_id:
            for p in ("/phones", ""):
                url = "%s/api/v1/offers/%s%s" % (self.base_url, ad_id, p)
                if url not in found:
                    found.append(url)
        return found

    @staticmethod
    def _extract_ad_id(html: str, url: str | None = None) -> str | None:
        """Numeric OLX ad id used by the offers API.

        Checked in order of specificity: the promote-link ad-id= param,
        the JSON-LD sku, the embedded state's offerId / adId / data-ad-id
        fields, then a numeric id in the listing URL. Returns None when
        nothing matches."""
        for rx in (
            re.compile(r'ad-id=(\d{6,})'),
            re.compile(r'"sku"\s*:\s*"(\d{6,})"'),
            re.compile(r'"offerId"\s*:\s*"?(?P<ad_id>\d{6,})'),
            re.compile(r'offer[_-]?id["\s:=]+["\']?(\d{6,})'),
            re.compile(r'adId["\s:=]+["\']?(\d{6,})'),
            re.compile(r'data-ad-id=["\']?(\d{6,})'),
        ):
            m = rx.search(html or "")
            if m:
                return m.group(1)
        if url:
            m = re.search(r'/(\d{6,})(?=[/?#]|$)', url)
            if m:
                return m.group(1)
        return None

    @staticmethod
    def _clean_phone(text: str) -> str:
        digits = "".join(ch for ch in (text or "") if ch.isdigit())
        if len(digits) in (10, 11) and digits[0] in "78":
            return digits
        return ""

    def _phone_via_endpoint(self, url: str, ad_id: str | None = None) -> tuple[str | None, bool]:
        """Fetch the number from the offers /phones API (fast path).

        Returns (phone, alive): the cleaned number (or None) and whether
        the offer still looks live. alive=False when the detail page is
        not 200 or has no ad id (removed offer); alive=True when the
        endpoint call itself failed or returned nothing (retryable). When
        ad_id is supplied the detail page is not re-fetched.
        """
        import time
        try:
            from curl_cffi import requests as cr
        except ImportError:
            return None, True
        if ad_id is None:
            try:
                resp = cr.get(url, impersonate="chrome", timeout=self.timeout,
                              headers={"Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8"})
            except Exception:
                return None, True
            ad_id = self._extract_ad_id(resp.text, url) if resp.status_code == 200 else None
        if not ad_id:
            return None, False
        now = time.time()
        last = getattr(self, "_pw_phone_ts", 0.0)
        if now - last < self.phone_endpoint_min_interval:
            time.sleep(self.phone_endpoint_min_interval - (now - last))
        try:
            pr = cr.get("%s/api/v1/offers/%s/phones" % (self.base_url, ad_id),
                        impersonate="chrome", timeout=self.timeout)
        except Exception:
            return None, True
        self._pw_phone_ts = time.time()
        if pr.status_code != 200:
            return None, True
        body = pr.text if isinstance(pr.text, str) else None
        if body is None:
            try:
                body = json.dumps(pr.json())
            except Exception:
                return None, True
        for phone in self._phones_from_api_json(body):
            if phone and phone not in self.phone_blacklist:
                return phone, True
        return None, True

    @staticmethod
    def _phones_from_api_json(text) -> list[str]:
        """Phone numbers embedded in an OLX offers-API JSON body.

        Understands the shapes OLX serves - a "phones" array of strings
        or objects, a scalar phone/tel value, and numbers nested under
        contact/seller keys - and falls back to a raw scan when the body
        is not valid JSON. Returns cleaned 10/11-digit numbers, contact
        keys first, in document order.
        """
        if not isinstance(text, str):
            try:
                text = json.dumps(text)
            except Exception:
                return []
        found: list = []

        def emit(value) -> None:
            if value is None:
                return
            phone = OlxParser._clean_phone(str(value))
            if phone and phone not in found:
                found.append(phone)

        def phoneish(key) -> bool:
            low = str(key).lower()
            return any(k in low for k in ("phone", "tel", "contact", "seller"))

        def emit_under(value) -> None:
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        for v in item.values():
                            emit(v)
                    else:
                        emit(item)
            elif isinstance(value, dict):
                for v in value.values():
                    emit(v)
            else:
                emit(value)

        def targeted(node) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    if phoneish(key):
                        emit_under(value)
                    targeted(value)
            elif isinstance(node, list):
                for item in node:
                    targeted(item)

        def all_scalars(node) -> None:
            if isinstance(node, dict):
                for v in node.values():
                    all_scalars(v)
            elif isinstance(node, list):
                for v in node:
                    all_scalars(v)
            elif isinstance(node, (str, int)):
                emit(node)

        data = None
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            data = None
        if data is not None:
            targeted(data)
            if not found:
                all_scalars(data)
        if not found:
            m = re.search(r'"phones"\s*:\s*\[([^\]]*)\]', text)
            if m:
                for part in re.findall(r'"([^"]+)"', m.group(1)):
                    emit(part)
            for m in re.finditer(
                    r'"(?:phone|tel|telephone|mobile)"\s*:\s*"([^"]{6,20})"', text):
                emit(m.group(1))
        return found

    def _phone_from_embedded_state(self, html: str) -> str:
        """Phone number from OLX's embedded JSON state (page source).

        OLX preloads offer state into inline <script> JSON blobs. When a
        number is present there (some layouts / seller types) this
        recovers it without a round-trip to the offers API.
        """
        for m in re.finditer(
                r"""<script[^>]*type=["']application/(?:ld\+)?json["']>(.*?)</script>""",
                html or "", re.I | re.S):
            for phone in self._phones_from_api_json(m.group(1)):
                if phone and phone not in self.phone_blacklist:
                    return phone
        return ""

    def _phone_via_playwright(self, url: str) -> str | None:
        """Render the detail page in a headless browser and read the
        revealed number. Returns None when the browser is missing or
        fails, or when no valid number is revealed."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return None
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled"])
                ctx = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                    locale="ru-RU", viewport={"width": 1366, "height": 900},
                    extra_http_headers={"Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8"})
                page = ctx.new_page()
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(3500)
                    container = page.locator('[data-testid="phones-container"]')
                    if container.count():
                        found = self._clean_phone(container.first.inner_text(timeout=2000))
                        if found:
                            return found
                    for i in range(page.locator('[data-testid="show-phone"]').count()):
                        btn = page.locator('[data-testid="show-phone"]').nth(i)
                        try:
                            if btn.is_visible():
                                btn.scroll_into_view_if_needed()
                                btn.click(timeout=4000, force=True)
                        except Exception:
                            continue
                        for _ in range(11):
                            page.wait_for_timeout(1000)
                            if container.count():
                                found = self._clean_phone(container.first.inner_text(timeout=1000))
                                if found:
                                    return found
                finally:
                    browser.close()
        except Exception:
            return None
        return None

    def _fetch_phone_playwright(self, listing: Listing, url: str,
                                ad_id: str | None = None) -> str | None:
        """Cycle the /phones endpoint and the headless browser until a
        number is extracted or the attempts are exhausted. The winning
        method is recorded in phone_extraction.log."""
        import time
        for attempt in range(1, self.phone_max_attempts + 1):
            phone, alive = self._phone_via_endpoint(url, ad_id)
            if phone:
                listing.phone = phone
                self._record_phone_outcome(listing, "found:olx_endpoint")
                phone_log.info("[%s] %s | found %s | method=olx_endpoint attempt=%d",
                               getattr(listing, "id", None) or listing.url,
                               listing.url, listing.phone, attempt)
                return phone
            if not alive:
                self._record_phone_outcome(listing, "no_phone_source")
                phone_log.info("[%s] %s | missing | method=olx_endpoint (offer not live)",
                               getattr(listing, "id", None) or listing.url,
                               listing.url)
                return None
            phone = self._phone_via_playwright(url)
            if phone:
                listing.phone = phone
                self._record_phone_outcome(listing, "found:olx_playwright")
                phone_log.info("[%s] %s | found %s | method=olx_playwright attempt=%d",
                               getattr(listing, "id", None) or listing.url,
                               listing.url, listing.phone, attempt)
                return phone
            if attempt < self.phone_max_attempts:
                time.sleep(self.phone_retry_delay)
        self._record_phone_outcome(listing, "endpoint_failed")
        phone_log.info("[%s] %s | missing | method=none (endpoint+playwright x%d)",
                       getattr(listing, "id", None) or listing.url,
                       listing.url, self.phone_max_attempts)
        return None

    def _resolve_phone(self, listing: Listing, html: str) -> None:
        """Fill listing.phone from the page source first; when the source
        has no number, cycle the /phones endpoint + headless browser."""
        try:
            phone, _stage = self._extract_detail_phone(html)
        except Exception:
            phone = ""
        if not phone:
            phone = self._clean_phone(html)
        if not phone:
            phone = self._phone_from_embedded_state(html)
        if phone:
            listing.phone = phone
            self._record_phone_outcome(listing, "found:page_source")
            phone_log.info("[%s] %s | found %s | method=page_source",
                           getattr(listing, "id", None) or listing.url,
                           listing.url, listing.phone)
            return
        if listing.url:
            ad_id = self._extract_ad_id(html, listing.url)
            self._fetch_phone_playwright(listing, listing.url, ad_id)

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

        # Map coords from the escaped JSON state, Almaty-only (cityName +
        # bounding box) — see _extract_map_coords.
        lat, lon = self._extract_map_coords(html)
        if lat is not None and lon is not None:
            listing.lat = lat
            listing.lon = lon

        # Phone: OLX hides the number behind a reveal button and keeps no
        # number in the page source. With the browser reveal enabled the
        # number is cycled through page source, the offers /phones API and
        # a headless browser; otherwise the base _enrich_phone runs.
        if self.phone_playwright_enabled and not listing.phone:
            self._resolve_phone(listing, html)
        else:
            self._enrich_phone(listing, html, session=self._cffi_session)

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
