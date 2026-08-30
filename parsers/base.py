from __future__ import annotations

import base64
import itertools
import logging
import random
import re
import threading
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

# --- Phone extraction log: separate file, one line per listing/endpoint ---
_PHONE_LOG_PATH = Path(__file__).parent.parent / "phone_extraction.log"
_phone_fmt = logging.Formatter(
    "%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
)
_phone_handler = logging.FileHandler(
    str(_PHONE_LOG_PATH), mode="a", encoding="utf-8")
_phone_handler.setLevel(logging.DEBUG)
_phone_handler.setFormatter(_phone_fmt)
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


# --- Phone normalization (shared by telegram & twogis) --------------------
# Kazakhstan numbers appear in many shapes; collapse to a single canonical
# form so a phone cluster can be built (one landlord → several listings).
_PHONE_DIGITS_RE = re.compile(r"\d")
_WA_PHONE_RE = re.compile(
    r"(?:wa\.me|api\.whatsapp\.com/send\?phone=)(\d{10,15})", re.I)


def _canonical_phone_digits(s: str) -> str:
    """Collapse a digits-only string to ``+7XXXXXXXXXX``; ``""`` if not a phone.

    Accepts the 11-digit international forms (+7/8/007 — KZ and RU) and the
    bare 10-digit national form (KZ ``7XXXXXXXXX``, RU mobile/landline).
    """
    if s.startswith("00"):  # international dialing prefix: 007...
        s = s[2:]
    if len(s) == 11 and s[0] in "78":
        s = s[1:]
    if len(s) != 10:
        return ""
    # National 10 digits: 7 = KZ (mobile 70X/74X/75X/77X, landline 71XX-79XX);
    # 9 / 3-6 / 8 = RU mobile/landline.  0/1/2 are not phone prefixes.
    if s[0] not in "3456789":
        return ""
    return "+7" + s


def normalize_phone(raw: str | None) -> str:
    """Normalize a KZ/RU phone to ``+7XXXXXXXXXX`` (10 digits after +7).

    Accepts ``8707...``, ``+7 707 ...``, ``8 (707) 123-45-67``, bare KZ
    ``707 123 45 67``, ``wa.me/77071234567`` etc. Returns ``""`` if it can't
    be normalized to a plausible KZ mobile/landline.
    """
    if not raw:
        return ""
    # Extract a wa.me/api.whatsapp phone if present (highest priority).
    wm = _WA_PHONE_RE.search(str(raw))
    if wm:
        return _canonical_phone_digits(wm.group(1))
    digits = "".join(_PHONE_DIGITS_RE.findall(str(raw)))
    if not digits:
        return ""
    return _canonical_phone_digits(digits)


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
        # Per-run phone outcomes: listing.url -> "found:<stage>" or a
        # missing-reason. Reset at the start of every run().
        self._phone_outcomes: dict[str, str] = {}
        # Serializes click-to-reveal phone-API calls so concurrent detail
        # fetches (ThreadPoolExecutor in _enrich_photos) cannot burst-fire
        # the endpoint and trigger a soft rate-limit (OLX returns 400).
        self._phone_endpoint_lock = threading.Lock()
        self._phone_last_call = 0.0
        # Block detection: OLX's /phones API has a limited per-IP budget
        # of clean calls, then returns 400 for the rest of the window.
        # Track consecutive 4xx responses and back off (the block
        # outlasts short retries, so hammering only extends it).
        # _phone_consec_fail resets on a 200; _phone_block_until pauses
        # all endpoint calls until it passes.
        self._phone_consec_fail = 0
        self._phone_block_until = 0.0
        # Proxy URL -> monotonic time of its last successful reveal call.
        # The reveal budget is per client IP, so a proxy that just returned
        # a number is skipped until its budget can plausibly have refilled.
        self._phone_proxy_ok: dict[str, float] = {}

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
    # Bounded WAF cooldown (fail-fast path only): on a 403/429/503, sleep and
    # retry up to waf_max_retries times. 0 = fail immediately (default).
    waf_max_retries: ClassVar[int] = 0
    waf_cooldown: ClassVar[float] = 30.0
    waf_max_cooldown: ClassVar[float] = 120.0
    # Site support/hotline numbers that leak into the page source (footer
    # tel: links). They must never be reported as the advertiser's phone.
    phone_blacklist: ClassVar[frozenset[str]] = frozenset()
    # Click-to-reveal phone endpoint throttling. The reveal API (OLX /phones,
    # kn.kz /card/phone, krisha.kz ajaxPhones) rate-limits rapid successive
    # calls — once _enrich_photos fans out to a 6-thread pool, concurrent
    # calls burst-fire it and the site soft-blocks (OLX: 400). The instance
    # lock (_phone_endpoint_lock) serializes calls across threads; the min
    # interval caps throughput.
    phone_endpoint_cooldown: ClassVar[float] = 2.0
    phone_endpoint_min_interval: ClassVar[float] = 0.0
    # Jitter (fraction) applied to the min interval so the call cadence
    # is not perfectly regular (a metronomic cadence is a bot signal).
    phone_endpoint_jitter: ClassVar[float] = 0.3
    # Per-proxy post-success cooldown. The reveal API budget is per client
    # IP (~1 success per 2-3 min on OLX), so a proxy that just yielded a
    # number is skipped for this long and calls rotate to a fresh exit IP.
    phone_proxy_cooldown: ClassVar[float] = 150.0
    # Block detection: after this many CONSECUTIVE 4xx/5xx responses the
    # endpoint is treated as soft-blocked for the IP and calls pause for
    # phone_block_cooldown seconds (the block outlasts short retries).
    phone_block_threshold: ClassVar[int] = 4
    phone_block_cooldown: ClassVar[float] = 120.0
    # Page-only phone mode. When False, _enrich_phone skips the click-to-reveal
    # XHR endpoints (OLX /phones, kn.kz /card/phone, krisha.kz ajaxPhones) and
    # relies solely on the page source (tel:/wa.me links, JSON state, visible
    # text). OLX uses this to avoid the reveal-API rate limits entirely: the
    # masked number is never present in the page HTML.
    phone_endpoint_enabled: ClassVar[bool] = True

    def _proxy_kwargs(self) -> dict:
        """Return ``{"http": url, "https": url}`` for the current rotation proxy,
        or ``{}`` when proxies are disabled, the site is not selected, or the
        pool is empty (direct)."""
        from .proxy import get_pool
        try:
            pk = get_pool().get_proxies(self.name)
            return pk
        except Exception as exc:
            log.debug("[%s] proxy lookup failed, using direct: %s", self.name, exc)
            return {}

    def _note_proxy_error(self, url: str, pk: dict, exc: Exception) -> None:
        """Log a proxy request failure to the shared pool's error log."""
        try:
            from .proxy import get_pool
            if pk and get_pool().enabled:
                get_pool().log_error(self.name, url, pk.get("https", ""),
                                     "%s: %s" % (type(exc).__name__, exc))
        except Exception:
            pass

    def _note_proxy_used(self, pk: dict) -> None:
        """Record that a request for this site went through the given proxy."""
        try:
            from .proxy import get_pool
            if pk and get_pool().enabled:
                get_pool().note_use(self.name, pk.get("https", ""))
        except Exception:
            pass

    def _proxy_tries(self) -> list:
        """Ordered proxy kwargs to try for this site: up to
        ``proxy_max_retries`` rotated proxies, then one direct (``{}``) attempt.
        Always ends with ``{}`` so a site still parses when every proxy is dead.
        Returns ``[{}]`` when proxies are disabled or not selected for the site."""
        from .proxy import get_pool
        try:
            max_retries = int(get_pool().proxy_max_retries)
        except Exception:
            max_retries = 2
        first = self._proxy_kwargs()
        if not first:
            return [{}]
        tries = []
        cur = first
        for _ in range(max(0, max_retries)):
            tries.append(cur)
            cur = self._proxy_kwargs()
        tries.append({})  # direct fallback
        return tries

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
        resp = None
        last_exc = None
        for pk in self._proxy_tries():
            try:
                tmo = self.timeout if not pk else min(self.timeout, 5)
                resp = session.get(url, timeout=tmo, proxies=pk)
                self._note_proxy_used(pk)
                break
            except Exception as exc:
                last_exc = exc
                self._note_proxy_error(url, pk, exc)
        if resp is None:
            raise last_exc if last_exc else RuntimeError("cffi fetch failed")
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
                # One consistent session, no rotation: a WAF block is
                # IP-based and lifts on its own. Fail fast by default; when
                # waf_max_retries > 0, back off and retry a bounded number of
                # times so a short block clears within the run.
                attempt = 0
                while True:
                    attempt += 1
                    try:
                        return self._fetch_cffi(url)
                    except Exception as exc:
                        if (self._is_waf_block(exc) and self.waf_max_retries > 0
                                and attempt <= self.waf_max_retries):
                            delay = min(self.waf_cooldown * attempt,
                                        self.waf_max_cooldown)
                            delay += random.uniform(0.0, 1.0)
                            log.warning(
                                "[%s] WAF block (%s); cooling down %.0fs "
                                "(retry %d/%d)",
                                self.name, type(exc).__name__, delay,
                                attempt, self.waf_max_retries)
                            time.sleep(delay)
                            continue
                        raise
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

    def fetch_session(self, url: str) -> tuple[requests.Session, str]:
        """GET ``url`` and return ``(session, text)``.

        Some sites (kn.kz) gate the phone behind an XHR reveal endpoint
        that only answers for the session which loaded the detail page
        (cookie + Referer). The page and the endpoint must therefore
        share one browser session — use this instead of fetch().
        """
        return self._fetch_requests_session(url)

    def _fetch_requests(self, url: str) -> str:
        _, text = self._fetch_requests_session(url)
        return text

    def _fetch_requests_session(self, url: str) -> tuple[requests.Session, str]:
        """Like _fetch_requests, but returns (session, text) so follow-up
        calls can reuse the same browser session (cookies persist).

        Proxies are best-effort: up to ``proxy_max_retries`` rotated proxies
        are tried, then one direct request, so the site still parses when
        every proxy is dead. Connection-level failures and repeated 403/429
        rotate to the next proxy; a genuine server error raises immediately."""
        session = requests.Session()
        # Fresh randomized headers for each request
        session.headers.update({**_random_headers(), **{k: v for k, v in self.headers.items() if k not in _random_headers()}})
        last_exc = None
        for pk in self._proxy_tries():
            session.proxies = pk
            tmo = self.timeout if not pk else min(self.timeout, 5)
            for attempt, verify in [(0, True), (1, False)]:
                try:
                    log.debug("[%s] requests.GET %s (verify=%s, proxy=%s)",
                              self.name, url, verify, bool(pk))
                    resp = session.get(url, timeout=tmo, allow_redirects=True, verify=verify)
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
                    self._note_proxy_used(pk)
                    return session, text
                except requests.exceptions.SSLError as exc:
                    if attempt == 0:
                        log.debug("[%s] SSL error, retrying verify=False", self.name)
                        continue
                    last_exc = exc
                    break
                except requests.exceptions.HTTPError as exc:
                    status = getattr(exc.response, "status_code", None)
                    if status == 403 and attempt == 0:
                        log.debug("[%s] 403, retrying with fresh headers", self.name)
                        session.headers.update(_random_headers())
                        time.sleep(random.uniform(0.5, 1.5))
                        continue
                    if status in (403, 429) and pk:
                        # The proxy's IP was likely blocked; rotate to the next.
                        last_exc = exc
                        self._note_proxy_error(url, pk, exc)
                        break
                    raise
                except requests.exceptions.RequestException as exc:
                    last_exc = exc
                    self._note_proxy_error(url, pk, exc)
                    break
            if not pk:
                # Direct request failed; no more fallbacks.
                if last_exc is not None:
                    raise last_exc
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("requests fetch failed")
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

    # ---- Phone extraction ---------------------------------------------
    # KZ/RU numbers: 11 digits starting with 7 or 8 (network code 7XX KZ
    # mobile, 9XX/8XX RU mobile, 3XX-6XX/8XX RU landline) or the bare
    # 10-digit KZ national form ``7XX XXX XX XX`` without the leading 7/8.
    # Detail pages hide the number behind a "Показать телефон" button, but
    # the raw value still sits in the source: on the button itself, tel: /
    # WhatsApp links, data-* attributes, embedded JSON state, inline JS
    # (sometimes base64- or charCode-obfuscated), visible text — or it is
    # served from a click-to-reveal XHR endpoint (phonesUrl & co).
    _PHONE_CAND_RE = re.compile(
        r"(?<!\d)(?:"
        r"(?:\+|00)?[78][\s\-()]?\(?\d{3}\)?[\s\-()]?"
        r"\d{3}[\s\-()]?\d{2}[\s\-()]?\d{2}"
        r"|"
        r"7[\s\-()]?\(?\d{2}\)?[\s\-()]?"
        r"\d{3}[\s\-()]?\d{2}[\s\-()]?\d{2}"
        r")(?!\d)"
    )
    # JSON state keys: "phone"/"phoneNumber"/"tel"/"mobile"/... with a
    # quoted string, a bare number, or a single-element array value.
    _PHONE_KEY_RE = re.compile(
        r'["\'](?:phone\w*|tel\w*|mobile\w*|whatsapp\w*)["\']'
        r'\s*:\s*'
        r'(?:"([^"]{4,40})"|\'([^\']{4,40})\'|(\d{10,13})|\[\s*"([^"]{4,40})")',
        re.I,
    )
    _PHONE_KW_RE = re.compile(
        r"(?:tel|phone|телефон|контакт|связ|звон|whatsapp|вацап|viber)"
        r"[^0-9+\-]{0,20}"
        r"((?:\+|00)?[78][\s\-()]?\(?\d{3}\)?[\s\-()]?"
        r"\d{3}[\s\-()]?\d{2}[\s\-()]?\d{2}"
        r"|"
        r"7[\s\-()]?\(?\d{2}\)?[\s\-()]?"
        r"\d{3}[\s\-()]?\d{2}[\s\-()]?\d{2})(?!\d)", re.I,
    )
    _PHONE_ATTR_RE = re.compile(
        r"^data[-_]?phone(?:number)?$|^data[-_]?tel(?:ephone)?$", re.I,
    )
    # WhatsApp deep links: wa.me/7705..., api.whatsapp.com, whatsapp://
    _WA_HREF_RE = re.compile(
        r"(?:wa\.me/|api\.whatsapp\.com/send\?phone=|whatsapp://send\?phone=)"
        r"(\d{10,15})", re.I,
    )
    # The button that hides the number: "Показать телефон / показать номер
    # / показать свой номер / show phone number". Its own markup (data-*
    # attributes, onclick handler, plain text) is the most reliable source.
    _REVEAL_BTN_TEXT_RE = re.compile(
        r"(?:показать|посмотреть|show)\s+(?:свой\s+)?(?:телефон|номер|контакт)|"
        r"телефон\s+(?:скрыт|защищ)|скрыт(?:ый|ая)?\s+(?:телефон|номер)",
        re.I,
    )
    # Classic JS obfuscation of the number: base64 blobs and
    # String.fromCharCode(43,56,55,...) sequences.
    _B64_BLOB_RE = re.compile(r"[A-Za-z0-9+/]{12,56}={0,2}")
    _CHARCODE_RE = re.compile(r"fromCharCode\(([\d,\s]{10,400})\)")
    # Click-to-reveal endpoint URLs embedded in the page source (the button's
    # JS calls them): data-show-phone-url-value (kn.kz), "phonesUrl" state
    # keys (krisha.kz), /ajax/...phone paths.
    _PHONE_ENDPOINT_RES = (
        re.compile(r'data-(?:show-)?phone(?:s)?[-_]?url(?:-value)?="([^"]+)"',
                   re.I),
        re.compile(
            r'["\'](?:phones?_?url|showphoneurl|revealphoneurl|phoneendpoint)'
            r'["\']\s*:\s*["\']([^"\']+)["\']', re.I),
        re.compile(r'["\']([^"\']*?/ajax[^"\']{0,40}phone[^"\']{0,40})["\']',
                   re.I),
        re.compile(r'["\'](/[^"\']{0,80}/phones?[^"\']{0,40})["\']', re.I),
    )

    @staticmethod
    def _valid_phone(raw: str) -> str:
        """Normalize a candidate to +7XXXXXXXXXX; '' when invalid/masked."""
        if not raw or any(c in raw for c in "*?•."):
            return ""
        return _canonical_phone_digits(re.sub(r"\D", "", raw))

    def _first_valid(self, text: str) -> str:
        """First phone candidate in *text* that passes validation + blacklist."""
        for m in self._PHONE_CAND_RE.finditer(text or ""):
            cand = m.group(0)
            digits = re.sub(r"\D", "", cand)
            # A bare unseparated 10-digit run in free text is more often an
            # ID than a phone number — accept it only when formatted.
            if len(digits) == 10 and not re.search(r"[\s\-()]", cand):
                continue
            phone = self._valid_phone(cand)
            if phone and phone not in self.phone_blacklist:
                return phone
        return ""

    def _reveal_buttons(self, soup):
        """Yield elements whose text reads like a "Показать телефон" button."""
        seen: set[int] = set()
        for match in soup.find_all(string=self._REVEAL_BTN_TEXT_RE):
            node = match.parent
            while (node is not None
                   and node.name not in ("button", "a", "span", "div", "p")):
                node = node.parent
            if node is None or id(node) in seen:
                continue
            seen.add(id(node))
            yield node

    def _phone_from_scripts(self, soup) -> list[str]:
        """Phone numbers hidden inside <script> JavaScript.

        The value may sit in a plain assignment (``phone: "7 705 ..."``),
        be base64-encoded, or be built with String.fromCharCode(...) — all
        three shapes are decoded and the results validated.
        """
        found: list[str] = []
        for script in soup.find_all("script"):
            js = script.get_text() or ""
            if not js:
                continue
            for m in self._PHONE_KW_RE.finditer(js):
                phone = self._valid_phone(m.group(1))
                if phone and phone not in self.phone_blacklist:
                    found.append(phone)
            for m in itertools.islice(self._B64_BLOB_RE.finditer(js), 200):
                try:
                    blob = m.group(0)
                    dec = base64.b64decode(
                        blob + "=" * (-len(blob) % 4)).decode("utf-8", "ignore")
                except Exception:
                    continue
                if not re.search(r"\d{5,}", dec):
                    continue
                for cand in self._PHONE_CAND_RE.finditer(dec):
                    phone = self._valid_phone(cand.group(0))
                    if phone and phone not in self.phone_blacklist:
                        found.append(phone)
            for m in itertools.islice(self._CHARCODE_RE.finditer(js), 20):
                try:
                    dec = "".join(
                        chr(int(part)) for part in m.group(1).split(",")
                        if part.strip().isdigit())
                except Exception:
                    continue
                for cand in self._PHONE_CAND_RE.finditer(dec):
                    phone = self._valid_phone(cand.group(0))
                    if phone and phone not in self.phone_blacklist:
                        found.append(phone)
        return found

    def extract_detail_phone(self, html: str) -> str:
        """Extract the contact phone from a detail page.

        The number is usually hidden behind a "Показать телефон" button,
        yet the raw value is still in the page source. Sources, in priority
        order: the reveal button's own markup (data-* attributes, onclick,
        plain text), tel: links, WhatsApp links, data-phone/data-tel
        attributes, JSON "phone" state keys (incl. camelCase keys, bare
        numbers, arrays), inline JS (plain/base64/charCode), visible text,
        keyword-anchored raw HTML. Returns "" when nothing found.
        """
        phone, _ = self._extract_detail_phone(html)
        return phone

    def _extract_phone_multi(self, html: str) -> list:
        """Last-resort phone candidate generation. Tries several
        independent algorithms for a number hidden behind a reveal
        button / anti-spam overlay. Returns a list of raw candidate
        strings (NOT validated); the caller validates each with the
        standard phone acceptance checks."""
        if not html:
            return []
        cands = []

        def add(v):
            if v and v not in cands:
                cands.append(v)

        # A) tel: / sms: / whatsapp links (incl. hidden in attrs)
        for rx in (r'tel:([+\d][\d\s\-().]{6,18}\d)',
                   r'sms:([+\d][\d\s\-().]{6,18}\d)',
                   r'(?:wa\.me/|whatsapp://send\?phone=|api\.whatsapp\.com/send\?phone=)(\+?\d{8,15})'):
            for m in re.finditer(rx, html, re.I):
                add(m.group(1))

        # B) data-* contact attributes
        for m in re.finditer(
                r'data-(?:phone|tel|telephone|contact|number|mobile|phone_number)'
                r'\s*=\s*["\']([+\d][\d\s\-().]{6,18}\d)["\']', html, re.I):
            add(m.group(1))

        # C) JSON / JS state keys holding a phone (string or bare number)
        for m in re.finditer(
                r'["\'](?:phone|telephone|phone_number|phone_number_full|'
                r'contact_phone|mobile|tel|phone_digits|phone_value)["\']'
                r'\s*:\s*["\']?([+\d][\d\s\-().]{7,18}\d)["\']?', html, re.I):
            add(m.group(1))

        # D) base64-encoded blobs
        for m in re.finditer(r'["\']([A-Za-z0-9+/]{16,}={0,2})["\']', html):
            tok = m.group(1)
            try:
                dec = base64.b64decode(tok + "=" * (-len(tok) % 4),
                                        validate=False).decode("utf-8", "ignore")
            except Exception:
                continue
            for pm in re.finditer(r'[+\d][\d\s\-().]{8,18}\d', dec):
                add(pm.group(0))

        # E) fromCharCode(...) arrays
        for m in re.finditer(r'fromCharCode\(([\d,\s]{4,300})\)', html):
            try:
                codes = [int(x) for x in m.group(1).split(",") if x.strip().isdigit()]
                dec = "".join(chr(c) for c in codes if 32 <= c < 127)
            except Exception:
                continue
            for pm in re.finditer(r'[+\d][\d\s\-().]{8,18}\d', dec):
                add(pm.group(0))

        # F) HTML-entity-decoded, zero-width-stripped text, keyword-anchored
        try:
            import html as _htmllib
            cleaned = _htmllib.unescape(html)
            cleaned = re.sub('[\u200b\u200c\u200d\ufeff\u00a0]', ' ', cleaned)
            for m in re.finditer(
                    r'\b(?:phone|tel|contact|whatsapp|viber|'
                    r'телефон|звон|контакт|связь|мобил)'
                    r'[^<>{}]{0,50}?([+\d][\d\s\-().]{8,18}\d)', cleaned, re.I):
                add(m.group(1))
        except Exception:
            pass

        return cands

    def _extract_detail_phone(self, html: str) -> tuple[str, str]:
        """Like extract_detail_phone, but also reports the winning stage.

        Stage names: button, tel_link, wa_link, data_attr, json_state,
        js_script, visible_text, raw_keyword — or "" when not found.
        """
        if not html:
            return "", ""
        soup = BeautifulSoup(html, "html.parser")

        def ok(phone: str) -> bool:
            return bool(phone) and phone not in self.phone_blacklist

        # 1) "Показать телефон" button — the number often lives right in
        #    the button's markup (data-* attr, onclick, adjacent text).
        for btn in self._reveal_buttons(soup):
            phone = self._first_valid(str(btn)[:4000])
            if ok(phone):
                return phone, "button"
        # 2) tel: links
        for a in soup.find_all("a", href=re.compile(r"^tel:")):
            phone = self._valid_phone((a.get("href") or "")[4:])
            if ok(phone):
                return phone, "tel_link"
        # 3) WhatsApp links: wa.me/7705..., whatsapp://send?phone=...
        for a in soup.find_all("a", href=True):
            m = self._WA_HREF_RE.search(a.get("href") or "")
            if m:
                phone = self._valid_phone(m.group(1))
                if ok(phone):
                    return phone, "wa_link"
        # 4) data-phone / data-tel / data-phonenumber attributes
        #    (bs4 >= 4.13 ignores a compiled regex in attrs=, so match
        #    attribute names with a tag predicate instead)
        for el in soup.find_all(
                lambda t: any(self._PHONE_ATTR_RE.match(a) for a in t.attrs)):
            for attr, val in el.attrs.items():
                if isinstance(val, str) and self._PHONE_ATTR_RE.match(attr):
                    phone = self._valid_phone(val)
                    if ok(phone):
                        return phone, "data_attr"
        # 5) JSON state: "phone": "+7 ...", "phoneNumber": "8 ...",
        #    "phone": 77051234567, "phones": ["+7 ..."]
        for m in self._PHONE_KEY_RE.finditer(html):
            phone = self._valid_phone(
                m.group(1) or m.group(2) or m.group(3) or m.group(4) or "")
            if ok(phone):
                return phone, "json_state"
        # 6) inline JS behind the reveal button (plain/base64/charCode)
        for phone in self._phone_from_scripts(soup):
            if ok(phone):
                return phone, "js_script"
        # 7) visible text (a bare unseparated 10-digit run is an ID, not a
        #    phone — the guard lives in _first_valid)
        phone = self._first_valid(soup.get_text(" ", strip=True))
        if phone:
            return phone, "visible_text"
        # 8) raw HTML, but only next to a phone keyword — bare digit
        #    runs (IDs, hashes) are rejected.
        for m in self._PHONE_KW_RE.finditer(html):
            phone = self._valid_phone(m.group(1))
            if ok(phone):
                return phone, "raw_keyword"
        # 9) multi-algorithm fallback (reveal-button / anti-spam phones)
        for _cand in self._extract_phone_multi(html):
            _p = self._valid_phone(_cand)
            if _p and ok(_p):
                return _p, "multi_algo"
        return "", ""

    def _record_phone_outcome(self, listing: Listing, outcome: str) -> None:
        """Remember how a listing got (or didn't get) its phone this run."""
        self._phone_outcomes[listing.url] = outcome

    def _collect_phone_stats(self, results: list[Listing],
                             stats: ParserRunStats) -> None:
        """Fill phones_found / phones_missing / phone_reasons for a run.

        Missing reasons: no_phone_source (nothing in the page source and
        no reveal endpoint), endpoint_failed (endpoint(s) found but none
        returned a phone), extraction_error (page parsing blew up),
        detail_fetch_failed (detail page fetch failed, enrichment never
        ran).
        """
        stats.phones_found = sum(1 for l in results if l.phone)
        stats.phones_missing = len(results) - stats.phones_found
        for l in results:
            if not l.phone:
                reason = (self._phone_outcomes.get(l.url)
                          or "detail_fetch_failed")
                stats.phone_reasons[reason] = (
                    stats.phone_reasons.get(reason, 0) + 1)
        if results:
            detail = (", ".join("%s=%d" % kv
                                for kv in sorted(stats.phone_reasons.items()))
                      if stats.phone_reasons else "none")
            log.info("[%s] phones: %d found, %d missing (%s)",
                     self.name, stats.phones_found, stats.phones_missing,
                     detail)

    def _enrich_phone(
        self,
        listing: Listing,
        html: str,
        session: requests.Session | None = None,
    ) -> None:
        """Set listing.phone from the detail page when it is still empty.

        Called by parsers from _fetch_detail_photos() right after the
        detail page is fetched — the same request that collects photos.
        First tries the page source (reveal-button markup, tel:/wa.me
        links, JSON state, inline JS, visible text); if the number is
        served from a click-to-reveal XHR endpoint instead, the endpoint
        URL is discovered from the page and fetched with the page's own
        session (or a fresh one with randomized browser headers).
        Every attempt is recorded in self._phone_outcomes (for run stats)
        and in phone_extraction.log (via the "parsers.phone" logger).
        """
        if listing.phone:
            return
        try:
            phone, stage = self._extract_detail_phone(html)
        except Exception as exc:
            log.debug("[%s] phone extraction failed for %s: %s",
                      self.name, listing.url[:60], exc)
            self._record_phone_outcome(listing, "extraction_error")
            phone_log.info("[%s] %s | missing | reason=extraction_error (%s)",
                           self.name, listing.url[:120], type(exc).__name__)
            return
        if phone:
            listing.phone = phone
            self._record_phone_outcome(listing, "found:" + stage)
            phone_log.info("[%s] %s | found %s | stage=%s",
                           self.name, listing.url[:120], phone, stage)
            return
        # Page source exhausted — try click-to-reveal XHR endpoints.
        reason = "no_phone_source"
        if self.phone_endpoint_enabled:
            try:
                for endpoint in self._discover_phone_endpoints(html):
                    phone = self.fetch_phone_endpoint(
                        session or self._ephemeral_session(),
                        endpoint, listing.url)
                    if phone:
                        listing.phone = phone
                        self._record_phone_outcome(listing, "found:endpoint")
                        phone_log.info(
                            "[%s] %s | found %s | stage=endpoint | url=%s",
                            self.name, listing.url[:120], phone, endpoint)
                        return
                    reason = "endpoint_failed"
            except Exception as exc:
                log.debug("[%s] phone endpoint discovery failed for %s: %s",
                          self.name, listing.url[:60], exc)
        self._record_phone_outcome(listing, reason)
        phone_log.info("[%s] %s | missing | reason=%s",
                       self.name, listing.url[:120], reason)

    def _ephemeral_session(self) -> requests.Session:
        """Throwaway session with randomized browser headers (no cookies).

        Used for click-to-reveal XHR endpoints when the detail page was
        fetched without a session (self.fetch) — the endpoint needs the
        right headers + Referer, not the page's cookie jar.
        """
        sess = requests.Session()
        _pk = self._proxy_kwargs()
        if _pk:
            sess.proxies = _pk
        sess.headers.update(_random_headers())
        sess.headers["X-Requested-With"] = "XMLHttpRequest"
        return sess

    def _discover_phone_endpoints(self, html: str) -> list[str]:
        """Find click-to-reveal phone endpoint URLs embedded in the page.

        The reveal button's JS references the endpoint:
        data-show-phone-url-value (kn.kz), a "phonesUrl" state key
        (krisha.kz), or an /ajax/...phone path. Returns absolute URLs,
        deduplicated, capped at 3.
        """
        found: list[str] = []
        for rx in self._PHONE_ENDPOINT_RES:
            for m in rx.finditer(html or ""):
                url = m.group(1)
                if not url:
                    continue
                if url.startswith("//"):
                    url = "https:" + url
                elif url.startswith("/") and self.base_url:
                    url = self.base_url.rstrip("/") + url
                if not url.startswith(("http://", "https://")):
                    continue
                if url in found:
                    continue
                found.append(url)
                if len(found) >= 3:
                    return found
        return found

    def _phone_proxy_hot(self, key: str) -> bool:
        """True while the proxy's per-IP reveal budget is (probably) spent."""
        last = self._phone_proxy_ok.get(key)
        return (last is not None
                and (time.monotonic() - last) < self.phone_proxy_cooldown)

    def _note_phone_proxy_spent(self, pk: dict) -> None:
        """Mark an exit IP's reveal budget spent (success) or the exit burnt
        (403 / connect failure): skip it for phone_proxy_cooldown seconds."""
        key = pk.get("https") or pk.get("http") or ""
        if key:
            self._phone_proxy_ok[key] = time.monotonic()

    def _phone_proxy_tries(self) -> list[dict]:
        """Rotated proxy picks for phone endpoint calls.

        The reveal API rate-limits per client IP, so every call should use
        a fresh exit IP. Returns up to three random pool proxies that are
        not in their post-success cooldown; direct (``{}``) only when the
        pool is disabled or empty. When every pick is still cooling down,
        one hot pick is returned (a cheap 400 beats skipping the call)."""
        first = self._proxy_kwargs()
        if not first:
            return [{}]
        picks: list[dict] = []
        seen: set[str] = set()
        last = first
        for _ in range(8):
            pk = self._proxy_kwargs() or last
            last = pk
            key = pk.get("https") or pk.get("http") or ""
            if not key:
                return [{}]
            if key not in seen:
                seen.add(key)
                if not self._phone_proxy_hot(key):
                    picks.append(pk)
            if len(picks) >= 3 or len(seen) >= 8:
                break
        # Direct (no proxy) always gets the last slot: its own per-IP
        # budget refills within minutes and must keep being harvested.
        picks.append({})
        return picks

    def fetch_phone_endpoint(
        self,
        session: requests.Session,
        url: str,
        referer: str,
    ) -> str:
        """GET a click-to-show phone endpoint in the page's own session.

        Sites that keep the number out of the detail HTML (kn.kz) serve
        it from an XHR endpoint. Answers seen in the wild: JSON with a
        "phones" array (kn.kz) or an HTML fragment with a tel: link.
        Returns the first valid, non-blacklisted phone or "".
        """
        if not url:
            return ""
        # Soft-blocked (too many consecutive 4xx): skip the HTTP call so
        # we stop feeding the rate-limiter and the block can lift.
        if time.monotonic() < self._phone_block_until:
            return ""
        # Rotate exit IPs across attempts: the reveal budget is per IP, so
        # retrying through the same proxy only re-hits an empty bucket.
        # The sticky page session carries no proxies (they are per-request
        # in _fetch_cffi), so they must be passed explicitly here too.
        # Every chain entry gets its own attempt — a dead proxy or an
        # empty bucket must not prevent the next exit IP from being tried.
        tries = self._phone_proxy_tries()
        resp = None
        for attempt, pk in enumerate(tries):
            if attempt:
                time.sleep(self.phone_endpoint_cooldown)
            try:
                # Serialize across the _enrich_photos thread pool: the reveal
                # API rate-limits rapid concurrent calls (OLX -> 400).
                with self._phone_endpoint_lock:
                    interval = self.phone_endpoint_min_interval
                    if interval and self.phone_endpoint_jitter:
                        interval *= 1.0 + random.uniform(
                            -self.phone_endpoint_jitter,
                            self.phone_endpoint_jitter)
                    elapsed = time.monotonic() - self._phone_last_call
                    if elapsed < interval:
                        time.sleep(interval - elapsed)
                    self._phone_last_call = time.monotonic()
                    tmo = self.timeout if not pk else min(self.timeout, 5)
                    resp = session.get(url, timeout=tmo, proxies=pk, headers={
                        "Referer": referer,
                        "X-Requested-With": "XMLHttpRequest",
                        "Accept": "*/*",
                    })
            except Exception as exc:
                log.debug("[%s] phone endpoint failed: %s", self.name, exc)
                phone_log.info("[%s] endpoint %s | error=%s (%s)",
                               self.name, url[:160], type(exc).__name__,
                               (pk.get("https") or "direct")[:40])
                if pk:
                    self._note_phone_proxy_spent(pk)
                continue
            self._note_proxy_used(pk)
            if resp.status_code == 200:
                self._phone_consec_fail = 0
                self._note_phone_proxy_spent(pk)
                break
            # 400/403/429/503 = soft-block (rate-limit / bot flag). A 400 on
            # a proxied exit just means that IP's bucket is empty — the next
            # chain entry has its own budget, so keep going. A 403 also marks
            # the exit burnt (WAF). The consecutive-failure breaker only
            # matters for single-exit (direct) parsers like kn/krisha; OLX
            # disables it (phone_block_threshold = 0) because OLX 400s are
            # unpunished bucket drains, not escalating blocks.
            if resp.status_code in (400, 403, 429, 503):
                if pk and resp.status_code == 403:
                    self._note_phone_proxy_spent(pk)
                if self.phone_block_threshold:
                    self._phone_consec_fail += 1
                    if (self._phone_consec_fail
                            >= self.phone_block_threshold):
                        self._phone_block_until = (
                            time.monotonic() + self.phone_block_cooldown)
                        phone_log.info(
                            "[%s] endpoint %s | blocked after %d consecutive "
                            "%s responses, pausing %.0fs",
                            self.name, url[:160], self._phone_consec_fail,
                            resp.status_code, self.phone_block_cooldown)
                phone_log.info(
                    "[%s] endpoint %s | http=%s via %s (try %d/%d)",
                    self.name, url[:160], resp.status_code,
                    (pk.get("https") or "direct")[:40],
                    attempt + 1, len(tries))
                continue
            log.debug("[%s] phone endpoint %s -> HTTP %s",
                      self.name, url, resp.status_code)
            phone_log.info("[%s] endpoint %s | http=%s",
                           self.name, url[:160], resp.status_code)
            return ""
        if resp is None or resp.status_code != 200:
            return ""
        body = resp.text or ""
        m = re.search(r'"phones"\s*:\s*\[([^\]]*)\]', body)
        if m:
            for part in re.findall(r'"([^"]+)"', m.group(1)):
                phone = self._valid_phone(part)
                if phone and phone not in self.phone_blacklist:
                    phone_log.info("[%s] endpoint %s | found %s",
                                   self.name, url[:160], phone)
                    return phone
        # JSON answers with a phone key: "phone": "+7 ...", "tel": 7705...
        for m in self._PHONE_KEY_RE.finditer(body):
            phone = self._valid_phone(
                m.group(1) or m.group(2) or m.group(3) or m.group(4) or "")
            if phone and phone not in self.phone_blacklist:
                phone_log.info("[%s] endpoint %s | found %s",
                               self.name, url[:160], phone)
                return phone
        # HTML-fragment answers: reuse the page extractor (tel: links, ...)
        phone = self.extract_detail_phone(body)
        if phone:
            phone_log.info("[%s] endpoint %s | found %s",
                           self.name, url[:160], phone)
        else:
            phone_log.info("[%s] endpoint %s | no_phone", self.name, url[:160])
        return phone

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
