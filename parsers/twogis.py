"""2GIS Almaty realty + agencies parser.

Reads the 2GIS realty layer (``2gis.kz/almaty``) via the internal XHR JSON
endpoint the SPA uses to render listings on the map. **JSON-first, no HTML
scraping, no OCR, no LLM** — matches the policy in ``task_2gis_2do.md`` §0.2
and the source doc §9/§28/§44.

BLOCKER (task_2gis_2do.md §6): the exact realty XHR endpoint, its params and
the response schema must be confirmed via a one-time DevTools Network
inspection of a live ``2gis.kz/almaty`` realty/Arenda page. The constants
below (``REALTY_ENDPOINT``, ``REGION_ID``, ``RUBRIC_*``) are placeholders shaped
after 2GIS public conventions and a representative realty response schema.

Until ``REALTY_ENDPOINT`` is verified and ``_realty_configured()`` returns
``True``, ``run()`` returns ``[]`` with ``status="not_configured"`` rather than
hitting a guessed URL — we never fabricate a working live scrape.

All deterministic extraction logic (price-context, rooms, floor, area, phone
normalization, listing_type, owner/agent, fingerprint) is fully implemented
and unit-tested against JSON fixtures regardless of the endpoint state.
"""
from __future__ import annotations

import json
import random
import re
import time
from datetime import datetime
from typing import Any

from .base import (
    BaseParser,
    ParserRunStats,
    _LAST_PARSER_STATS,
    log,
    normalize_phone,
    parse_float,
    parse_int,
    property_fingerprint,
)
from .models import Listing, SearchParams

# ---------------------------------------------------------------------------
# Stage-1 RECON COMPLETE (2026-08-16): endpoint + params verified live.
# Endpoint discovered in 2gis.kz SPA config + app.js bundle:
#   marketApiUrl = https://market-backend.api.2gis.ru/5.0/
#   GET_REALTY_URL = `${marketApiUrl}realty/items`
# Required params (from app.js getRealtyItems + DevTools-style recon):
#   region_id (Almaty realty region): 9430034490064971
#   category_ids: see CATEGORY_* below (rent-daily-sale)
#   point1/point2 bounding box in "lon,lat" format
#     (point1=NW: smaller lon, larger lat; point2=SE: larger lon, smaller lat)
#   platform_code: 4  (2GIS web CLIENT_ID)
#   locale: ru_KZ
# fields: structured projection of the response objects.
# ---------------------------------------------------------------------------
REALTY_CONFIGURED: bool = True

# Live realty endpoint verified working (HTTP 200, JSON).
REALTY_ENDPOINT = "https://market-backend.api.2gis.ru/5.0/realty/items"
# 2GIS Almaty realty region id (NOT catalog region_id=32).
REGION_ID = "9430034490064971"
# 2GIS web client_id used in platform_code param.
CLIENT_ID = "4"
# Almaty bounding box in "lon,lat" format (NW corner / SE corner).
# Covers the city from airport south to БАК; loose enough to catch suburbia.
POINT1_NW = "76.78,43.45"  # NW corner: smaller lon, larger lat
POINT2_SE = "77.20,43.10"  # SE corner: larger lon, smaller lat

# Real category_ids discovered via live probe (see tools/probe_all_categories.py).
# All categories return 0 listings for Almaty on 2026-08-16 — this matches the
# source-doc warning §2/§44 (KZ realty layer launched with sale+daily, long-term
# rent "планируется"). Endpoint works; data not yet available.
RUBRIC_RENT_LONG = "70241201812768719"   # Аренда жилой недвижимости (long-term)
RUBRIC_RENT_DAILY = "70241201813054308"  # Посуточная аренда недвижимости
RUBRIC_RENT_COMMERCIAL = "70241201812768720"
RUBRIC_SALE = "70241201812761646"        # Продажа жилой недвижимости
# Default requested selection: long-term rent first (or fallback to sale).
DEFAULT_CATEGORY_IDS = (RUBRIC_RENT_LONG,)

# Structured field projection from the SPA (app.js bundle). This is the
# set of fields the 2GIS web client asks for, so response items have these
# keys — exact shape varies per item, parser is tolerant.
REALTY_FIELDS = (
    "items.adm_div,items.address,items.ads,items.contact,"
    "items.realty_options,items.ext,items.geometry,"
    "items.description,items.photo,items.title"
)

# Query matrix for agency discovery (task_2gis_2do.md §7.1, source §13).
QUERY_MATRIX_A = (
    "аренда квартир", "аренда квартиры", "агентство недвижимости аренда",
    "агентство аренды квартир", "агентства по аренде квартир",
    "риелтор по аренде квартир", "риэлтор по аренде квартир",
    "снять квартиру", "сдам квартиру", "сдача квартир",
    "аренда жилья", "аренда жилья Алматы", "квартиры в аренду", "квартиры на аренду",
)

# ---------------------------------------------------------------------------
# Deterministic extraction patterns (no LLM, no OCR).
# ---------------------------------------------------------------------------
# Price: number + optional thousand multiplier + currency word/symbol.
_PRICE_RE = re.compile(
    r"(?<!\d)(\d[\d\s.]{0,})\s*"
    r"(тыс\.?|тысяч|тысяча|мың|млн|миллион|тг|тенге|теңге|₸|kzt|к|k)?",
    re.I,
)
# Bare-price short shorthand "180к" / "40k" with no currency word.
_PRICE_K_RE = re.compile(r"(?<!\d)(\d{1,3})\s*([кk])\b", re.I)
# Context keywords (Russian + Kazakh) telling which cost a number belongs to.
_CTX_RENT = re.compile(r"аренд|сда[ёе]т|жалға бер|айына|әр ай|месяц|в месяц|цена|баға|құны|стоимость", re.I)
_CTX_DEPOSIT = re.compile(r"депозит|залог", re.I)
_CTX_COMMISSION = re.compile(r"комисс|риелтор|риэлтор|агент", re.I)
_CTX_UTILITIES = re.compile(r"коммунал|ком\.?\s*услуг|коммуналк|коммуналды", re.I)
_CTX_DAILY = re.compile(r"/сут|за сутки|сутки|тәулік|жеке күн|посуточн", re.I)
_CTX_PER_PERSON = re.compile(r"за человека|с человека|за одного|за чел", re.I)
_CTX_FIRST_PAY = re.compile(r"первый|первый месяц|заход|заезд|въезд", re.I)
_CTX_REFUND = re.compile(r"возвратн|қайтару|за счет", re.I)

_PHONE_RE = re.compile(r"(?<!\d)(?:\+?7|8)\s*\d{3}[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}(?!\d)")
_WA_RE = re.compile(r"(?:wa\.me/|api\.whatsapp\.com/send\?phone=)(\d{6,15})", re.I)
_TG_RE = re.compile(r"(?<!\w)@([A-Za-z0-9_]{5,32})\b|(?:https?://)?t\.me/([A-Za-z0-9_]{5,32})\b", re.I)

_2GIS_URL_RE = re.compile(r"2gis\.kz/almaty/(?:geo|firm|([^/\s\"']+))", re.I)
_PHOTO_TILE_RE = re.compile(r"(?:tile\d?\.maps\.2gis\.com|maps\.2gis\.com|mapgl)", re.I)

# listing_type classification — ordered; first match wins (see §4.2).
_RE_ROOMMATE = re.compile(
    r"\b(подселени|подселение|ищем\s+(девушк|парн|сосед)|нужен\s+сосед|"
    r"нужна\s+соседк|возьму\s+(девушк|парн|сосед)|қыз\s+керек|ұл\s+керек|"
    r"подселениеге)\b", re.I)
_RE_SUBLET = re.compile(r"сдаю?\s+комнат|жалу\s+берем|жалға\s+береді.*?бөлме", re.I)
_RE_PRIVATE_ROOM = re.compile(r"\b(комната|бөлме)\b", re.I)
_RE_HOUSE = re.compile(r"\b(частный\s+дом|сдается\s+дом|сдам\s+дом|үй\s+жалға)\b", re.I)
_RE_WHOLE = re.compile(r"квартир|пәтер|бөлмелі|студия", re.I)
_RE_WANTED = re.compile(r"\b(ищу|сниму|куплю|іздеймін|жалдаймын|алам|керегім)\b", re.I)
_RE_DAILY = _CTX_DAILY  # short-term marker

# Owner/agent signal banks (source §22, matches task_telega_2do.md §11).
_AGENT_SIGNALS = re.compile(
    r"комисс|риелтор|риэлтор|агентств|АН\b|агент\b|услуги\s+риелтор", re.I)
_OWNER_SIGNALS = re.compile(
    r"я\s+собственник|собственник|сама\s+хозяйк|сам\s+хозяин|без\s+посредник|"
    r"без\s+риелтор", re.I)

# Urgency / freshness words (source §17, task_telega_2do.md §8.3).
_URGENT_RE = re.compile(r"срочно|сегодня|заселение\s+сегодня|освободилась|заехать\s+можно", re.I)

# --- Geo / address -------------------------------------------------------
# Residential complexes (ЖК) — a representative Almaty set; extend freely.
_ZHK_RE = re.compile(
    r"\b(?:ЖК|ЖК\s+|тұрғын\s+кешен)\s*([А-Яа-яЁёA-Za-z0-9№\-\s]{2,40})", re.I)
_LANDMARK_RE = re.compile(
    r"(?:возле|рядом\s+с|напротив|около|жанында|жақын)\s+([А-Яа-яЁё0-9\s\-]{2,40})",
    re.I)


def _extract_costs(text: str) -> dict:
    """Context-aware cost extraction (task_2gis_2do.md §10, source §7).

    Returns a dict with ``rent``, ``deposit``, ``commission_percent``,
    ``commission_fixed``, ``utilities`` (included/separate/unknown),
    ``utilities_min``, ``utilities_max``, ``price_per_person``, ``first_payment``.

    Never takes the first number as rent blindly. Specific cost keywords
    (депозит / комиссия / коммунальные / первый платёж) PRECEDE the number in
    Russian listings, so we only trust them in a short **backward** window.
    Rent markers (тг/месяц/аренда) typically FOLLOW the number, so rent is the
    default branch when no specific keyword preceded the number.
    """
    out: dict[str, Any] = {
        "rent": None, "deposit": None, "deposit_refundable": None,
        "commission_percent": None, "commission_fixed": None,
        "utilities": "unknown", "utilities_min": None, "utilities_max": None,
        "price_per_person": False, "first_payment": None,
    }
    if not text:
        return out

    def _scale(num: int, unit: str | None) -> int:
        u = (unit or "").lower()
        if u.startswith(("тыс", "тысяч", "мың")):
            return num * 1000
        if u.startswith(("млн", "миллион")):
            return num * 1_000_000
        if u == "к":
            return num * 1000
        return num

    BACK = 30   # keyword-before-number window (specific costs precede).
    FWD = 30    # forward window for rent markers / per-person / refund.
    for m in _PRICE_RE.finditer(text):
        num = parse_int(m.group(1))
        if num is None:
            continue
        num = _scale(num, m.group(2))
        back = text[max(0, m.start() - BACK): m.start()]
        fwd = text[m.end(): m.end() + FWD]
        # Daily price anywhere nearby → out of scope, skip this number.
        if _CTX_DAILY.search(back) or _CTX_DAILY.search(fwd):
            continue
        # Deposit (keyword precedes the number).
        if _CTX_DEPOSIT.search(back[-25:]):
            if out["deposit"] is None and 5000 <= num <= 2_000_000:
                out["deposit"] = num
                if _CTX_REFUND.search(back) or _CTX_REFUND.search(fwd):
                    out["deposit_refundable"] = True
            continue
        # Commission: "Комиссия 30%" (percent) or "Комиссия 50 000" (fixed).
        if _CTX_COMMISSION.search(back[-25:]):
            # Include the number itself + trailing context so "30%" resolves.
            tail = text[m.start(): m.end() + 12]
            if "%" in tail and out["commission_percent"] is None:
                cm = re.search(r"(\d{1,3})\s*%", tail)
                if cm:
                    p = int(cm.group(1))
                    if 1 <= p <= 100:
                        out["commission_percent"] = p
            elif out["commission_fixed"] is None and 5000 <= num <= 1_000_000:
                out["commission_fixed"] = num
            continue
        # Utilities: "Коммунальные 5–6 тыс" → min/max (possibly scaled).
        if _CTX_UTILITIES.search(back[-25:]):
            seg = text[max(0, m.start() - 15): m.end() + 20]
            rng = re.search(
                r"(\d[\d\s.]*)\s*[–\-]\s*(\d[\d\s.]*)\s*"
                r"(тыс|тысяч|мың|к|k)?", seg, re.I)
            if rng and out["utilities_min"] is None:
                lo = _scale(parse_int(rng.group(1)) or 0, rng.group(3))
                hi = _scale(parse_int(rng.group(2)) or 0, rng.group(3))
                if 1000 <= lo <= hi <= 200_000:
                    out["utilities_min"] = lo
                    out["utilities_max"] = hi
            out["utilities"] = "separate"
            continue
        # First payment (keyword precedes).
        if _CTX_FIRST_PAY.search(back[-25:]):
            if out["first_payment"] is None and 10000 <= num <= 2_000_000:
                out["first_payment"] = num
            continue
        # Default → rent (first plausible rent wins).
        if out["rent"] is None and 30000 <= num <= 200_000_000:
            out["rent"] = num
            if _CTX_PER_PERSON.search(back) or _CTX_PER_PERSON.search(fwd):
                out["price_per_person"] = True

    # "180к" / "40k" shorthand missed by _PRICE_RE when no currency word
    # follows and the unit wasn't captured.
    for m in _PRICE_K_RE.finditer(text):
        num = int(m.group(1)) * 1000
        window = text[max(0, m.start() - 40): m.end() + 40]
        if 30000 <= num <= 200_000_000 and out["rent"] is None \
                and _CTX_RENT.search(window):
            out["rent"] = num

    # "всё включено" / "коммуналки включены" override separate→included.
    if re.search(r"вс[ёе]\s+включено|коммуналк[ии]\s+включен|включая\s+коммунал",
                text, re.I):
        out["utilities"] = "included"
        out["utilities_min"] = None
        out["utilities_max"] = None
    return out



def _extract_rooms(text: str) -> int | None:
    if not text:
        return None
    m = re.search(r"\b(\d)\s*[-]?\s*комн", text, re.I)
    if m:
        n = int(m.group(1))
        return n if 0 <= n <= 6 else None
    m = re.search(r"\b(\d)\s*[-]?\s*бөлмелі", text, re.I)
    if m:
        n = int(m.group(1))
        return n if 1 <= n <= 6 else None
    if re.search(r"\bстудия\b|\bстудию\b|\bстудиялық\b", text, re.I):
        return 0
    word_map = {"одно": 1, "двух": 2, "трёх": 3, "трех": 3,
                "четырёх": 4, "четырех": 4, "пяти": 5,
                "бір": 1, "екі": 2, "үш": 3, "төрт": 4, "бес": 5}
    m = re.search(
        r"(одно|двух|трёх|трех|четырёх|четырех|пяти|бір|екі|үш|төрт|бес)\s*комн",
        text, re.I)
    if m:
        return word_map.get(m.group(1).lower())
    return None


def _extract_area(text: str) -> float | None:
    if not text:
        return None
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*/\s*\d+\s*/\s*\d+", text)
    if m:
        a = parse_float(m.group(1))
        if a and 10 <= a <= 400:
            return a
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*м[²\^2]", text, re.I)
    if m:
        a = parse_float(m.group(1))
        if a and 10 <= a <= 400:
            return a
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*шаршы", text, re.I)
    if m:
        a = parse_float(m.group(1))
        if a and 10 <= a <= 400:
            return a
    return None


def _extract_floor(text: str) -> tuple[int | None, int | None]:
    if not text:
        return None, None
    m = re.search(r"(\d+)\s*/\s*(\d+)\s*(?:панель|эт|этаж|жил|кв|қабат|қабаты)",
                  text, re.I)
    if m:
        f, tf = int(m.group(1)), int(m.group(2))
        if 1 <= f <= tf <= 60:
            return f, tf
    for pat in (r"(\d+)\s*эт(?:аж)?", r"(\d+)\s*қабат(?:ы)?"):
        m = re.search(pat, text, re.I)
        if m:
            f = int(m.group(1))
            if 1 <= f <= 60:
                return f, None
    return None, None


def _classify_listing_type(text: str) -> str:
    """Ordered classification — first regex match wins (task_2gis_2do.md §4.2)."""
    if not text:
        return "UNKNOWN"
    low = text.lower()
    if _RE_WANTED.search(low[:80]):
        return "WANTED"
    if _RE_DAILY.search(low):
        return "SHORT_TERM"
    if _RE_ROOMMATE.search(low):
        return "ROOMMATE"
    if _RE_SUBLET.search(low):
        return "SUBLET"
    if _RE_HOUSE.search(low):
        return "HOUSE"
    if _RE_PRIVATE_ROOM.search(low):
        return "PRIVATE_ROOM"
    if _RE_WHOLE.search(low):
        return "WHOLE_APARTMENT"
    return "UNKNOWN"


def _owner_agent_prob(text: str, has_org: bool = False) -> tuple[float, float]:
    """Rule-based owner/agent probabilities (source §22). Returns (owner, agent)."""
    if not text and not has_org:
        return 0.5, 0.5
    score = 0.5
    if has_org:
        score -= 0.4  # tied to a 2GIS organization → likely agency.
    if text:
        if _OWNER_SIGNALS.search(text):
            score += 0.35
        if _AGENT_SIGNALS.search(text):
            score -= 0.35
    if score > 1.0:
        score = 1.0
    if score < 0.0:
        score = 0.0
    return round(score, 2), round(1.0 - score, 2)


def compute_quality_score(l: Listing) -> int:
    """0..100 rule-based quality (task_2gis_2do.md §12, source §21)."""
    s = 0
    if l.price is not None:
        s += 20
    if (l.lat is not None and l.lon is not None) or l.residential_complex \
            or l.normalized_location:
        s += 15
    if l.photo:
        s += 15
    if l.area is not None:
        s += 10
    if l.rooms is not None:
        s += 10
    if l.deposit is not None:
        s += 10
    if l.utilities and l.utilities != "unknown":
        s += 10
    if l.available_from:
        s += 5
    if l.phone or l.contact_telegram or l.contact_whatsapp:
        s += 5
    # Penalties
    if l.owner_probability is not None and l.owner_probability < 0.4:
        s -= 20
    if l.duplicate_group_id and (l.sources_count or 1) <= 1:
        s -= 0  # only within-group non-master duplicates are penalized upstream
    if l.price is None and not l.address and not l.photo:
        s -= 15
    return max(0, min(100, s))


def compute_freshness_score(date_published: str) -> float:
    """0..1 freshness by hours since publish (task_2gis_2do.md §8.1)."""
    if not date_published:
        return 0.0
    try:
        # Accept full ISO timestamp or YYYY-MM-DD.
        ts = date_published.replace("Z", "")
        dt = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return 0.0
    age_h = (datetime.now(dt.tzinfo) - dt).total_seconds() / 3600.0
    if age_h < 0:
        age_h = 0
    if age_h <= 2:
        return 1.0
    if age_h <= 6:
        return 0.95
    if age_h <= 12:
        return 0.90
    if age_h <= 24:
        return 0.80
    if age_h <= 48:
        return 0.60
    if age_h <= 168:
        return 0.30
    return 0.10


class TwoGisParser(BaseParser):
    """2GIS Almaty realty + agencies (JSON-XHR-first).

    The realty layer renders client-side; listings come from an XHR JSON
    endpoint. ``parse()`` accepts a JSON string (path used in production and
    tests) and degrades to an empty list for plain HTML (the SPA shell carries
    no listings). ``run()`` iterates the configured endpoint with polite delays;
    when ``REALTY_CONFIGURED`` is False it returns ``[]`` with
    ``status="not_configured"`` instead of guessing a URL.
    """

    name = "twogis"
    base_url = "https://2gis.kz"
    # 2GIS is bot-aware; go through curl_cffi browser TLS like olx/etagi.
    use_cffi = True
    cffi_impersonate = "chrome"
    session_sticky = True
    enrich_photo_count = 0          # photos already embedded in realty JSON
    category_title_markers: tuple[str, ...] = ()
    min_page_size = 1
    max_pages = 3

    # ---- URL building (unused — run() drives the XHR endpoint directly) ----
    def _build_url_base(self, params: SearchParams) -> str:
        return REALTY_ENDPOINT

    def build_url(self, params: SearchParams, page: int = 1) -> str:
        return REALTY_ENDPOINT

    # ---- parse(): JSON-first, HTML degraded --------------------------------
    def parse(self, html_or_json: str, params: SearchParams) -> list[Listing]:
        """Parse a 2GIS realty response.

        Accepts either a JSON string (the realty XHR payload — primary path)
        or an HTML string (the SPA shell — returns ``[]`` since listings load
        client-side). Honours the same contract as ``BaseParser.parse``:
        returns ``list[Listing]`` and never raises on malformed input.
        """
        if not html_or_json:
            return []
        s = html_or_json.strip()
        if s and s[0] in "{[":
            try:
                payload = json.loads(s)
            except (json.JSONDecodeError, ValueError):
                log.debug("[%s] malformed JSON payload, skipping", self.name)
                return []
            return self._parse_realty_json(payload, params)
        # Server-rendered SPA shell: the first page of listings is embedded in
        # the React Query dehydrated state. Extract it; if absent, return [].
        payload = self._extract_realty_dehydrated(html_or_json)
        if payload is not None:
            return self._parse_realty_json(payload, params)
        return []

    @staticmethod
    def _extract_realty_dehydrated(html: str) -> dict | None:
        """Pull the getRealtyItems payload out of a 2GIS realty SPA shell.

        The realty page embeds the first page of listings in a React Query
        dehydrated state (``JSON.parse('{"mutations":...}')``). Return the
        getRealtyItems ``state.data`` wrapped as ``{"result": data}`` so the
        standard ``_parse_realty_json`` path handles it, or ``None``.
        """
        marker = "JSON.parse('{\"mutations\""
        start = html.find(marker)
        if start < 0:
            return None
        q = html.find("'", start + len("JSON.parse("))
        if q < 0:
            return None
        j = q + 1
        while j < len(html):
            if html[j] == "\\":
                j += 2
                continue
            if html[j] == "'":
                break
            j += 1
        raw = html[q + 1:j]
        unescaped = raw.replace("\\'", "'").replace('\\"', '"').replace("\\\\", "\\")
        try:
            state = json.loads(unescaped)
        except (json.JSONDecodeError, ValueError):
            return None
        for query in state.get("queries", []):
            key = query.get("queryKey", [])
            if key and str(key[0]).startswith("getRealtyItems"):
                data = query.get("state", {}).get("data")
                if isinstance(data, dict) and data.get("items"):
                    return {"result": data}
        return None

    def _parse_soup(self, soup, params: SearchParams):  # type: ignore[override]
        # 2GIS is JSON-first; soup path is intentionally a no-op.
        return []

    # ---- Realty JSON → list[Listing] ---------------------------------------
    def _parse_realty_json(self, payload: Any, params: SearchParams) -> list[Listing]:
        """Map a 2GIS realty JSON payload to ``Listing`` records.

        The exact response shape is confirmed during the Stage-1 DevTools
        research; the field-path constants below (``_F_*``) are indexed from a
        representative realty schema and re-pointed as needed. Extractors are
        robust to missing fields: every ``get``/``find`` returns ``None``
        gracefully and the listing is still built from whatever is present.
        """
        if not isinstance(payload, dict):
            return []
        # 2GIS realty payloads nest the items under several possible keys.
        items = self._find_items(payload)
        listings: list[Listing] = []
        for it in items:
            try:
                l = self._item_to_listing(it)
            except Exception as exc:
                log.debug("[%s] item skipped: %s", self.name, exc)
                continue
            if l is not None:
                listings.append(l)
        log.debug("[%s] parsed %d realty items", self.name, len(listings))
        return listings

    @staticmethod
    def _find_items(payload: dict) -> list[dict]:
        """Locate the listings array in a 2GIS realty payload.

        2GIS wraps results under ``data.items`` / ``result.items`` /
        ``result.adverts`` depending on the version; try each in order.
        """
        for key in ("data", "result", "response", "payload"):
            node = payload.get(key)
            if isinstance(node, dict):
                for arr_key in ("items", "adverts", "listings", "ads"):
                    arr = node.get(arr_key)
                    if isinstance(arr, list):
                        return arr
        # Fallback: a top-level array key.
        for arr_key in ("items", "adverts", "listings", "ads"):
            arr = payload.get(arr_key)
            if isinstance(arr, list):
                return arr
        return []

    def _spa_item_to_listing(self, it: dict) -> Listing | None:
        """Convert a 2GIS SPA-shell realty item to a ``Listing``.

        SPA items nest the property under ``product``, the pricing/URL under
        ``offer``, the agency under ``branch`` and the address under
        ``building`` -- unlike the legacy ``item.ads[]`` XHR payload.
        """
        product = it.get("product") if isinstance(it.get("product"), dict) else {}
        offer = it.get("offer") if isinstance(it.get("offer"), dict) else {}
        branch = it.get("branch") if isinstance(it.get("branch"), dict) else {}
        building = it.get("building") if isinstance(it.get("building"), dict) else {}

        def _s(v):
            return v if isinstance(v, str) else (str(v) if v is not None else "")

        title = _s(product.get("name") or offer.get("button_name") or "")
        description = _s(product.get("description") or "")

        price_raw = offer.get("price")
        if isinstance(price_raw, dict):
            price_raw = price_raw.get("value")
        price = parse_int(str(price_raw)) if price_raw is not None else None
        currency = _s(offer.get("currency") or "KZT")
        if currency.upper() in ("KZT", "\u20b8"):
            currency = "\u0442\u0433"

        rent_per_m2 = None
        ppm_raw = offer.get("price_per_meter_value")
        if isinstance(ppm_raw, dict):
            fx = ppm_raw.get("fixed")
            ppm_raw = fx.get("value") if isinstance(fx, dict) else ppm_raw.get("value")
        if ppm_raw is not None:
            rent_per_m2 = parse_float(str(ppm_raw))

        rooms = area = floor = total_floors = None
        for attr in product.get("attributes", []):
            if not isinstance(attr, dict):
                continue
            caption = _s(attr.get("caption")).lower()
            value = attr.get("value")
            if value is None:
                continue
            if "\u043a\u043e\u043c\u043d\u0430\u0442" in caption or "room" in caption:
                rooms = parse_int(str(value))
            elif "\u043f\u043b\u043e\u0449\u0430\u0434" in caption or "area" in caption:
                area = parse_float(str(value))
            elif "\u044d\u0442\u0430\u0436" in caption and "\u044d\u0442\u0430\u0436\u043d" not in caption:
                floor = parse_int(str(value))
            elif "\u044d\u0442\u0430\u0436\u043d" in caption or "floor" in caption:
                total_floors = parse_int(str(value))
        if rooms is None and description:
            rooms = _extract_rooms(description)
        if area is None and description:
            area = _extract_area(description)
        if floor is None and description:
            f2, tf2 = _extract_floor(description)
            floor = f2
            total_floors = total_floors or tf2

        address = _s(building.get("address_name") or "")
        url = _s(offer.get("url") or "")
        photos = self._extract_photo_urls(product) or self._extract_photo_urls(it)
        photo = photos[0] if photos else ""

        listing_id_alt = _s(product.get("id")) or None
        building_id = _s(building.get("id")) or None
        provider_branch_id = _s(branch.get("id")) or None
        provider_org_id = _s(branch.get("org_id") or branch.get("org")) or None

        full_text = f"{title}\n{description}"
        listing_type = _classify_listing_type(full_text)
        deal_type = "long_term"
        normalized_location = self._normalize_location(address or full_text)

        return Listing(
            title=title,
            price=price,
            currency=currency,
            rooms=rooms,
            area=area,
            floor=floor,
            total_floors=total_floors,
            address=address,
            url=url,
            source="2gis",
            phone="",
            description=description,
            photo=photo,
            listing_id_alt=listing_id_alt,
            listing_type=listing_type,
            deal_type=deal_type,
            building_id=building_id,
            provider_org_id=provider_org_id,
            provider_branch_id=provider_branch_id,
            rent_per_m2=rent_per_m2,
            normalized_location=normalized_location,
        )

    def _item_to_listing(self, it: dict) -> Listing | None:
        """Convert one realty JSON item to a ``Listing``.

        Field paths follow 2GIS public conventions; structured fields are
        preferred (Level 1 in task_2gis_2do.md §10.4) and free-text fields
        feed the deterministic regex extractors (Level 3).
        """
        if isinstance(it, dict) and "product" in it and "offer" in it:
            return self._spa_item_to_listing(it)
        if not isinstance(it, dict):
            return None

        def g(*path, default=None):
            node = it
            for k in path:
                if not isinstance(node, dict):
                    return default
                node = node.get(k)
            return node if node is not None else default

        # Price — 2GIS realty uses item.ads[] (array of ad payloads);
        # structured field preferred over regex.
        title = g("title") or g("name") or ""
        description = g("description") or g("text") or ""
        ads = it.get("ads") or []
        first_ad = ads[0] if isinstance(ads, list) and ads else {}
        if isinstance(first_ad, dict):
            price_raw = (first_ad.get("price") or {}).get("value") \
                if isinstance(first_ad.get("price"), dict) \
                else first_ad.get("price")
            currency = (first_ad.get("price") or {}).get("currency", "KZT") \
                if isinstance(first_ad.get("price"), dict) else "KZT"
        else:
            price_raw = g("price", "value") or g("price") or g("cost")
            currency = g("price", "currency") or "KZT"
        if isinstance(price_raw, dict):
            price_raw = price_raw.get("value")
        price = parse_int(str(price_raw)) if price_raw is not None else None
        if currency and currency.upper() in ("KZT", "₸"):
            currency = "тг"

        # Rooms / area / floor: 2GIS realty items expose them at the top
        # level (item.rooms, item.area, item.floor, item.floors_total);
        # fall back to regex from description for missing structured values.
        rooms = g("rooms") or g("total_rooms")
        if isinstance(rooms, str):
            rooms = parse_int(rooms)
        if rooms is None and description:
            rooms = _extract_rooms(description)

        area = g("area") or g("total_area")
        if isinstance(area, (str, float, int)) and not isinstance(area, bool):
            area = parse_float(str(area))
        if area is None and description:
            area = _extract_area(description)

        floor = g("floor")
        total_floors = g("floors_total") or g("total_floors")
        if floor is None and description:
            f2, tf2 = _extract_floor(description)
            floor = floor or f2
            total_floors = total_floors or tf2
        if isinstance(floor, str):
            floor = parse_int(floor)
        if isinstance(total_floors, str):
            total_floors = parse_int(total_floors)

        # Coordinates — 2GIS realty item.geometry.centroid is {lat, lon}.
        geo = it.get("geometry") or {}
        if isinstance(geo, dict):
            centroid = geo.get("centroid") or {}
            lat = centroid.get("lat") or geo.get("lat") or g("lat")
            lon = centroid.get("lon") or geo.get("lng") or geo.get("lon") or g("lon")
        else:
            lat, lon = g("lat"), g("lon")
        building_id = g("building_id") or g("building", "id")
        address = (g("address") or g("building", "address")
                   or g("location") or "")

        # Provider / org provenance.
        provider = g("provider") or g("source") or g("company", "name")
        org_id = g("org_id") or g("company", "id") or g("provider_org_id")
        branch_id = g("branch_id") or g("company", "branch_id")

        # Photos — structured list of URLs.
        photos = self._extract_photo_urls(it)
        # Contacts — structured first, then description regex (2GIS listings
        # frequently carry the phone only in free text, like telegram posts).
        phone_raw = ""
        for p in (g("phone"),
                  g("phones", 0) if isinstance(g("phones"), list) else None,
                  g("contact", "phone")):
            if p:
                phone_raw = str(p)
                break
        if not phone_raw:
            m = _PHONE_RE.search(description or "")
            if m:
                phone_raw = m.group(0)
        phone = normalize_phone(phone_raw) or (re.sub(r"\s+", " ", phone_raw).strip() if phone_raw else "")
        contact_whatsapp = self._extract_whatsapp(description or "")
        contact_telegram = self._extract_telegram(description or "")

        # Free-text costs context (fills deposit/commission/utilities even
        # when structured price exists).
        costs = _extract_costs(description or "")
        rent = price if price is not None else costs["rent"]

        full_text = f"{title}\n{description}"
        listing_type = _classify_listing_type(full_text)
        # rental_period from structured or text.
        rental_period = g("rental_period") or g("period")
        if rental_period:
            rental_period = {"long_term": "long_term", "monthly": "monthly",
                             "daily": "daily", "short_term": "daily"}.get(
                str(rental_period).lower(), str(rental_period).lower())
        elif _RE_DAILY.search(full_text):
            rental_period = "daily"
        else:
            rental_period = "unknown"

        deal_type = "short_term" if rental_period == "daily" else "long_term"

        normalized_location = self._normalize_location(address or full_text)
        residential_complex = self._extract_residential_complex(full_text)
        landmark = self._extract_landmark(full_text)

        owner_prob, agent_prob = _owner_agent_prob(
            full_text, has_org=bool(org_id))
        if listing_type in ("ROOMMATE", "SUBLET") and costs["price_per_person"]:
            price_per_person = True
        else:
            price_per_person = costs["price_per_person"]

        listing = Listing(
            title=title[:120] or "2GIS listing",
            price=rent,
            currency=currency or "тг",
            rooms=rooms,
            area=area,
            floor=floor,
            total_floors=total_floors,
            address=address,
            url=g("url") or g("link") or g("external_url") or "",
            source=self.name,
            phone=phone,
            description=description,
            photo="|".join(photos),
            date_published=self._normalize_date(g("date_created")
                                               or g("published_at")
                                               or g("date")),
            lat=parse_float(str(lat)) if lat is not None else None,
            lon=parse_float(str(lon)) if lon is not None else None,
            # extended fields
            listing_id_alt=str(g("id") or g("external_id") or "") or None,
            listing_type=listing_type,
            deal_type=deal_type,
            rental_period=rental_period,
            provider=provider,
            building_id=str(building_id) if building_id is not None else None,
            provider_org_id=str(org_id) if org_id is not None else None,
            provider_branch_id=str(branch_id) if branch_id is not None else None,
            views=parse_int(str(g("views"))) if g("views") is not None else None,
            deposit=costs["deposit"],
            deposit_refundable=costs["deposit_refundable"],
            commission_percent=costs["commission_percent"],
            commission_fixed=costs["commission_fixed"],
            utilities=(costs["utilities"] if costs["utilities"] != "unknown"
                       else None),
            utilities_min=costs["utilities_min"],
            utilities_max=costs["utilities_max"],
            rent_per_m2=(round(rent / area, 2)
                         if rent and area else None),
            price_per_person=price_per_person or None,
            first_payment=costs["first_payment"],
            normalized_location=normalized_location,
            microdistrict=None,  # filled by building enrichment (§11)
            residential_complex=residential_complex,
            landmark=landmark,
            contact_telegram=contact_telegram,
            contact_whatsapp=contact_whatsapp,
            available_from=self._extract_available_from(full_text),
            owner_probability=owner_prob,
            agent_probability=agent_prob,
        )
        # Scoring + fingerprint.
        listing.freshness_score = compute_freshness_score(listing.date_published)
        listing.quality_score = compute_quality_score(listing)
        listing.duplicate_group_id = property_fingerprint(
            building_id=listing.building_id,
            normalized_location=listing.normalized_location,
            rooms=listing.rooms,
            area=listing.area,
            floor=listing.floor,
            price=listing.price,
        ) or None
        # Filter unwanted intents (mirror telegram parser): wanted ads and
        # short-term/daily rentals are out of scope for long-term search.
        if listing.listing_type in ("WANTED", "SHORT_TERM") \
                or listing.rental_period == "daily":
            return None
        return listing

    # ---- Small extraction helpers -----------------------------------------
    @staticmethod
    def _extract_photo_urls(item: dict) -> list[str]:
        """Collect photo URLs, rejecting 2GIS map tiles / avatars (cf. etagi)."""
        urls: list[str] = []
        for key in ("photos", "images", "gallery"):
            node = item.get(key)
            if isinstance(node, list):
                for p in node:
                    u = p if isinstance(p, str) else (
                        p.get("url") or p.get("src") or p.get("large")
                        if isinstance(p, dict) else None)
                    if u and not _PHOTO_TILE_RE.search(u) and u not in urls:
                        urls.append(u)
        return urls

    @staticmethod
    def _extract_whatsapp(text: str) -> str:
        m = _WA_RE.search(text or "")
        return normalize_phone("+" + m.group(1)) if m else ""

    @staticmethod
    def _extract_telegram(text: str) -> str:
        m = _TG_RE.search(text or "")
        if not m:
            return ""
        handle = m.group(1) or m.group(2)
        return handle if handle else ""

    @staticmethod
    def _normalize_location(text: str) -> str:
        if not text:
            return ""
        # "Толе би-Саина, напротив Asia Park" → "Толе би / Саина"-ish: keep
        # the first street/microdistrict fragment, strip qualifiers.
        m = re.search(r"((?:ул\.|улица|көш|мкр|микрорайон|пр\.|проспект|"
                      r"даңғ|ЖК|жк)[^,\n]{2,40})", text, re.I)
        return m.group(1).strip().rstrip(",.;") if m else text[:60].strip()

    @staticmethod
    def _extract_residential_complex(text: str) -> str | None:
        m = _ZHK_RE.search(text or "")
        if not m:
            return None
        return " ".join(m.group(1).split())[:60] or None

    @staticmethod
    def _extract_landmark(text: str) -> str | None:
        m = _LANDMARK_RE.search(text or "")
        if not m:
            return None
        return " ".join(m.group(1).split())[:60] or None

    @staticmethod
    def _extract_available_from(text: str) -> str | None:
        if not text:
            return None
        m = re.search(r"с\s+(\d{1,2}\s+[а-яё]+|завтрашнего дня)", text, re.I)
        if m:
            return " ".join(m.group(1).split())
        if re.search(r"заселение\s+сегодня|заехать\s+можно", text, re.I):
            return "сегодня"
        return None

    @staticmethod
    def _normalize_date(raw) -> str:
        if not raw:
            return ""
        s = str(raw)
        # Already ISO-ish (allow through).
        if re.match(r"\d{4}-\d{2}-\d{2}", s):
            return s[:19]
        # Unix epoch seconds.
        if s.isdigit() and len(s) >= 10:
            try:
                return datetime.utcfromtimestamp(int(s[:10])).isoformat()
            except (ValueError, OSError):
                pass
        return s

    # ---- Detail price hook (BaseParser.extract_detail_price contract) ------
    def extract_detail_price(self, html: str, url: str) -> tuple:
        # 2GIS detail data is JSON in the XHR, not on an HTML detail page.
        # If a JSON body was saved as the "detail" page, mine it; otherwise
        # fall through to the base behaviour.
        s = (html or "").strip()
        if s and s[0] in "{[":
            try:
                payload = json.loads(s)
            except (json.JSONDecodeError, ValueError):
                return super().extract_detail_price(html, url)
            items = self._find_items(payload) if isinstance(payload, dict) else []
            if items:
                l = self._item_to_listing(items[0])
                if l and l.price is not None:
                    return (l.price, None, None)
        return super().extract_detail_price(html, url)

    # ---- run(): JSON XHR with the BaseParser stats/error contract ---------
    def run(self, params: SearchParams) -> list[Listing]:
        stats = ParserRunStats(
            name=self.name, base_url=self.base_url,
            status="pending",
            timestamp=datetime.now().isoformat(sep=" ", timespec="seconds"),
        )
        self.last_stats = stats
        t0 = time.monotonic()
        all_results: list[Listing] = []

        if not REALTY_CONFIGURED:
            stats.status = "not_configured"
            stats.error = ("REALTY_CONFIGURED is False — run DevTools Stage-1 "
                          "research (task_2gis_2do.md §6) and set the endpoint "
                          "constants before enabling live fetching.")
            stats.duration_ms = (time.monotonic() - t0) * 1000
            _LAST_PARSER_STATS[self.name] = stats
            log.info("[%s] realty endpoint not configured — returning []", self.name)
            return []

        try:
            page_size = 20
            max_pages = params.max_pages or getattr(self, "max_pages", 3)
            for page in range(1, max_pages + 1):
                if page > 1:
                    break  # SPA shell only serves page 1
                if self.page_delay and page > 1:
                    time.sleep(random.uniform(*self.page_delay))
                url = self._build_realty_html_url(params, page)
                log.debug("[%s] realty fetch page %d: %s", self.name, page, url)
                body = self.fetch(url)
                results = self.parse(body, params)
                if not results:
                    log.debug("[%s] page %d: 0 results, stopping", self.name, page)
                    break
                stats.pages_fetched += 1
                all_results.extend(results)
                log.info("[%s] page %d: %d listings", self.name, page, len(results))
                if len(results) < page_size:
                    break  # last page
            deduped = self._dedupe(all_results)
            self._fingerprint_dedup_inplace(deduped)
            filtered = self.apply(deduped, params)
            stats.pages_fetched = stats.pages_fetched or 1
            stats.results_count = len(filtered)
            stats.status = "ok" if filtered else "empty"
            stats.duration_ms = (time.monotonic() - t0) * 1000
            _LAST_PARSER_STATS[self.name] = stats
            return filtered
        except Exception as exc:
            stats.status = "error"
            stats.error = str(exc)[:300]
            stats.error_type = type(exc).__name__
            stats.duration_ms = (time.monotonic() - t0) * 1000
            log.warning("[%s] realty run error: %s", self.name, exc, exc_info=True)
            _LAST_PARSER_STATS[self.name] = stats
            try:
                return self.apply(self._dedupe(all_results), params)
            except Exception:
                return []

    def _build_realty_html_url(self, params: SearchParams, page: int) -> str:
        """Build the 2GIS realty SPA page URL (server-rendered first page).

        The SPA shell embeds page 1 of the realty listings in its dehydrated
        state; deeper pages are client-side only, so ``page`` is accepted but
        every page resolves to the same shell.
        """
        return f"{self.base_url}/almaty/realty"

    def _build_realty_url(self, params: SearchParams, page: int,
                          page_size: int) -> str:
        """Build the realty XHR URL with the verified params from Stage-1.

        Shape (verified live 2026-08-16):
          GET https://market-backend.api.2gis.ru/5.0/realty/items
              ?region_id=9430034490064971&locale=ru_KZ
              &category_ids=<rent_long>|<sale>...
              &page=1&page_size=20
              &point1=lon,lat&point2=lon,lat    (NW / SE corners of Almaty)
              &platform_code=4                   (2GIS web CLIENT_ID)
              &fields=items.<projection>
        """
        from urllib.parse import urlencode
        # Apply SearchParams filter values where they map cleanly.
        cat_ids = ",".join(DEFAULT_CATEGORY_IDS)
        q = {
            "region_id": REGION_ID,
            "locale": "ru_KZ",
            "category_ids": cat_ids,
            "page": page,
            "page_size": page_size,
            "point1": POINT1_NW,
            "point2": POINT2_SE,
            "platform_code": CLIENT_ID,
            "fields": REALTY_FIELDS,
        }
        return f"{REALTY_ENDPOINT}?{urlencode(q)}"

    # ---- Cross-run fingerprint dedup (task_2gis_2do.md §9) -----------------
    def _fingerprint_dedup_inplace(self, listings: list[Listing]) -> None:
        """Group near-duplicates by ``duplicate_group_id`` and pick a master.

        Mutates in place: masters keep their data and get ``sources_count`` > 1;
        non-masters are dropped (kept only as a count). Identical fingerprints
        with enough signal collapse to one record.
        """
        groups: dict[str, list[Listing]] = {}
        for l in listings:
            if not l.duplicate_group_id:
                groups.setdefault(f"__u{l.url or id(l)}", []).append(l)
                continue
            groups.setdefault(l.duplicate_group_id, []).append(l)
        out: list[Listing] = []
        for grp in groups.values():
            if len(grp) == 1:
                grp[0].sources_count = 1
                out.append(grp[0])
                continue
            # Master = earliest published, then highest quality.
            master = min(grp, key=lambda x: (
                x.date_published or "9999", -(x.quality_score or 0)))
            master.sources_count = len(grp)
            if master.quality_score is not None:
                master.quality_score = min(100, master.quality_score + 5)
            out.append(master)
        listings[:] = out
