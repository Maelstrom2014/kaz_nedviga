from __future__ import annotations

import random
import re
import time
from datetime import datetime

from bs4 import BeautifulSoup

from .base import (
    BaseParser,
    ParserRunStats,
    _LAST_PARSER_STATS,
    log,
    parse_float,
    parse_int,
)
from .models import Listing, SearchParams

# Public Telegram channels (t.me/s/<name>) carrying Almaty real-estate
# offers. Read via the public web preview — no API token, no client.
#   kvartiry_almaty        — «КВАРТИРА В АЛМАТЫ», rent & sale, active
#   arenda_kvartira_almaty — «Аренда квартира Алматы»
#   kvartira_almaty_arenda — «Квартира в Алматы»
#   arenda_kvartir_almaty  — «Аренда Алматы Подселение квартир»
_CHANNELS: tuple[str, ...] = (
    "kvartiry_almaty",
    "arenda_kvartira_almaty",
    "kvartira_almaty_arenda",
    "arenda_kvartir_almaty",
)

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

# ---- Kazakh → Russian real-estate glossary --------------------------------
# Telegram channels mix Kazakh and Russian freely («2 бөлмелі кв-да
# Изолированная комната жалға беріледі»). Without an MT API we normalize the
# common real-estate vocabulary to Russian so the title/description are
# readable for a Russian-speaking user. Applied to title + description only;
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
    # rent/sale verbs
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
    # requirements/people
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


class TelegramParser(BaseParser):
    """Telegram public channels with Almaty apartment listings.

    Reads the public web preview (``t.me/s/<channel>``) — no API token, no
    Telegram client required. Each post is free-form text, so fields
    (price, rooms, area, floor, address, phone) are regex-mined from the
    message text; photos come from the album's ``background-image`` URLs.
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
        return f"{self.base_url}/s/{_CHANNELS[0]}"

    def build_url(self, params: SearchParams, page: int = 1) -> str:
        return self._build_url_base(params)

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

            date_published = ""
            time_el = msg.select_one(".tgme_widget_message_date time")
            if time_el and time_el.get("datetime"):
                date_published = str(time_el["datetime"])[:10]  # YYYY-MM-DD

            photos: list[str] = []
            for pw in msg.select(".tgme_widget_message_photo_wrap"):
                m = _BG_URL_RE.search(pw.get("style", ""))
                if m and m.group(1) not in photos:
                    photos.append(m.group(1))

            listing = self._build_listing(text, url, date_published, photos)
            if listing is not None:
                listings.append(listing)
        log.debug("[%s] parsed %d messages", self.name, len(listings))
        return listings

    def _build_listing(
        self, text: str, url: str, date_published: str, photos: list[str]
    ) -> Listing | None:
        price = self._extract_price(text)
        rooms = self._extract_rooms(text)
        area = self._extract_area(text)
        floor, total_floors = self._extract_floor(text)
        address = self._extract_address(text)
        phone = _PHONE_RE.search(text)
        title = self._extract_title(text)

        # Require at least a price or a room count — otherwise the message
        # is likely chat/discussion, not a real offer. Accept Kazakh
        # property markers too so KZ-only posts aren't dropped.
        low = text.lower()
        has_offer_marker = ("квартир" in low or "комн" in low
                            or "пәтер" in low or "бөлмелі" in low
                            or "жалға" in low)
        if price is None and rooms is None and not has_offer_marker:
            return None

        # Normalize Kazakh real-estate vocabulary to Russian so the
        # title/description are readable for a RU-speaking user. Applied
        # for display only; structured fields are mined from the raw text.
        title_ru = _kz_to_ru(title)
        desc_ru = _kz_to_ru(text)

        return Listing(
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
            phone=re.sub(r"\s+", " ", phone.group(0)).strip() if phone else "",
            description=desc_ru,
            photo="|".join(photos),
            date_published=date_published,
        )

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

    # ---- Run (multi-channel, per-channel resilient) -----------------------
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
            for ch in _CHANNELS:
                try:
                    base = f"{self.base_url}/s/{ch}"
                    html = self.fetch(base)
                    pages += 1
                    results = self.parse(html, params)
                    # One older batch per channel for more fresh listings.
                    before = self._extract_before(html)
                    if before:
                        time.sleep(random.uniform(0.3, 0.8))
                        html2 = self.fetch(f"{base}?before={before}")
                        pages += 1
                        results += self.parse(html2, params)
                    all_results.extend(results)
                    log.info("[%s] %s: %d listings", self.name, ch, len(results))
                except Exception as exc:
                    # One dead/blocked channel must not kill the whole parser.
                    log.warning("[%s] channel %s failed: %s", self.name, ch, exc)
                    continue
            deduped = self._dedupe(all_results)
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
                return self.apply(self._dedupe(all_results), params)
            except Exception:
                return []
