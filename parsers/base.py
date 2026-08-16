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

from .models import Listing, SearchParams

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- Logger: file (errors) + console (full DEBUG stream) ---
_LOG_PATH = Path(__file__).parent.parent / "parsers_errors.log"
_log_fmt = logging.Formatter(
    "%(asctime)s [%(name)s] %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
)
# mode="a": shared with the "app" logger; "w" truncated the log on every
# import/restart.
_file_handler = logging.FileHandler(str(_LOG_PATH), mode="a", encoding="utf-8")
_file_handler.setLevel(logging.DEBUG)
_file_handler.setFormatter(_log_fmt)
_console_handler = logging.StreamHandler()
_console_handler.setLevel(logging.DEBUG)
_console_handler.setFormatter(_log_fmt)

log = logging.getLogger("parsers")
log.setLevel(logging.DEBUG)
if not log.handlers:
    log.addHandler(_file_handler)
    log.addHandler(_console_handler)
# Console output must not depend on werkzeug's root handler (which is not
# always present, e.g. under `flask run`); also prevents double printing
# when the dev server does add one.
log.propagate = False


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
        }


# Module-level registry: latest stats per parser name
_LAST_PARSER_STATS: dict[str, ParserRunStats] = {}


def get_all_parser_stats() -> list[dict]:
    """Return stats for every parser that has run at least once."""
    return [s.to_dict() for s in _LAST_PARSER_STATS.values()]


def reset_parser_stats():
    _LAST_PARSER_STATS.clear()

# --- Pool of User-Agent strings (browsers + devices) ---
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
]

ACCEPT_LANGUAGES = [
    "ru-RU,ru;q=0.9,en;q=0.8",
    "ru,en-US;q=0.9,en;q=0.8",
    "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "kk-RU,ru;q=0.9,en;q=0.8",
    "ru,be;q=0.9,en;q=0.8",
]

ACCEPT_VALUES = [
    "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
]

_SEC_FETCH_COMBOS = [
    {"Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "none", "Sec-Fetch-User": "?1"},
    {"Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "cross-site", "Sec-Fetch-User": "?1"},
    {"Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "same-origin"},
    {"Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "same-site", "Sec-Fetch-User": "?1"},
]

_DNT_VALUES = ["1", "0", None]
_CACHE_CONTROL = ["max-age=0", "no-cache", None]
_PRAGMA = ["no-cache", None]
_REFERER_VALUES = [
    "https://www.google.com/",
    "https://yandex.ru/",
    "https://go.mail.ru/",
    None,
]


def _random_headers() -> dict:
    """Generate a randomized set of HTTP headers to avoid bot detection."""
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": random.choice(ACCEPT_VALUES),
        "Accept-Language": random.choice(ACCEPT_LANGUAGES),
        "Accept-Encoding": random.choice(["gzip, deflate, br", "gzip, deflate", "identity"]),
        "Connection": random.choice(["keep-alive", "close"]),
        "Upgrade-Insecure-Requests": "1",
    }
    headers.update(random.choice(_SEC_FETCH_COMBOS))
    cc = random.choice(_CACHE_CONTROL)
    if cc:
        headers["Cache-Control"] = cc
    pragma = random.choice(_PRAGMA)
    if pragma:
        headers["Pragma"] = pragma
    dnt = random.choice(_DNT_VALUES)
    if dnt is not None:
        headers["DNT"] = dnt
    referer = random.choice(_REFERER_VALUES)
    if referer:
        headers["Referer"] = referer
    return headers


# Default static headers (backward compat)
HEADERS = _random_headers()
UA = USER_AGENTS[0]


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


class BaseParser:
    name: ClassVar[str] = "base"
    base_url: ClassVar[str] = ""
    search_path: ClassVar[str] = ""

    def __init__(self, timeout: int = 20, headers: dict | None = None):
        self.timeout = timeout
        self.headers = {**_random_headers(), **(headers or {})}
        self._cffi_session = None
        self.last_stats: ParserRunStats = ParserRunStats(
            name=self.name, base_url=self.base_url, status="pending"
        )

    # ---- URL building -------------------------------------------------
    # build_url() is defined below with pagination support;
    # subclasses override _build_url_base() instead.

    # ---- HTTP ---------------------------------------------------------
    # Some sites (e.g. olx.kz) sit behind a WAF that fingerprints the TLS
    # handshake and 403s plain python-requests traffic while accepting
    # browser-like TLS. When enabled, fetch() uses curl_cffi impersonation.
    use_cffi: ClassVar[bool] = False
    cffi_impersonate: ClassVar[str] = "chrome"
    # A page smaller than this is treated as the last one (stops paging).
    # Sites with small per-page counts (etagi: 6) override this.
    min_page_size: ClassVar[int] = 10
    # Browser TLS profiles to rotate through when a WAF blocks us. A single
    # profile can get rate-limited/blocked while others still pass.
    cffi_profiles: ClassVar[tuple[str, ...]] = (
        "chrome", "safari184", "edge101", "firefox147",
    )
    # One persistent browser-TLS session across page fetches: cookies
    # persist and the TLS fingerprint never changes — like a real tab.
    session_sticky: ClassVar[bool] = False
    # (min, max) seconds of human-like pause between page fetches.
    page_delay: ClassVar[tuple[float, float] | None] = None
    # When set, a WAF block raises immediately instead of burning more
    # requests on profile rotation / fallbacks — retrying a block only
    # escalates it (the block lifts on its own).
    fail_fast_on_waf: ClassVar[bool] = False

    def _fetch_cffi(self, url: str, profile: str | None = None) -> str:
        from curl_cffi import requests as cffi_requests
        profile = profile or self.cffi_impersonate
        log.debug("[%s] cffi.GET %s (profile=%s)", self.name, url, profile)
        if self.session_sticky and profile == self.cffi_impersonate:
            if self._cffi_session is None:
                self._cffi_session = cffi_requests.Session(impersonate=profile)
            session = self._cffi_session
        else:
            session = cffi_requests.Session(impersonate=profile)
        resp = session.get(url, timeout=self.timeout)
        log.debug("[%s] cffi HTTP %s — %d bytes", self.name, resp.status_code, len(resp.text or ""))
        try:
            resp.raise_for_status()
        except cffi_requests.exceptions.HTTPError as cexc:
            # Normalize to the requests exception class so run()'s
            # HTTPError handling (partial results, WAF detection) applies.
            raise requests.exceptions.HTTPError(response=resp) from cexc
        return resp.text

    def _fetch_cffi_any(self, url: str) -> str | None:
        """Try each browser TLS profile in turn; return the first 200 body."""
        try:
            import curl_cffi  # noqa: F401
        except ImportError:
            return None
        for profile in self.cffi_profiles:
            try:
                return self._fetch_cffi(url, profile)
            except Exception as exc:
                log.debug("[%s] cffi profile %s failed: %s", self.name, profile, exc)
        return None

    def _is_waf_block(self, exc: Exception) -> bool:
        if isinstance(exc, requests.exceptions.HTTPError):
            return getattr(exc.response, "status_code", None) in (403, 429, 503)
        return isinstance(exc, (requests.exceptions.ConnectionError,
                                requests.exceptions.SSLError))

    def fetch(self, url: str) -> str:
        time.sleep(random.uniform(0.2, 0.8))
        if self.use_cffi:
            if self.fail_fast_on_waf:
                # One consistent session, no rotation: a WAF block lifts on
                # its own, so surface the failure instead of hammering.
                return self._fetch_cffi(url)
            # Preferred path: rotate browser profiles — a WAF may allow one
            # fingerprint and block another after rate limiting.
            html = self._fetch_cffi_any(url)
            if html is not None:
                return html
            log.debug("[%s] all cffi profiles failed, falling back to requests",
                      self.name)
        try:
            return self._fetch_requests(url)
        except Exception as exc:
            # WAF blocked plain requests — give browser-like TLS a chance.
            if self._is_waf_block(exc):
                html = self._fetch_cffi_any(url)
                if html is not None:
                    log.info("[%s] requests blocked (%s), browser TLS passed",
                             self.name, type(exc).__name__)
                    return html
            raise

    def _fetch_requests(self, url: str) -> str:
        session = requests.Session()
        # Fresh randomized headers for each request
        session.headers.update({**_random_headers(), **{k: v for k, v in self.headers.items() if k not in _random_headers()}})
        for attempt, verify in [(0, True), (1, False)]:
            try:
                log.debug("[%s] requests.GET %s (verify=%s)", self.name, url, verify)
                resp = session.get(url, timeout=self.timeout, allow_redirects=True, verify=verify)
                log.debug("[%s] HTTP %s — %d bytes (Content-Type: %s)",
                          self.name, resp.status_code, len(resp.content),
                          resp.headers.get("Content-Type", "?")[:60])
                resp.raise_for_status()
                # Fix encoding: prefer content-type header, then apparent, then utf-8
                ct = resp.headers.get("Content-Type", "")
                if "charset=" in ct:
                    resp.encoding = ct.split("charset=")[-1].strip()
                elif resp.apparent_encoding:
                    resp.encoding = resp.apparent_encoding
                else:
                    resp.encoding = "utf-8"
                text = resp.text
                # Fallback: if text looks like mojibake, try utf-8 directly
                if "\\u0" in repr(text)[:200] or "Ä" in text[:100]:
                    try:
                        text = resp.content.decode("utf-8")
                    except Exception:
                        pass
                return text
            except requests.exceptions.SSLError as exc:
                if attempt == 0:
                    log.debug("[%s] SSL error, retrying verify=False", self.name)
                    continue
                raise
            except requests.exceptions.HTTPError as exc:
                status = getattr(exc.response, "status_code", None)
                if status == 403 and attempt == 0:
                    # Rejected with the current header fingerprint — retry with
                    # fresh randomized headers, otherwise the same request is
                    # just blocked again.
                    log.debug("[%s] 403, retrying with fresh headers", self.name)
                    session.headers.update(_random_headers())
                    time.sleep(random.uniform(0.5, 1.5))
                    continue
                raise

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

    # ---- Photo enrichment (fetch detail pages for more photos) -----------
    # Number of top listings to enrich with detail-page photos.
    # Subclasses can override; 0 disables enrichment.
    enrich_photo_count: ClassVar[int] = 5
    # When True, _enrich_photos also downloads each photo to the disk cache
    # (cache/photos/) so export_pdf is instant (all cache hits).  Tests
    # that don't need real HTTP set this to False.
    precache_photos: ClassVar[bool] = True

    def _fetch_detail_photos(self, listing: Listing) -> list[str]:
        """Fetch the listing detail page and return all photo URLs.

        Override in subclasses that know how to find the photo gallery on
        the detail page. Returns an empty list if not implemented or on error.
        """
        return []

    def _enrich_photos(self, listings: list[Listing]) -> None:
        """Enrich *listings* (in place) with photos from detail pages.

        After extracting the photo **URLs** from the detail page, also
        **download each image** and write it to the on-disk photo cache
        (``cache/photos/``).  This way the export step later finds every
        photo already cached and hits zero network — the cache grows
        during parsing, not during export.
        """
        n = min(getattr(self, "enrich_photo_count", 0), len(listings))
        if n <= 0:
            return
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {
                pool.submit(self._fetch_detail_photos, item): item
                for item in listings[:n]
            }
            for fut in as_completed(futures):
                item = futures[fut]
                try:
                    photos = fut.result()
                    if photos:
                        # Merge instead of overwrite: the card photo is still a
                        # valid (and often the best) first frame.
                        existing = [item.photo] if item.photo else []
                        merged = list(dict.fromkeys(existing + photos))
                        item.photo = "|".join(merged)
                        # Pre-download all photos to the disk cache so the
                        # export step is instant (all cache hits).  Skip in
                        # tests (precache_photos=False) or when disabled.
                        if getattr(self, "precache_photos", True):
                            self._precache_photos(merged)
                except Exception as exc:
                    log.debug("[%s] photo enrichment failed for %s: %s",
                              self.name, item.url[:60], exc)

    def _precache_photos(self, urls: list[str]) -> None:
        """Download all photo URLs to the export_utils disk cache.

        Called after _fetch_detail_photos extracts URLs — the images
        themselves are fetched and cached so export_pdf doesn't hit
        the network later.  Runs in a **background daemon thread** so
        parsing returns results to the user immediately while photos
        trickle into the cache asynchronously.
        """
        import threading

        def _worker():
            try:
                from export_utils import _cache_get, _download_photo, _photo_urls
                to_download = [
                    u for u in _photo_urls("|".join(urls))
                    if _cache_get(u) is None
                ]
                if not to_download:
                    return
                from concurrent.futures import ThreadPoolExecutor, as_completed
                with ThreadPoolExecutor(max_workers=6) as pool:
                    futures = {pool.submit(_download_photo, u): u
                               for u in to_download}
                    done = 0
                    for fut in as_completed(futures):
                        try:
                            if fut.result():
                                done += 1
                        except Exception as exc:
                            log.debug("[%s] precache photo failed: %s",
                                      self.name, exc)
                if done:
                    log.debug("[%s] precached %d/%d photos",
                              self.name, done, len(to_download))
            except Exception as exc:
                log.debug("[%s] precache worker error: %s", self.name, exc)

        # Daemon thread: killed when the process exits, never blocks
        # the search response or the parser's run() return.
        t = threading.Thread(target=_worker, daemon=True)
        t.start()

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
            _LAST_PARSER_STATS[self.name] = stats
            log.info("[%s] === run done: %d results, %d pages, %.0fms ===",
                     self.name, len(filtered), pages, stats.duration_ms)
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
