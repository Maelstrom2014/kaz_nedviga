from __future__ import annotations

import logging
import random
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import ClassVar, Optional

import requests
import urllib3
from bs4 import BeautifulSoup

from .http import (HttpMixin, _random_headers, USER_AGENTS, ACCEPT_VALUES,
                   ACCEPT_LANGUAGES, HEADERS)

UA = USER_AGENTS[0]
from .phones import PhonesMixin, _canonical_phone_digits, normalize_phone
from .photos import PhotosMixin
from .models import Listing, SearchParams

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- Logger: file (errors) + console (full DEBUG stream) ---
from logsetup import console_handler, errors_file_handler, phone_file_handler

# mode="a" via rotation: shared with the "app" logger; "w" truncated the log
# on every import/restart.
_file_handler = errors_file_handler()
_console_handler = console_handler()

log = logging.getLogger("parsers")
log.setLevel(logging.DEBUG)
if not log.handlers:
    log.addHandler(_file_handler)
    log.addHandler(_console_handler)
# Console output must not depend on werkzeug's root handler (which is not
# always present, e.g. under `flask run`); also prevents double printing
# when the dev server does add one.
log.propagate = False

# --- Phone extraction log: separate file, one line per listing/endpoint ---
_phone_handler = phone_file_handler()
phone_log = logging.getLogger("parsers.phone")
phone_log.setLevel(logging.DEBUG)
if not phone_log.handlers:
    phone_log.addHandler(_phone_handler)
# Keep phone lines out of parsers_errors.log and the console.
phone_log.propagate = False


# ============================================================
# Parser run statistics — captured during run() for the analyzer tab
# ============================================================

@dataclass
class ParserRunStats:
    """Snapshot of a single parser run for diagnostics."""
    name: str = ""
    base_url: str = ""
    status: str = "pending"  # ok | partial | empty | http_error | ssl_error | connection_error | timeout | error | pending
    results_count: int = 0
    pages_fetched: int = 0
    duration_ms: float = 0.0
    error: str = ""
    error_type: str = ""
    http_status: str = ""
    timestamp: str = ""
    # Phone extraction: how many results ended up with a phone number and
    # why the rest didn't (reason -> count).
    phones_found: int = 0
    phones_missing: int = 0
    phone_reasons: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "base_url": self.base_url,
            "status": self.status,
            "results_count": self.results_count,
            "pages_fetched": self.pages_fetched,
            "duration_ms": round(self.duration_ms, 0),
            "error": self.error,
            "error_type": self.error_type,
            "http_status": self.http_status,
            "timestamp": self.timestamp,
            "phones_found": self.phones_found,
            "phones_missing": self.phones_missing,
            "phone_reasons": dict(self.phone_reasons),
        }


# Module-level registry: latest stats per parser name
_LAST_PARSER_STATS: dict[str, ParserRunStats] = {}


def get_all_parser_stats() -> list[dict]:
    """Return stats for every parser that has run at least once."""
    return [s.to_dict() for s in _LAST_PARSER_STATS.values()]


def reset_parser_stats():
    _LAST_PARSER_STATS.clear()

def _digits(value) -> str:
    # Accept str/int/float (embedded JSON states ship numbers as ints).
    return re.sub(r"[^\d]", "", str(value or ""))


def parse_int(value: str | None) -> int | None:
    if not value:
        return None
    d = _digits(value)
    return int(d) if d else None


def parse_float(value: str | None) -> float | None:
    if not value:
        return None
    m = re.search(r"[0-9]+(?:[.,][0-9]+)?", value.replace("\xa0", " "))
    if not m:
        return None
    return float(m.group(0).replace(",", "."))


def parse_rooms(value: str | None) -> int | None:
    if not value:
        return None
    m = re.search(r"(\d+)\s*комн", value, re.I)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)\s*[-]?\s*к", value, re.I)
    if m:
        return int(m.group(1))
    if re.search(r"студ", value, re.I):
        return 0
    return parse_int(value)


def parse_floor_pair(value: str | None) -> tuple[int | None, int | None]:
    if not value:
        return None, None
    m = re.search(r"(\d+)\s*[/\\]\s*(\d+)", value)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        # House numbers like "196/17" (building/wing) must not be read as
        # floors — real floors are small numbers.
        if 1 <= a <= 60 and 1 <= b <= 60:
            return a, b
        return None, None
    m = re.search(r"(\d+)\s*эт", value, re.I)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 60:
            return n, None
    return None, None


# --- Phone normalization (shared by telegram & twogis) --------------------
# Kazakhstan numbers appear in many shapes; collapse to a single canonical
# form so a phone cluster can be built (one landlord → several listings).
# --- Property fingerprint (cross-source dedup) ---------------------------
def property_fingerprint(
    *,
    building_id: str | None = None,
    normalized_location: str | None = None,
    rooms: int | None = None,
    area: float | None = None,
    floor: int | None = None,
    price: int | None = None,
) -> str:
    """Build a deterministic ``duplicate_group_id`` for a listing.

    Buckets area within ±2 m² (floor) and price within ±5% so trivial
    variations (``54.8`` vs ``55`` m², 250 000 vs 258 000 ₸) collapse to the
    same fingerprint while genuinely different flats stay apart. Returns
    ``""`` if there is not enough signal (no location AND no price).
    """
    loc = (building_id or normalized_location or "").strip().lower()
    if not loc and price is None:
        return ""
    area_bucket = int(area) if area is not None else None
    # Floor price to a ~8% band (20 000 ₸ step for typical rents) so two
    # listings whose rent differs by a few thousand due to wording round
    # the same apartment collapse to one fingerprint. Genuine price gaps
    # (e.g. 250 000 vs 300 000) land in different bands.
    step = 20000
    price_bucket = ((price // step) * step) if price else None
    parts = [
        loc or "_",
        str(rooms) if rooms is not None else "_",
        str(area_bucket) if area_bucket is not None else "_",
        str(floor) if floor is not None else "_",
        str(price_bucket) if price_bucket is not None else "_",
    ]
    return "|".join(parts)


# --- Russian date parsing ---
_MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "май": 5, "мая": 5,
    "июн": 6, "июл": 7, "август": 8, "авг": 8, "сентябр": 9, "сен": 9, "сент": 9,
    "октябр": 10, "окт": 10, "ноябр": 11, "ноя": 11, "нояб": 11, "декабр": 12, "дек": 12,
}


def parse_russian_date(text: str) -> Optional[str]:
    """Parse a Russian date string into ISO ``YYYY-MM-DD``.

    Handles relative (``Сегодня``/``Вчера``/``Позавчера``) and absolute forms
    (``5 авг``, ``5 августа 2024``, ``05.08.2024``, ``2024-08-05``). Returns
    ``None`` when no date is recognisable.
    """
    if not text:
        return None
    t = text.strip().lower()
    today = date.today()

    # Relative words — check longest first so "позавчера" wins over "вчера"
    rel = [("позавчера", -2), ("сегодня", 0), ("вчера", -1)]
    for word, delta in rel:
        if re.search(r"\b" + word + r"\b", t):
            return (today + timedelta(days=delta)).isoformat()

    # "5 августа 2024" / "5 авг"
    m = re.search(r"(\d{1,2})\s+([а-яё]+)\s*(\d{4})?", t)
    if m:
        day = int(m.group(1))
        mon = _month_of(m.group(2))
        year = int(m.group(3)) if m.group(3) else today.year
        if mon and 1 <= day <= 31:
            return _safe_iso(year, mon, day)

    # "август 5, 2024" — month-name first (requires a month stem)
    m = re.search(r"([а-яё]{3,})\s+(\d{1,2})(?!\d)\,?\s*(\d{4})?", t)
    if m:
        mon = _month_of(m.group(1))
        day = int(m.group(2))
        year = int(m.group(3)) if m.group(3) else today.year
        if mon and 1 <= day <= 31:
            return _safe_iso(year, mon, day)

    # ISO YYYY-MM-DD — check BEFORE dot-dates so "2024-08-05" isn't
    # misread as DD.MM.YY by the pattern below ("24-08-05").
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        return _safe_iso(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    # DD.MM.YYYY / DD-MM-YYYY — require a year so floor pairs like "3/5"
    # are not mistaken for dates.
    m = re.search(r"(?<!\d)(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{2,4})(?!\d)", t)
    if m:
        day = int(m.group(1))
        mon = int(m.group(2))
        year = int(m.group(3))
        if len(str(year)) == 2:
            year += 2000
        if 1 <= day <= 31 and 1 <= mon <= 12:
            return _safe_iso(year, mon, day)

    return None


def _month_of(token: str) -> Optional[int]:
    for stem, num in _MONTHS.items():
        if token.startswith(stem):
            return num
    return None


def _safe_iso(year: int, month: int, day: int) -> Optional[str]:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


_DATE_RE = re.compile(
    r"\b(?:сегодня|позавчера|вчера)\b"
    r"|\d{1,2}\s+[а-яё]{3,}(?:\s+\d{4})?"
    r"|(?<!\d)\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4}(?!\d)"
    r"|\d{4}-\d{1,2}-\d{1,2}",
    re.IGNORECASE,
)
_UPDATED_RE = re.compile(r"обнов", re.IGNORECASE)
_PUBLISHED_RE = re.compile(r"публик|размест|создан|добав", re.IGNORECASE)


def extract_dates(text: str) -> tuple[str, str]:
    """Extract publication/update dates from card text.

    Returns ``(date_published, date_updated)`` as ISO ``YYYY-MM-DD`` strings
    (``""`` when not found). If an "обновлено" keyword labels a date, it is
    assigned to ``date_updated``; "размещено"/"опубликовано" label
    ``date_published``. Otherwise the first date found becomes the publication
    date and any second date the update date.
    """
    if not text:
        return "", ""
    published = ""
    updated = ""
    for m in _DATE_RE.finditer(text):
        start, end = m.span()
        chunk = text[max(0, start - 14): end + 4]
        iso = parse_russian_date(m.group(0))
        if not iso:
            continue
        if _UPDATED_RE.search(chunk):
            if not updated:
                updated = iso
        elif _PUBLISHED_RE.search(chunk):
            if not published:
                published = iso
        else:
            if not published:
                published = iso
            elif not updated:
                updated = iso
    return published, updated


class BaseParser(HttpMixin, PhotosMixin, PhonesMixin):
    name: ClassVar[str] = "base"
    base_url: ClassVar[str] = ""
    search_path: ClassVar[str] = ""

    def __init__(self, timeout: int = 20, headers: dict | None = None):
        super().__init__()
        self.timeout = timeout
        self.headers = {**_random_headers(), **(headers or {})}
        self.last_stats: ParserRunStats = ParserRunStats(
            name=self.name, base_url=self.base_url, status="pending"
        )

    # ---- URL building -------------------------------------------------
    # build_url() is defined below with pagination support;
    # subclasses override _build_url_base() instead.

    def parse_html(self, html: str) -> BeautifulSoup:
        return BeautifulSoup(html, "lxml")

    @staticmethod
    def extract_dates(text: str) -> tuple[str, str]:
        """Return ``(date_published, date_updated)`` parsed from card text."""
        return extract_dates(text)

    # ---- Parsing ------------------------------------------------------
    # Substrings (lowercase) that must appear in the page <title> or H1 for
    # the page to be parsed. Protects against sites that redirect a category
    # URL to a generic search page (WAF, broken path): without the check the
    # parser would happily ingest unrelated classifieds (people's belongings,
    # pets, "отдам даром" items) instead of real estate.
    category_title_markers: ClassVar[tuple[str, ...]] = ()

    def parse(self, html: str, params: SearchParams) -> list[Listing]:
        soup = self.parse_html(html)
        if self.category_title_markers and not self._is_category_page(soup):
            return []
        return self._parse_soup(soup, params)

    def _is_category_page(self, soup: BeautifulSoup) -> bool:
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        if not title:
            h1 = soup.find("h1")
            title = h1.get_text(" ", strip=True) if h1 else ""
        if not title:
            # Fragment (tests/fixtures): nothing to validate against.
            return True
        if not any(m in title.lower() for m in self.category_title_markers):
            log.warning("[%s] page is not the estate category (title: %r), skipping",
                        self.name, title[:100])
            return False
        return True

    def _parse_soup(self, soup: BeautifulSoup, params: SearchParams) -> list[Listing]:
        raise NotImplementedError

    def is_unavailable(self, html: str) -> bool:
        """Detail page of a removed listing served with HTTP 200.

        Default: False — removed ads of most sources fail with an HTTP
        error instead. Only parsers that know the site's "ad is gone"
        page (OLX) override this.
        """
        return False

    def detect_status(self, html: str) -> Optional[str]:
        """Detect a listing status shown on the detail page (e.g. archived /
        possibly not relevant). Returns a human-readable label or None when
        the listing looks active. Parsers override this for site-specific
        status phrases."""
        return None

    def extract_detail_price(self, html: str, url: str) -> tuple:
        """Extract (price, lat, lon) from a detail page.

        Default fallback: run the search parser and look for a listing
        whose URL matches. Parsers whose detail page structure differs
        from search cards override this with site-specific selectors.
        Returns (None, None, None) when the price can't be extracted.
        """
        try:
            results = self.parse(html, SearchParams())
        except Exception:
            return (None, None, None)
        fav_base = url.split("?")[0].rstrip("/")
        for r in results:
            if not r.url:
                continue
            r_base = r.url.split("?")[0].rstrip("/")
            if r_base == fav_base or fav_base in r_base or r_base in fav_base:
                return (r.price, r.lat, r.lon)
        return (None, None, None)

    # ---- Filtering ----------------------------------------------------
    def filter_listing(self, listing: Listing, params: SearchParams) -> bool:
        if not params.matches_price(listing.price):
            return False
        if not params.matches_rooms(listing.rooms):
            return False
        if not params.matches_area(listing.area):
            return False
        if not params.matches_floor(listing.floor):
            return False
        if listing.address and not params.matches_district(listing.address):
            return False
        if params.query and params.query.lower() not in listing.title.lower() \
                and params.query.lower() not in listing.description.lower():
            return False
        return True

    def apply(self, results: list[Listing], params: SearchParams) -> list[Listing]:
        filtered = [r for r in results if self.filter_listing(r, params)]
        return filtered[: params.limit] if params.limit > 0 else filtered

    @staticmethod
    def _dedupe(results: list[Listing]) -> list[Listing]:
        seen_urls: set[str] = set()
        deduped: list[Listing] = []
        for r in results:
            if r.url and r.url not in seen_urls:
                seen_urls.add(r.url)
                deduped.append(r)
            elif not r.url:
                deduped.append(r)
        return deduped

    def run(self, params: SearchParams) -> list[Listing]:
        stats = ParserRunStats(
            name=self.name, base_url=self.base_url,
            status="pending", timestamp=datetime.now().isoformat(sep=" ", timespec="seconds"),
        )
        self.last_stats = stats
        self._phone_outcomes = {}
        t0 = time.monotonic()
        pages = 0
        log.info("[%s] === run start (max_pages=%d) ===", self.name,
                 params.max_pages or getattr(self, "max_pages", 3))
        try:
            all_results: list[Listing] = []
            # pages per site: user override (params.max_pages) wins,
            # otherwise the parser's own default
            max_pages = params.max_pages or getattr(self, "max_pages", 3)
            for page in range(1, max_pages + 1):
                if page > 1 and self.page_delay:
                    time.sleep(random.uniform(*self.page_delay))
                url = self.build_url(params, page=page)
                log.debug("[%s] fetching page %d: %s", self.name, page, url)
                t_fetch = time.monotonic()
                html = self.fetch(url)
                fetch_ms = (time.monotonic() - t_fetch) * 1000
                log.debug("[%s] page %d fetched in %.0fms (%d bytes)",
                          self.name, page, fetch_ms, len(html or ""))
                pages += 1
                results = self.parse(html, params)
                if not results:
                    log.debug("[%s] page %d: parse returned 0 results, stopping", self.name, page)
                    break
                all_results.extend(results)
                log.info("[%s] page %d: %d listings", self.name, page, len(results))
                if len(results) < self.min_page_size:
                    log.debug("[%s] page %d below min_page_size (%d<%d), last page",
                              self.name, page, len(results), self.min_page_size)
                    break  # last page
            # Deduplicate by URL
            deduped = self._dedupe(all_results)
            log.info("[%s] total %d listings (after dedup of %d)", self.name, len(deduped), len(all_results))
            filtered = self.apply(deduped, params)
            log.debug("[%s] after filters: %d listings", self.name, len(filtered))
            # Enrich top listings with photos from detail pages
            if filtered:
                log.debug("[%s] enriching photos for %d listings", self.name, len(filtered))
                self._enrich_photos(filtered)
            stats.pages_fetched = pages
            stats.results_count = len(filtered)
            stats.status = "ok" if filtered else "empty"
            stats.duration_ms = (time.monotonic() - t0) * 1000
            self._collect_phone_stats(filtered, stats)
            _LAST_PARSER_STATS[self.name] = stats
            log.info("[%s] === run done: %d results, %d pages, %.0fms, "
                     "phones %d/%d ===",
                     self.name, len(filtered), pages, stats.duration_ms,
                     stats.phones_found, len(filtered))
            return filtered
        except requests.exceptions.HTTPError as exc:
            status_code = getattr(exc.response, "status_code", "?")
            stats.http_status = str(status_code)
            stats.error = str(exc)[:300] or f"HTTP {status_code}"
            stats.error_type = type(exc).__name__
            stats.pages_fetched = pages
            stats.duration_ms = (time.monotonic() - t0) * 1000
            if all_results:
                # Blocked mid-pagination: keep the pages we already got
                # instead of throwing them away.
                stats.status = "partial"
                stats.results_count = len(all_results)
                log.warning(
                    "[%s] HTTP %s on page %d — keeping %d listings from "
                    "previous pages", self.name, status_code, pages + 1,
                    len(all_results))
                filtered = self.apply(self._dedupe(all_results), params)
                if filtered:
                    self._enrich_photos(filtered)
                stats.results_count = len(filtered)
                self._collect_phone_stats(filtered, stats)
                _LAST_PARSER_STATS[self.name] = stats
                return filtered
            stats.status = "http_error"
            log.warning("[%s] HTTP %s: %s", self.name, status_code, exc)
            _LAST_PARSER_STATS[self.name] = stats
            return []
        except requests.exceptions.SSLError as exc:
            stats.status = "ssl_error"
            stats.error = str(exc)[:300]
            stats.error_type = type(exc).__name__
            stats.pages_fetched = pages
            stats.duration_ms = (time.monotonic() - t0) * 1000
            log.warning("[%s] SSL error: %s", self.name, str(exc)[:200])
            _LAST_PARSER_STATS[self.name] = stats
            return []
        except requests.exceptions.ConnectionError as exc:
            stats.status = "connection_error"
            stats.error = str(exc)[:300]
            stats.error_type = type(exc).__name__
            stats.pages_fetched = pages
            stats.duration_ms = (time.monotonic() - t0) * 1000
            log.warning("[%s] Connection error: %s", self.name, str(exc)[:200])
            _LAST_PARSER_STATS[self.name] = stats
            return []
        except requests.exceptions.Timeout:
            stats.status = "timeout"
            stats.error = f"Timeout after {self.timeout}s"
            stats.error_type = "Timeout"
            stats.pages_fetched = pages
            stats.duration_ms = (time.monotonic() - t0) * 1000
            log.warning("[%s] Timeout after %ds", self.name, self.timeout)
            _LAST_PARSER_STATS[self.name] = stats
            return []
        except Exception as exc:
            stats.status = "error"
            stats.error = str(exc)[:300]
            stats.error_type = type(exc).__name__
            stats.pages_fetched = pages
            stats.duration_ms = (time.monotonic() - t0) * 1000
            log.warning("[%s] unexpected error: %s", self.name, exc, exc_info=True)
            _LAST_PARSER_STATS[self.name] = stats
            return []

    def build_url(self, params: SearchParams, page: int = 1) -> str:
        """Build URL for a given page. Override in subclasses for pagination."""
        url = self._build_url_base(params)
        if page > 1:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}page={page}"
        return url

    def _build_url_base(self, params: SearchParams) -> str:
        """Subclasses should override this instead of build_url."""
        raise NotImplementedError
