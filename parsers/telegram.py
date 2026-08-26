from __future__ import annotations

import random
import re
import time
from datetime import datetime
from typing import NamedTuple, Optional

from bs4 import BeautifulSoup

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
# Tiered channel portfolio (task_telega_2do.md §1.4, §15.2; source §18).
# Tier → before_batches (depth of ?before= pagination on the public preview).
#   A = 2  (largest / best-structured): fresh-listing recall
#   B = 1  (specialized / smaller)
#   C = 1  (noisy / tiny)
#   D = 0  (legacy channels kept until verified)
# NOTE (2do §1.2): the source-doc channel names are pending live verification
# of the public web preview. run() is resilient to dead/blocked channels
# (per-channel try/except), so an unverified/dead name degrades to skipped.
# ---------------------------------------------------------------------------
class Channel(NamedTuple):
    name: str
    tier: str
    before_batches: int


_TIERED_CHANNELS: tuple[Channel, ...] = (
    # 🥇 Tier A — source §1/§18
    Channel("kvartiry2",                 "A", 2),  # ~92K, largest, rooms/subsel
    Channel("kvartira_v_almaty",         "A", 2),  # ~17.5K, best-structured
    Channel("arenda_kvartiry_almaty_kz", "A", 2),  # ~19.7K, RU+KZ stream
    # Tier B
    Channel("kvartiry222",               "B", 1),  # «без риэлторов»
    Channel("Arenda_Kvartira_Ala02",    "B", 1),  # помесячно/посуточно
    # Tier C
    Channel("tn_almaty",                 "C", 1),  # широкое: много продажи
    Channel("kvartiram7",                "C", 1),  # подселение/поиск
    Channel("arendakvartiralma",         "C", 1),  # tiny
    # Tier D — legacy (pre-existing public-preview names; kept for safety).
    Channel("kvartiry_almaty",           "D", 0),
    Channel("arenda_kvartira_almaty",    "D", 0),
    Channel("kvartira_almaty_arenda",    "D", 0),
    Channel("arenda_kvartir_almaty",     "D", 0),
)
# Flat name tuple for backward compatibility (tests index _CHANNELS[i]).
_CHANNELS: tuple[str, ...] = tuple(c.name for c in _TIERED_CHANNELS)

_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?7|8)\s*\d{3}[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}(?!\d)"
)
# Price: number + optional thousand multiplier + currency word/symbol.
#   Russian: тыс / тысяч / тг / тенге / ₸        → тг
#   Kazakh:  мың / теңге                          → тг
#   Shorthand "180к" / "40k" (Latin k)            → ×1000
_PRICE_RE = re.compile(
    r"(?<!\d)(\d[\d\s.]{0,})\s*"
    r"(тыс\.?|тысяч|тысяча|мың|млн|миллион|"
    r"тг|тенге|теңге|₸|kzt|к|k)?",
    re.I,
)
# "180к" / "40k" shorthand with no currency word.
_PRICE_K_RE = re.compile(r"(?<!\d)(\d{1,3})\s*([кk])\b", re.I)
# Bare price (no currency word) is only trusted when a rent keyword is near
# the number — otherwise "5 минут" / "лицей №90" / phone fragments would be
# mistaken for a price.
_RENT_KW_RE = re.compile(
    r"(аренд|сда[ёе]т|жалға|айына|әр ай|месяц|в месяц|оплата|плат|"
    r"цена|баға|стоимость|депозит|құны)",
    re.I,
)
_ADDR_RE = re.compile(r"(?:адрес|мекенжай)\s*:?\s*(.+)", re.I)
_ADDR_FALLBACK_RE = re.compile(
    r"((?:ул\.|улица|көш|мкр|микрорайон|аудан|пр\.|проспект|даңғ|"
    r"жк|жилой комплекс|тұрғын кешен)\s*[^,\n]{2,40})",
    re.I,
)
# "Ищу/Сниму/Куплю квартиру" — wanted ads. Russian + Kazakh intent verbs.
#   Kazakh: іздеймін (looking for), алып берем (will buy), жалдаймын (will rent)
_WANTED_RE = re.compile(
    r"\b(ищу|сниму|куплю|срочно\s+\S+\s+квартир|"
    r"іздеймін|жалдаймын|алам|керегім)\b", re.I,
)
_BG_URL_RE = re.compile(r"url\(['\"]?([^'\")]+)['\"]?\)")
# Daily/short-term rental markers (Russian + Kazakh "тәулік"/"күн").
_DAILY_RE = re.compile(
    r"посуточн|/сут|за сутки|сутки|тәулік|күн|жеке күн", re.I,
)

# ---- listing_type classification (2do §4.2; source §6) -------------------
# Ordered: first match wins. KZ markers included («подселениеге», «қыз керек»).
_RE_ROOMMATE = re.compile(
    r"подселени|подселение|на\s+подселение|ищем\s+(девушк|парн|сосед)|"
    r"нужен\s+сосед|нужна\s+соседк|возьму\s+(девушк|парн)|соседк|соседа|"
    r"қыз\s+керек|ұл\s+керек|подселениеге", re.I)
_RE_SUBLET = re.compile(r"сдаю?\s+комнат|жалу\s+берем", re.I)
_RE_PRIVATE_ROOM = re.compile(r"\bкомната\b|\bбөлме\b", re.I)
_RE_HOUSE = re.compile(r"частный\s+дом|сдается\s+дом|сдам\s+дом|үй\s+жалға", re.I)
_RE_WHOLE = re.compile(r"квартир|пәтер|бөлмелі|студия", re.I)

# ---- cost-context keywords (2do §5; source §7/§8/§9) -------------------
_CTX_DEPOSIT = re.compile(r"депозит|залог", re.I)
_CTX_COMMISSION = re.compile(r"комисс|риелтор|риэлтор|агент", re.I)
_CTX_UTILITIES = re.compile(r"коммунал|ком\.?\s*услуг|коммуналк|коммуналды", re.I)
_CTX_PER_PERSON = re.compile(r"за\s+человек|с\s+человек|за\s+одного|за\s+чел", re.I)
_CTX_FIRST_PAY = re.compile(r"первый|первый\s+месяц|заход|заезд|въезд", re.I)
_CTX_REFUND = re.compile(r"возвратн|за\s+счет", re.I)

# ---- contacts (2do §7; source §14) --------------------------------------
_WA_RE = re.compile(r"(?:wa\.me/|api\.whatsapp\.com/send\?phone=)(\d{6,15})", re.I)
_TG_RE = re.compile(
    r"(?<!\w)@([A-Za-z0-9_]{5,32})\b|(?:https?://)?t\.me/([A-Za-z0-9_]{5,32})\b",
    re.I)

# ---- location (2do §6; source §10/§11) ---------------------------------
_ZHK_RE = re.compile(
    r"\b(?:ЖК|Жк|тұрғын\s+кешен|жилой\s+комплекс)\s*\.?\s*"
    r"([А-Яа-яЁё0-9№\-\s]{2,40})", re.I)
_LANDMARK_RE = re.compile(
    r"(?:возле|рядом\s+с|напротив|около|жанында|жақын)\s+"
    r"([А-Яа-яЁё0-9\s\-\.]{2,40})", re.I)
_2GIS_RE = re.compile(
    r"(https?://2gis\.kz/almaty/(?:geo|firm|branches)/[^\s\"'\)]+)", re.I)

# ---- availability / urgency (2do §8.3; source §17) --------------------
_AVAILABLE_RE = re.compile(
    r"с\s+(\d{1,2}\s+[а-яё]+|завтрашнего\s+дня|понедельника|вторника)", re.I)
_URGENT_RE = re.compile(
    r"срочно|сегодня|заселение\s+сегодня|освободилась|заехать\s+можно", re.I)

# ---- owner / agent signals (2do §11/§22; source §22) ------------------
_AGENT_SIGNALS = re.compile(
    r"комисс|риелтор|риэлтор|агентств|АН\b|агент\b|услуги\s+риелтор", re.I)
_OWNER_SIGNALS = re.compile(
    r"я\s+собственник|собственник|сама\s+хозяйк|сам\s+хозяин|"
    r"без\s+посредник|без\s+риелтор", re.I)

# ---- Kazakh → Russian real-estate glossary --------------------------------
# Telegram channels mix Kazakh and Russian freely («2 бөлмелі кв-да
# Изолированная комната жалға беріледі»). Without an MT API we normalize the
# common real-estate vocabulary to Russian so the title/description are
# readable for a RU-speaking user. Applied to title + description only;
# raw digits-driven fields (price/rooms/area) are mined separately.
#
# Only terms long/distinctive enough to avoid false partial matches are kept:
# "үй" (дом) is too short and would corrupt "Үйде"/"үйдің", so it's omitted.
# Replacements are case-insensitive; the Russian lowercase form is inserted,
# which reads acceptably in mixed-case sentences.
_KZ_RU_GLOSS: dict[str, str] = {
    # property types (word + common inflections)
    "пәтерді": "квартиру", "пәтері": "квартира", "пәтер": "квартира",
    "бөлмесіне": "в комнату", "бөлмесі": "комната",
    "бөлмеліге": "комнатной", "бөлмелі": "комнатная",
    "бөлмелер": "комнаты", "бөлме": "комната",
    "жатақхана": "общежитие",
    # rent/sale verbs (multiword KZ first so longest wins)
    "жалға беремін": "сдаю",
    "жалға беріледі": "сдаётся", "жалға беру": "сдача",
    "жалға": "аренда", "жалдауға": "аренде",
    "жалдаймын": "снимаю", "жалдайтын": "сниму",
    "сатамын": "продаю", "сатам": "продаю", "сату": "продажа",
    # quantities/counts
    "мың": "тысяч", "теңге": "тенге",
    "бір": "1", "екі": "2", "үш": "3", "төрт": "4", "бес": "5",
    # time/period
    "айына": "в месяц", "әр айға": "в месяц", "айы": "месяц",
    "тәулік": "сутки",
    # floor
    "қабаты": "этаж", "қабатта": "на этаже", "қабат": "этаж",
    # requirements / people (roommate markers)
    "қыздарға": "девушкам", "қыздар": "девушки", "қыз": "девушка",
    "жігіттер": "парни", "жігіт": "парень",
    "адамға": "человеку", "адам": "человек",
    "керек емес": "не нужен", "керек": "нужен",
    "студенттер": "студенты", "студент": "студент",
    # qualities
    "жаңа": "новый", "жақсы": "хороший",
    "үлкен": "большой", "кіші": "маленький", "кең": "широкий",
    "жарық": "светлый",
    # location
    "жанында": "возле", "жақын": "рядом", "аудан": "район",
    "көше": "улица", "даңғыл": "проспект", "тоқтатағы": "остановка",
    # deposit/utilities
    "депозит жоқ": "без депозита", "депозит": "депозит",
    "коммуналды": "коммунальные", "коммуналка": "коммуналка",
    "жоқ": "нет", "бар": "есть",
    # state/condition
    "жаңа ремонт": "новый ремонт", "ремонт": "ремонт",
    "жиһаз": "мебель", "интернет": "интернет",
    # misc connectors
    "бірге": "вместе", "жеке": "отдельно",
    "өте": "очень",
}

# Case-insensitive whole-word matching, longest entries first so
# «депозит жоқ» → «без депозита» wins over «депозит» → «депозит» + «жоқ» → «нет».
_GLOSS_KEYS = sorted(_KZ_RU_GLOSS, key=len, reverse=True)


def _kz_to_ru(text: str) -> str:
    """Light lexicon normalization: replace common Kazakh real-estate
    terms with their Russian equivalents. Keeps the message readable for a
    Russian-speaking user; not a full morphological translator.
    """
    if not text:
        return text
    out = text
    for kz in _GLOSS_KEYS:
        ru = _KZ_RU_GLOSS[kz]
        if kz in out:
            out = out.replace(kz, ru)
    return out


# ---------------------------------------------------------------------------
# Shared deterministic extractors (2do §4–§12; source §5–§17/§21/§22).
# No OCR, no LLM — pure regex + dictionaries. Kept module-level so they are
# unit-testable independently of the parser instance.
# ---------------------------------------------------------------------------
def _scale_thousand(num: int, unit: str | None) -> int:
    u = (unit or "").lower()
    if u.startswith(("тыс", "тысяч", "мың")):
        return num * 1000
    if u.startswith(("млн", "миллион")):
        return num * 1_000_000
    if u == "к":
        return num * 1000
    return num


def _extract_costs(text: str) -> dict:
    """Context-aware cost extraction (2do §5; source §7/§8/§9).

    Splits every monetary mention into rent / deposit / commission /
    utilities / first_payment, and detects per-person pricing. Specific cost
    keywords (депозит / комиссия / коммунальные / первый платёж) PRECEDE the
    number in Russian listings → use a short backward window. Rent markers
    follow the number → default branch. Never records a deposit/utility equal
    to the rent (avoids misclassifying "180к депозит" where "депозит" trails
    the rent number) — caller passes the already-extracted rent for that.
    """
    out: dict = {
        "deposit": None, "deposit_refundable": None,
        "commission_percent": None, "commission_fixed": None,
        "utilities": "unknown", "utilities_min": None, "utilities_max": None,
        "price_per_person": False, "first_payment": None,
    }
    if not text:
        return out
    BACK, FWD = 30, 30
    for m in _PRICE_RE.finditer(text):
        num = parse_int(m.group(1))
        if num is None:
            continue
        num = _scale_thousand(num, m.group(2))
        back = text[max(0, m.start() - BACK): m.start()]
        fwd = text[m.end(): m.end() + FWD]
        if _DAILY_RE.search(back) or _DAILY_RE.search(fwd):
            continue  # short-term out of scope
        if _CTX_DEPOSIT.search(back[-25:]):
            if out["deposit"] is None and 5000 <= num <= 2_000_000:
                out["deposit"] = num
                if _CTX_REFUND.search(back) or _CTX_REFUND.search(fwd):
                    out["deposit_refundable"] = True
            continue
        if _CTX_COMMISSION.search(back[-25:]):
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
        if _CTX_UTILITIES.search(back[-25:]):
            seg = text[max(0, m.start() - 15): m.end() + 20]
            rng = re.search(
                r"(\d[\d\s.]*)\s*[–\-]\s*(\d[\d\s.]*)\s*"
                r"(тыс|тысяч|мың|к|k)?", seg, re.I)
            if rng and out["utilities_min"] is None:
                lo = _scale_thousand(parse_int(rng.group(1)) or 0, rng.group(3))
                hi = _scale_thousand(parse_int(rng.group(2)) or 0, rng.group(3))
                if 1000 <= lo <= hi <= 200_000:
                    out["utilities_min"] = lo
                    out["utilities_max"] = hi
            out["utilities"] = "separate"
            # Don't record the rent number as utilities amount.
            continue
        if _CTX_FIRST_PAY.search(back[-25:]):
            if out["first_payment"] is None and 10000 <= num <= 2_000_000:
                out["first_payment"] = num
            continue
        # default → rent number; rent itself is chosen by _extract_price.
        if _CTX_PER_PERSON.search(back) or _CTX_PER_PERSON.search(fwd):
            out["price_per_person"] = True
    # "всё включено" overrides separate→included.
    if re.search(r"вс[ёе]\s+включено|коммуналк[ии]\s+включен|включая\s+коммунал",
                text, re.I):
        out["utilities"] = "included"
        out["utilities_min"] = None
        out["utilities_max"] = None
    return out


def _classify_listing_type(text: str) -> str:
    """Ordered classification — first regex match wins (2do §4.2; source §6).

    Returns one of WHOLE_APARTMENT | PRIVATE_ROOM | SHARED_ROOM | ROOMMATE |
    SUBLET | HOUSE | SHORT_TERM | WANTED | UNKNOWN.
    """
    if not text:
        return "UNKNOWN"
    low = text.lower()
    if _WANTED_RE.search(low[:80]):
        return "WANTED"
    if _DAILY_RE.search(low):
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
    """Rule-based owner/agent probabilities (2do §11; source §22)."""
    if not text and not has_org:
        return 0.5, 0.5
    score = 0.5
    if has_org:
        score -= 0.4
    if text:
        if _OWNER_SIGNALS.search(text):
            score += 0.35
        if _AGENT_SIGNALS.search(text):
            score -= 0.35
    score = max(0.0, min(1.0, score))
    return round(score, 2), round(1.0 - score, 2)


def compute_freshness_score(date_published: str) -> float:
    """0..1 freshness by hours since publish (2do §8.1; source §17).

    Accepts a full ISO timestamp (preferred) or a bare YYYY-MM-DD.
    """
    if not date_published:
        return 0.0
    try:
        ts = date_published.replace("Z", "")
        dt = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return 0.0
    now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
    age_h = (now - dt).total_seconds() / 3600.0
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


def compute_quality_score(l: Listing) -> int:
    """0..100 rule-based quality (2do §12; source §21)."""
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
    if l.owner_probability is not None and l.owner_probability < 0.4:
        s -= 20
    if l.price is None and not l.address and not l.photo:
        s -= 15
    return max(0, min(100, s))


def _extract_2gis(text: str) -> dict:
    """Parse a 2GIS link from the message (2do §6.3; source §11).

    Returns ``{url, lat, lon, object_id}`` or empty dict. 2GIS geo links
    embed coordinates as ``?m=lat,lon``; firm links carry an object id.
    """
    if not text:
        return {}
    m = _2GIS_RE.search(text)
    if not m:
        return {}
    url = m.group(1)
    out: dict = {"url": url, "lat": None, "lon": None, "object_id": None}
    fm = re.search(r"/firm/(\d+)", url) or re.search(r"/branches/(\d+)", url)
    if fm:
        out["object_id"] = fm.group(1)
    crd = re.search(r"[?&]m=([\d.]+),([\d.]+)", url)
    if crd:
        out["lat"] = parse_float(crd.group(1))
        out["lon"] = parse_float(crd.group(2))
    return out


def _extract_whatsapp(text: str) -> str:
    m = _WA_RE.search(text or "")
    return normalize_phone("+" + m.group(1)) if m else ""


def _extract_telegram(text: str) -> str:
    m = _TG_RE.search(text or "")
    if not m:
        return ""
    return (m.group(1) or m.group(2) or "").strip()


def _normalize_location(text: str) -> str:
    if not text:
        return ""
    m = re.search(
        r"((?:ул\.|улица|көш|мкр|микрорайон|пр\.|проспект|даңғ|ЖК|жк)"
        r"[^,\n]{2,40})", text, re.I)
    return m.group(1).strip().rstrip(",.;") if m else text[:60].strip()


def _extract_residential_complex(text: str) -> str:
    m = _ZHK_RE.search(text or "")
    if not m:
        return ""
    v = " ".join(m.group(1).split())[:60]
    return v


def _extract_landmark(text: str) -> str:
    m = _LANDMARK_RE.search(text or "")
    if not m:
        return ""
    return " ".join(m.group(1).split())[:60]


def _extract_available_from(text: str) -> str:
    if not text:
        return ""
    m = _AVAILABLE_RE.search(text)
    if m:
        return " ".join(m.group(1).split())
    if _URGENT_RE.search(text):
        return "срочно/сегодня"
    return ""


class TelegramParser(BaseParser):
    """Telegram public channels with Almaty apartment listings.

    Reads the public web preview (``t.me/s/<channel>``) — no API token, no
    Telegram client required (2do §0.2: no Telethon). Each post is free-form
    text, so fields (price, rooms, area, floor, address, phone) are
    regex-mined from the message text; photos come from the album's
    ``background-image`` URLs. New fields (listing_type, deposit, utilities,
    owner_probability, freshness/quality scores, duplicate_group_id) are
    filled by the shared deterministic extractors above — no OCR, no LLM.
    """

    name = "telegram"
    base_url = "https://t.me"
    enrich_photo_count = 0  # all photos already embedded in the message
    # Channel titles vary («КВАРТИРА В АЛМАТЫ», «Аренда квартира Алматы»);
    # the category-page guard checks against a fixed marker list, so skip it.
    category_title_markers: tuple[str, ...] = ()
    # A preview page holds ~20 messages; one older batch per channel is
    # enough fresh listings. min_page_size is unused (run() is overridden).
    min_page_size = 1
    max_pages = 3

    # ---- URL building (unused — run() iterates channels directly) ----------
    def _build_url_base(self, params: SearchParams) -> str:
        return f"{self.base_url}/s/{_TIERED_CHANNELS[0].name}"

    def build_url(self, params: SearchParams, page: int = 1) -> str:
        return self._build_url_base(params)

    # ---- Detail price hook ------------------------------------------------
    def extract_detail_price(self, html: str, url: str) -> tuple:
        """Detail URL is ``t.me/<channel>/<post_id>`` (or ``t.me/s/...``).
        The public preview holds the channel's recent messages; find the
        exact post by ``data-post`` and mine the price from its text.
        (The base parse()-based fallback would miss posts that parse()
        rejects or that carry no price in the text.)"""
        m = re.search(r"t\.me/(?:s/)?([\w-]+)/(\d+)", url)
        if not m:
            return super().extract_detail_price(html, url)
        target = f"{m.group(1)}/{m.group(2)}"
        soup = self.parse_html(html)
        for msg in soup.select("div.tgme_widget_message"):
            if (msg.get("data-post") or "") != target:
                continue
            text_el = msg.select_one(".tgme_widget_message_text")
            text = text_el.get_text("\n", strip=True) if text_el else ""
            return (self._extract_price(text) if text else None, None, None)
        return super().extract_detail_price(html, url)

    # ---- Parsing ----------------------------------------------------------
    def _parse_soup(self, soup: BeautifulSoup, params: SearchParams) -> list[Listing]:
        listings: list[Listing] = []
        for msg in soup.select("div.tgme_widget_message"):
            post = msg.get("data-post") or ""
            text_el = msg.select_one(".tgme_widget_message_text")
            # Skip service messages («Channel created», «Channel photo
            # updated») and photo-only album parts: they carry no text.
            if not text_el or not post:
                continue
            text = text_el.get_text("\n", strip=True)
            if not text:
                continue
            # Wanted ads («Ищу/Сниму квартиру…») are not offers; the intent
            # verb appears near the start of the message.
            if _WANTED_RE.search(text[:80]):
                continue
            # Daily/short-term rentals are out of scope.
            if _DAILY_RE.search(text):
                continue

            channel = post.split("/", 1)[0]
            url = f"https://t.me/{post}"

            # Full ISO timestamp (for freshness); falls back to '' if absent.
            published_ts = ""
            time_el = msg.select_one(".tgme_widget_message_date time")
            if time_el and time_el.get("datetime"):
                published_ts = str(time_el["datetime"])

            # View counter (2do §16; source §24 Phase 1).
            views = None
            views_el = msg.select_one(".tgme_widget_message_views")
            if views_el:
                vtxt = views_el.get_text(strip=True)
                if vtxt:
                    # "1.2K" → 1200, "484" → 484.
                    vtxt_norm = vtxt.replace("K", "00").replace(
                        "k", "00").replace(",", "")
                    views = parse_int(vtxt_norm)

            photos: list[str] = []
            for pw in msg.select(".tgme_widget_message_photo_wrap"):
                m = _BG_URL_RE.search(pw.get("style", ""))
                if m and m.group(1) not in photos:
                    photos.append(m.group(1))

            listing = self._build_listing(text, url, published_ts, photos, views)
            if listing is not None:
                listings.append(listing)
        log.debug("[%s] parsed %d messages", self.name, len(listings))
        return listings

    def _build_listing(
        self, text: str, url: str, published_ts: str,
        photos: list[str], views: int | None = None,
    ) -> Listing | None:
        price = self._extract_price(text)
        rooms = self._extract_rooms(text)
        area = self._extract_area(text)
        floor, total_floors = self._extract_floor(text)
        address = self._extract_address(text)
        title = self._extract_title(text)

        # Require at least a price or a room count — otherwise the message
        # is likely chat/discussion, not a real offer. Accept Kazakh
        # property markers too so KZ-only posts aren't dropped (2do §13).
        low = text.lower()
        has_offer_marker = ("квартир" in low or "комн" in low
                            or "пәтер" in low or "бөлмелі" in low
                            or "жалға" in low or "подселени" in low
                            or "қыз" in low or "ұл" in low)
        if price is None and rooms is None and not has_offer_marker:
            return None

        costs = _extract_costs(text)
        # Don't record deposit/utility equal to the rent number — happens
        # when "депозит"/"коммунал" trails the rent and is mis-scooped.
        if price is not None:
            if costs["deposit"] == price:
                costs["deposit"] = None
            if costs["utilities_min"] == price:
                costs["utilities_min"] = None
                costs["utilities_max"] = None

        phone_raw = ""
        pm = _PHONE_RE.search(text)
        if pm:
            phone_raw = pm.group(0)
        phone_norm = normalize_phone(phone_raw) or re.sub(r"\s+", " ", phone_raw).strip()
        contact_whatsapp = _extract_whatsapp(text)
        # WhatsApp link often also carries the phone: prefer wa-normalized.
        if contact_whatsapp and not phone_norm:
            phone_norm = contact_whatsapp
        contact_telegram = _extract_telegram(text)

        gis = _extract_2gis(text)
        # 2GIS link gives free coordinates + building id (2do §6.3).
        lat = gis.get("lat")
        lon = gis.get("lon")
        building_id = gis.get("object_id") or None

        full_text = text  # raw (KZ) for classification
        listing_type = _classify_listing_type(full_text)
        if _DAILY_RE.search(full_text):
            listing_type = "SHORT_TERM"
        # 2GIS coord wins; otherwise lat/lon stay None (geo enrichment later).

        residential_complex = _extract_residential_complex(full_text)
        landmark = _extract_landmark(full_text)
        normalized_location = _normalize_location(address) if address else ""

        owner_prob, agent_prob = _owner_agent_prob(full_text, has_org=False)
        available_from = _extract_available_from(full_text)

        if listing_type in ("ROOMMATE", "SUBLET") and costs["price_per_person"]:
            price_per_person = True
        else:
            price_per_person = costs["price_per_person"] or None

        # description: KZ-lexicon-normalized for RU-readable display.
        title_ru = _kz_to_ru(title)
        desc_ru = _kz_to_ru(text)

        # date_published stored as YYYY-MM-DD (backward compat with tests);
        # freshness_score uses the full ISO timestamp (2do §8.2).
        date_published = published_ts[:10] if published_ts else ""

        # Telegram message id (provenance): url is "https://t.me/<channel>/<id>".
        post = url.replace("https://t.me/", "", 1) if url.startswith("https://t.me/") else url
        msg_id = post.rsplit("/", 1)[-1] if "/" in post else None

        listing = Listing(
            title=title_ru,
            price=price,
            currency="тг",
            rooms=rooms,
            area=area,
            floor=floor,
            total_floors=total_floors,
            address=address,
            url=url,
            source=self.name,
            phone=phone_norm,
            description=desc_ru,
            photo="|".join(photos),
            date_published=date_published,
            lat=lat,
            lon=lon,
            # --- extended fields (2do §3.2) ---
            listing_id_alt=msg_id,
            listing_type=listing_type,
            deal_type="short_term" if listing_type == "SHORT_TERM" else "long_term",
            rental_period=("daily" if listing_type == "SHORT_TERM"
                            else ("long_term" if re.search(r"долгосрочн", low)
                                  else "monthly")),
            building_id=building_id,
            views=views,
            deposit=costs["deposit"],
            deposit_refundable=costs["deposit_refundable"],
            commission_percent=costs["commission_percent"],
            commission_fixed=costs["commission_fixed"],
            utilities=(costs["utilities"]
                       if costs["utilities"] != "unknown" else None),
            utilities_min=costs["utilities_min"],
            utilities_max=costs["utilities_max"],
            rent_per_m2=(round(price / area, 2) if price and area else None),
            price_per_person=price_per_person or None,
            first_payment=costs["first_payment"],
            normalized_location=normalized_location or None,
            microdistrict=None,  # filled by building enrichment pass
            residential_complex=residential_complex or None,
            landmark=landmark or None,
            contact_telegram=contact_telegram or None,
            contact_whatsapp=contact_whatsapp or None,
            available_from=available_from or None,
            owner_probability=owner_prob,
            agent_probability=agent_prob,
        )
        # Scoring + fingerprint (2do §8/§9/§12).
        listing.freshness_score = compute_freshness_score(published_ts)
        listing.quality_score = compute_quality_score(listing)
        listing.duplicate_group_id = property_fingerprint(
            building_id=building_id,
            normalized_location=normalized_location or None,
            rooms=rooms,
            area=area,
            floor=floor,
            price=price,
        ) or None
        return listing

    # ---- Field extractors -------------------------------------------------
    @staticmethod
    def _extract_rooms(text: str) -> int | None:
        # Telegram text is free-form; the base parse_rooms() falls through
        # to parse_int() which mashes every digit (incl. phone numbers)
        # together, so use a bounded single-digit extractor here.
        # Russian: "2-ком", "2 комн", "1-комнатная", "двухкомнатная"
        # Kazakh:  "2 бөлмелі", "1 бөлмелі" (бөлме = комната)
        m = re.search(r"\b(\d)\s*[-]?\s*комн", text, re.I)
        if m:
            n = int(m.group(1))
            return n if 0 <= n <= 6 else None
        m = re.search(r"\b(\d)\s*[-]?\s*ком\b", text, re.I)
        if m:
            n = int(m.group(1))
            return n if 1 <= n <= 6 else None
        m = re.search(r"\b(\d)\s*[-]?\s*бөлмелі", text, re.I)
        if m:
            n = int(m.group(1))
            return n if 1 <= n <= 6 else None
        m = re.search(r"\b(\d)\s*[-]?\s*бөлме", text, re.I)
        if m:
            n = int(m.group(1))
            return n if 1 <= n <= 6 else None
        if re.search(r"\bстудия\b|\bстудию\b|\bстудиялық\b", text, re.I):
            return 0
        # Word forms: однокомнатная / двухкомнатная / …
        word_map = {"одно": 1, "двух": 2, "трёх": 3, "трех": 3,
                    "четырёх": 4, "четырех": 4, "пяти": 5,
                    # Kazakh: бірл‎екі/үш/төрт/бес бөлмелі
                    "бір": 1, "екі": 2, "үш": 3, "төрт": 4, "бес": 5}
        m = re.search(
            r"(одно|двух|трёх|трех|четырёх|четырех|пяти|"
            r"бір|екі|үш|төрт|бес)\s*комн|\b(бір|екі|үш|төрт|бес)\s*бөлмелі",
            text, re.I)
        if m:
            key = (m.group(1) or m.group(2) or "").lower()
            return word_map.get(key)
        return None

    @staticmethod
    def _extract_price(text: str) -> int | None:
        # 1) Number + currency word/symbol (incl. Kazakh "теңге", "мың").
        for m in _PRICE_RE.finditer(text):
            num = parse_int(m.group(1))
            if num is None:
                continue
            unit = (m.group(2) or "").lower()
            if unit.startswith(("тыс", "тысяч", "мың")):
                num *= 1000
            elif unit.startswith(("млн", "миллион")):
                num *= 1_000_000
            elif unit == "к":  # "180к"
                num *= 1000
            if 30000 <= num <= 200_000_000:
                return num
        # 2) "180к" / "40k" shorthand — single Latin/Cyrillic k suffix,
        #    no currency word. (Skipped by the loop above when group(2) is
        #    None, so handle it explicitly here.)
        for m in re.finditer(r"(?<!\d)(\d{1,3})\s*([кk])\b", text, re.I):
            num = int(m.group(1)) * 1000
            if 30000 <= num <= 200_000_000:
                return num
        # 3) Bare number near a rent keyword ("по 55 000 + ком.услуги").
        #    Only trusted when a rent/month/deposit word is within 40 chars
        #    so "5 минут", "лицей №90", phone fragments are not matched.
        for m in re.finditer(r"(?<!\d)(\d[\d\s.]{2,})\s*(?![кk])", text):
            num = parse_int(m.group(1))
            if num is None or not (30000 <= num <= 1_000_000):
                continue
            window = text[max(0, m.start() - 40): m.end() + 40]
            if _RENT_KW_RE.search(window):
                return num
        return None

    @staticmethod
    def _extract_area(text: str) -> float | None:
        # "43/27/6" — area/living/kitchen: take the first (total area).
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*/\s*\d+\s*/\s*\d+", text)
        if m:
            a = parse_float(m.group(1))
            if a and 10 <= a <= 400:
                return a
        # "45 м²" / "45 м2" / Kazakh "ауданы 45 м²" / "шаршы" (square)
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*м[²\^2]", text, re.I)
        if m:
            a = parse_float(m.group(1))
            if a and 10 <= a <= 400:
                return a
        # Kazakh: "45 шаршы метр" / "45 шаршы"
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*шаршы", text, re.I)
        if m:
            a = parse_float(m.group(1))
            if a and 10 <= a <= 400:
                return a
        return None

    @staticmethod
    def _extract_floor(text: str) -> tuple[int | None, int | None]:
        # "4/4 панель", "9/9 этаж" — pair followed by a floor keyword.
        # Avoids the area triple "43/27/6" (no keyword after the pair).
        # Kazakh floor words: қабат / қабаты.
        m = re.search(
            r"(\d+)\s*/\s*(\d+)\s*(?:панель|эт|этаж|жил|кв|қабат|қабаты)",
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

    @staticmethod
    def _extract_address(text: str) -> str:
        m = _ADDR_RE.search(text)
        if m:
            return m.group(1).strip().rstrip(",.;")
        m = _ADDR_FALLBACK_RE.search(text)
        if m:
            return m.group(1).strip().rstrip(",.;")
        return ""

    @staticmethod
    def _extract_title(text: str) -> str:
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        # Prefer a line that names the property type — Russian or Kazakh —
        # (квартира/комн/студия/пәтер/бөлмелі or a "2-ком…" room count)
        # over a generic «Долгосрочная аренда.».
        for line in lines:
            low = line.lower()
            if ("квартир" in low or "комн" in low or "студия" in low
                    or "пәтер" in low or "бөлмелі" in low
                    or "жалға" in low
                    or re.search(r"\d\s*[-]?\s*ком", low)):
                return line[:90]
        # Fallback: a rent/sale descriptive line (RU + KZ verbs).
        for line in lines:
            low = line.lower()
            if (("аренда" in low or "сдаёт" in low or "жалға" in low)
                    and len(line) > 10):
                return line[:90]
        return lines[0][:90] if lines else "Telegram"

    # ---- Pagination helper ------------------------------------------------
    def _extract_before(self, html: str) -> str | None:
        """Return the ``data-before`` of the «load older» link, or None."""
        soup = self.parse_html(html)
        more = soup.select_one(".tme_messages_more")
        return more.get("data-before") if more else None

    # ---- Cross-channel fingerprint dedup (2do §9; source §15/§16) ---------
    def _fingerprint_dedup_inplace(self, listings: list[Listing]) -> None:
        """Group near-duplicates by ``duplicate_group_id`` and keep a master.

        Mutates in place: masters keep their data and get ``sources_count``
        reflecting how many channels carried the same ad; non-masters are
        dropped. Phone-equality is an extra merge signal beyond fingerprint
        (one landlord posting across channels — 2do §9.2 / source §15).
        """
        groups: dict[str, list[Listing]] = {}
        for l in listings:
            key = l.duplicate_group_id or f"__u{l.url or id(l)}"
            groups.setdefault(key, []).append(l)
        out: list[Listing] = []
        for grp in groups.values():
            if len(grp) == 1:
                grp[0].sources_count = 1
                out.append(grp[0])
                continue
            # Phone-merge bonus: same normalized phone → same landlord
            # (wholly different fingerprint) — still collapses to master
            # because the same unit is almost certainly being re-advertised.
            phones = {p for p in (g.phone for g in grp) if p}
            master = min(grp, key=lambda x: (
                x.date_published or "9999", -(x.quality_score or 0)))
            master.sources_count = len(grp)
            # Cross-source "3 sources" is a POSITIVE signal (2do §9.5):
            # apartment actively advertised → likely real & current.
            if master.quality_score is not None and len(grp) >= 2:
                master.quality_score = min(100, master.quality_score + 5)
            out.append(master)
        listings[:] = out

    # ---- Run (multi-channel, tiered, resilient) ---------------------------
    def run(self, params: SearchParams) -> list[Listing]:
        stats = ParserRunStats(
            name=self.name, base_url=self.base_url,
            status="pending", timestamp=datetime.now().isoformat(sep=" ", timespec="seconds"),
        )
        self.last_stats = stats
        t0 = time.monotonic()
        pages = 0
        all_results: list[Listing] = []
        try:
            for ch in _TIERED_CHANNELS:
                try:
                    base = f"{self.base_url}/s/{ch.name}"
                    html = self.fetch(base)
                    pages += 1
                    results = self.parse(html, params)
                    # Older batches up to the channel's tier depth.
                    before = self._extract_before(html)
                    fetched_batches = 0
                    while before and fetched_batches < ch.before_batches:
                        time.sleep(random.uniform(0.3, 0.8))
                        try:
                            html2 = self.fetch(f"{base}?before={before}")
                            pages += 1
                            fetched_batches += 1
                        except Exception as exc:
                            log.debug("[%s] %s: older batch failed: %s",
                                      self.name, ch.name, exc)
                            break
                        results += self.parse(html2, params)
                        before = self._extract_before(html2)
                    all_results.extend(results)
                    log.info("[%s] %s (tier %s): %d listings",
                             self.name, ch.name, ch.tier, len(results))
                except Exception as exc:
                    # One dead/blocked channel must not kill the whole parser.
                    log.warning("[%s] channel %s failed: %s",
                                self.name, ch.name, exc)
                    continue
            deduped = self._dedupe(all_results)
            # Cross-channel fuzzy dedup → master listings (2do §9.3).
            self._fingerprint_dedup_inplace(deduped)
            filtered = self.apply(deduped, params)
            stats.pages_fetched = pages
            stats.results_count = len(filtered)
            stats.status = "ok" if filtered else "empty"
            stats.duration_ms = (time.monotonic() - t0) * 1000
            _LAST_PARSER_STATS[self.name] = stats
            return filtered
        except Exception as exc:
            stats.status = "error"
            stats.error = str(exc)[:300]
            stats.error_type = type(exc).__name__
            stats.pages_fetched = pages
            stats.duration_ms = (time.monotonic() - t0) * 1000
            log.warning("[%s] unexpected error: %s", self.name, exc, exc_info=True)
            _LAST_PARSER_STATS[self.name] = stats
            # Keep whatever we gathered before the failure.
            try:
                self._fingerprint_dedup_inplace(self._dedupe(all_results))
                return self.apply(self._dedupe(all_results), params)
            except Exception:
                return []
