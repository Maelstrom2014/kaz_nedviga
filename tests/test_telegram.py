"""Tests for the Telegram channel parser."""
import pytest
from unittest.mock import patch

from parsers.models import SearchParams
from parsers.telegram import TelegramParser, _CHANNELS

from .loaders import load_fixture


def _parse():
    html = load_fixture("telegram")
    return TelegramParser().parse(html, SearchParams())


def test_parses_two_listings_skips_noise():
    # fixture has: service msg, listing1, wanted ad, daily rental, listing2
    results = _parse()
    assert len(results) == 2, f"expected 2, got {len(results)}"


def test_listing_title_and_url():
    results = _parse()
    for r in results:
        assert r.title, "empty title"
        assert r.url.startswith("https://t.me/"), r.url
        assert r.source == "telegram"


def test_price_extracted_both_formats():
    results = _parse()
    by_title = {r.title: r for r in results}
    # "360 000 тг" — direct
    apt1 = next(r for r in results if "2-ком" in r.title)
    assert apt1.price == 360000, apt1.price
    # "270 тыс тг" — thousand multiplier
    apt2 = next(r for r in results if "1-комнатная" in r.title)
    assert apt2.price == 270000, apt2.price


def test_rooms_parsed():
    results = _parse()
    apt1 = next(r for r in results if "2-ком" in r.title)
    assert apt1.rooms == 2
    apt2 = next(r for r in results if "1-комнатная" in r.title)
    assert apt2.rooms == 1


def test_rooms_extractor_bounded_and_safe():
    # Regression: base parse_rooms() mashed all digits (phone + "Лицей №90")
    # into rooms=9077064001412. The telegram extractor must stay small.
    extract = TelegramParser._extract_rooms
    assert extract("2-ком.квартира в центре") == 2
    assert extract("1-комнатная квартира") == 1
    assert extract("Светлая квартира. Лицей №90. +77064001412 Ольга") is None
    assert extract("Сдаётся студия, 38 м²") == 0
    assert extract("Двухкомнатная квартира") == 2
    # A phone-number digit run before "комфортного" must not be read as rooms.
    assert extract("цена 77064001412 для комфортного проживания") is None


def test_area_parsed_both_formats():
    results = _parse()
    apt1 = next(r for r in results if "2-ком" in r.title)
    # "43/27/6" → total area 43
    assert apt1.area == 43.0, apt1.area
    apt2 = next(r for r in results if "1-комнатная" in r.title)
    # "38 м²"
    assert apt2.area == 38.0, apt2.area


def test_floor_parsed_and_not_confused_with_area():
    results = _parse()
    apt1 = next(r for r in results if "2-ком" in r.title)
    # "4/4 панель" — floor 4 of 4, NOT the area triple 43/27/6
    assert apt1.floor == 4
    assert apt1.total_floors == 4
    apt2 = next(r for r in results if "1-комнатная" in r.title)
    # "7/9 этаж"
    assert apt2.floor == 7
    assert apt2.total_floors == 9


def test_address_extracted():
    results = _parse()
    apt1 = next(r for r in results if "2-ком" in r.title)
    assert "Ауэзова" in apt1.address
    apt2 = next(r for r in results if "1-комнатная" in r.title)
    assert "Сайран" in apt2.address


def test_phone_extracted():
    results = _parse()
    apt1 = next(r for r in results if "2-ком" in r.title)
    # Phone is normalized to +7XXXXXXXXXX (2do §7.1; source §14).
    assert apt1.phone == "+77052110891", apt1.phone
    apt2 = next(r for r in results if "1-комнатная" in r.title)
    assert apt2.phone == "+77059008676", apt2.phone


def test_date_extracted():
    results = _parse()
    apt1 = next(r for r in results if "2-ком" in r.title)
    assert apt1.date_published == "2025-11-11"


def test_photos_collected_from_album():
    results = _parse()
    apt1 = next(r for r in results if "2-ком" in r.title)
    photos = apt1.photo.split("|")
    assert len(photos) == 3, photos
    assert all(p.startswith("https://cdn4.telesco.pe/file/listing1") for p in photos)


def test_skips_wanted_and_daily():
    results = _parse()
    titles = " ".join(r.title for r in results)
    assert "сниму" not in titles.lower()
    assert "посуточно" not in " ".join(r.description for r in results).lower()


def test_build_url_returns_telegram_preview():
    url = TelegramParser().build_url(SearchParams())
    assert url.startswith("https://t.me/s/")


def test_channels_are_public_preview_names():
    for ch in _CHANNELS:
        assert "/" not in ch and ch.isidentifier() or ch.replace("_", "").isalnum()


def test_run_aggregates_channels_and_is_resilient():
    # One channel fetches the fixture, the rest fail → parser still returns
    # the fixture's listings and never raises.
    parser = TelegramParser()

    def fake_fetch(url):
        if _CHANNELS[0] in url and "before=" not in url:
            return load_fixture("telegram")
        raise ConnectionError("blocked")

    with patch.object(parser, "fetch", side_effect=fake_fetch), \
         patch.object(parser, "_extract_before", return_value=None):
        results = parser.run(SearchParams())
    assert len(results) == 2
    assert all(r.source == "telegram" for r in results)


def test_run_partial_failure_keeps_results():
    parser = TelegramParser()

    def fake_fetch(url):
        if _CHANNELS[0] in url and "before=" not in url:
            return load_fixture("telegram")
        if _CHANNELS[1] in url and "before=" not in url:
            return load_fixture("telegram")
        raise ConnectionError("blocked")

    with patch.object(parser, "fetch", side_effect=fake_fetch), \
         patch.object(parser, "_extract_before", return_value=None):
        results = parser.run(SearchParams())
    # 2 channels × 2 listings = 4 (deduped by URL — different data-post? same
    # fixture → same URLs → dedup keeps 2)
    assert len(results) == 2


# ---- Price extraction (shorthand, Kazakh currency, bare-number) ----------
def test_price_k_suffix_thousand():
    extract = TelegramParser._extract_price
    assert extract("В месяц: 180к депозит") == 180000
    assert extract("Оплата 50к+ком.услуга") == 50000
    assert extract("Цена - 40к Коммуналка") == 40000
    assert extract("latin k: 270k per month") == 270000


def test_price_kazakh_currency_words():
    extract = TelegramParser._extract_price
    # "теңге" = Kazakh for "тенге"
    assert extract("Аренда 200 000 теңге айына") == 200000
    # "мың" = Kazakh for "тысяча"
    assert extract("Бағасы 200 мың теңге") == 200000


def test_price_dot_separator():
    extract = TelegramParser._extract_price
    assert extract("оплата 66.000тг + коммуналка") == 66000
    assert extract("депозит 50.000 тенге") == 50000


def test_price_bare_number_near_rent_keyword():
    extract = TelegramParser._extract_price
    # bare number trusted only when a rent/month/oplata keyword is nearby
    assert extract("С каждого по 55 000 + ком.услуги") == 55000
    assert extract("Стоимость: 120000 в месяц") == 120000
    # NOT trusted without a rent keyword (avoids "5 минут", phone fragments)
    assert extract("5 минут до метро. Лицей №90") is None
    assert extract("телефон 77059008676 Ольга") is None


def test_price_skips_implausibly_low():
    extract = TelegramParser._extract_price
    # 250 тг is below the 30000 floor — not a real long-term rent price
    assert extract("Арендная плата 250 теңге") is None
    assert extract("плата 5000 тг") is None


# ---- Kazakh room/area/floor word forms -----------------------------------
def test_rooms_kazakh_word_forms():
    extract = TelegramParser._extract_rooms
    assert extract("2 бөлмелі кв-да") == 2
    assert extract("1 бөлмелі пәтер") == 1
    assert extract("3 бөлме жалға") == 3
    assert extract("екі бөлмелі пәтер") == 2


def test_area_kazakh_sharshy():
    extract = TelegramParser._extract_area
    assert extract("ауданы 45 м²") == 45.0
    assert extract("45 шаршы метр") == 45.0
    assert extract("площадь 38 м²") == 38.0


def test_floor_kazakh_qabat():
    extract = TelegramParser._extract_floor
    # "қабат" = Kazakh for "этаж"
    assert extract("5/9 қабат") == (5, 9)
    assert extract("4 қабаты") == (4, None)
    assert extract("9/9 этаж") == (9, 9)


# ---- Kazakh → Russian translation glossary --------------------------------
def test_kz_to_ru_translates_property_terms():
    from parsers.telegram import _kz_to_ru
    assert "сдаётся" in _kz_to_ru("жалға беріледі")
    assert "комнатная" in _kz_to_ru("2 бөлмелі")
    assert "квартира" in _kz_to_ru("пәтер")


def test_kz_to_ru_multiword_deposit():
    from parsers.telegram import _kz_to_ru
    # longest entry first: «депозит жоқ» → «без депозита», not split
    out = _kz_to_ru("депозит жоқ, всё новое")
    assert "без депозита" in out
    assert " нет" not in out  # «жоқ» consumed by the multiword entry


def test_kz_to_ru_preserves_russian_and_numbers():
    from parsers.telegram import _kz_to_ru
    out = _kz_to_ru("2 комнатная квартира, 180к")
    assert "2" in out and "комнатная" in out


def test_kz_to_ru_empty_and_none_safe():
    from parsers.telegram import _kz_to_ru
    assert _kz_to_ru("") == ""
    assert _kz_to_ru("чисто русский текст") == "чисто русский текст"


def test_listing_translates_kazakh_title_and_description():
    # A mixed RU/KZ message should reach the user with KZ terms normalized.
    parser = TelegramParser()
    text = ("2 бөлмелі кв жалға беріледі. Қыздарға. "
            "Оплата 180к, депозит жоқ. Таза, жаңа ремонт.")
    listing = parser._build_listing(
        text, "https://t.me/x/1", "2025-11-11", [])
    assert listing is not None
    assert "сдаётся" in listing.title or "комнатная" in listing.title
    assert "без депозита" in listing.description
    assert "комнатная" in listing.description


# ===========================================================================
# Tests for the task_telega_chatgpt.md recommendations (2do doc).
# All deterministic — no OCR, no LLM.
# ===========================================================================
from parsers.telegram import (
    _classify_listing_type,
    _extract_costs,
    _extract_2gis,
    _extract_available_from,
    _extract_landmark,
    _extract_residential_complex,
    _extract_whatsapp,
    _extract_telegram,
    compute_freshness_score,
    compute_quality_score,
    _owner_agent_prob,
    _TIERED_CHANNELS,
    _KZ_RU_GLOSS,
    _GLOSS_KEYS,
)

from parsers.base import normalize_phone, property_fingerprint


# ---- Tiered channels (2do §1.4 / §15.2; source §18) -----------------------
def test_channels_are_tiered():
    assert len(_TIERED_CHANNELS) >= 8
    tiers = {c.tier for c in _TIERED_CHANNELS}
    assert {"A", "B", "C"} <= tiers
    # Tier A channels have deeper pagination.
    for ch in _TIERED_CHANNELS:
        if ch.tier == "A":
            assert ch.before_batches >= 1


def test_channels_flat_tuple_backward_compat():
    # Existing tests index _CHANNELS[i]; structure must expose the flat list.
    assert _CHANNELS[0] == _TIERED_CHANNELS[0].name
    assert len(_CHANNELS) == len(_TIERED_CHANNELS)


# ---- listing_type classification (2do §4.2; source §6) --------------------
@pytest.mark.parametrize("text,expected", [
    ("Ищу 2-комн квартиру", "WANTED"),
    ("Квартира посуточно 15000", "SHORT_TERM"),
    ("Ищем девушку на подселение", "ROOMMATE"),
    ("Сдаю комнату в квартире", "SUBLET"),
    ("возьму девушку на подселение", "ROOMMATE"),
    ("подселениеге қыз керек", "ROOMMATE"),
    ("Сдаётся 2-комнатная квартира", "WHOLE_APARTMENT"),
    ("Сдаётся частный дом", "HOUSE"),
    ("комната в общежитии", "PRIVATE_ROOM"),
    ("какой-то текст", "UNKNOWN"),
])
def test_classify_listing_type(text, expected):
    assert _classify_listing_type(text) == expected


def test_listing_type_set_on_parsed_listings():
    results = _parse()
    apt1 = next(r for r in results if "2-ком" in r.title)
    assert apt1.listing_type == "WHOLE_APARTMENT"
    assert apt1.deal_type == "long_term"


# ---- Context-aware costs (2do §5; source §7/§8/§9) -----------------------
def test_costs_deposit_commission_utilities_split():
    text = ("Сдаётся 2-комн. 250 000 тг/мес. "
            "Депозит 50 000 возвратный. Комиссия 30%. "
            "Коммунальные 5–6 тыс отдельно.")
    c = _extract_costs(text)
    assert c["deposit"] == 50000
    assert c["deposit_refundable"] is True
    assert c["commission_percent"] == 30
    assert c["utilities"] == "separate"
    assert c["utilities_min"] == 5000
    assert c["utilities_max"] == 6000


def test_costs_first_payment_vs_rent():
    c = _extract_costs("первый платёж 120 000, дальше 80 000 тг/мес")
    assert c["first_payment"] == 120000


def test_costs_included_overrides():
    c = _extract_costs("аренда 200000, всё включено")
    assert c["utilities"] == "included"


def test_costs_per_person():
    c = _extract_costs("Ищем девушку на подселение. 70000 за человека")
    assert c["price_per_person"] is True


def test_costs_deposit_not_equal_rent_in_listing():
    # "180к депозит" — the rent is 180000 (via _extract_price); deposit
    # trailing must not be recorded as equal to rent (2do §5.3).
    parser = TelegramParser()
    listing = parser._build_listing(
        "аренда 180к депозит", "https://t.me/x/1", "2025-11-11T10:00:00+00:00", [])
    assert listing.price == 180000
    assert listing.deposit is None  # equal to rent → suppressed


# ---- Phone normalization (2do §7.1; source §14) --------------------------
@pytest.mark.parametrize("raw,expected", [
    ("87071234567", "+77071234567"),
    ("+7 707 123 45 67", "+77071234567"),
    ("8 (707) 123-45-67", "+77071234567"),
    ("wa.me/77071234567", "+77071234567"),
    ("нет телефона", ""),
])
def test_normalize_phone(raw, expected):
    assert normalize_phone(raw) == expected


def test_whatsapp_extracted_from_text():
    assert _extract_whatsapp("звоните wa.me/77071234567") == "+77071234567"
    assert _extract_whatsapp("нет whatsapp") == ""


def test_telegram_handle_extracted():
    assert _extract_telegram("пишите @username") == "username"
    assert _extract_telegram("нет handle") == ""


# ---- 2GIS link parsing (2do §6.3; source §11) ----------------------------
def test_extract_2gis_geo_link():
    gis = _extract_2gis("квартира https://2gis.kz/almaty/geo/123?m=43.2,76.9")
    assert gis["url"].startswith("https://2gis.kz/")
    assert gis["lat"] == 43.2
    assert gis["lon"] == 76.9


def test_extract_2gis_firm_link():
    gis = _extract_2gis("офис https://2gis.kz/almaty/firm/98765")
    assert gis["object_id"] == "98765"


def test_extract_2gis_none():
    assert _extract_2gis("нет ссылки") == {}


# ---- Location / ЖК / landmark (2do §6; source §10) -----------------------
def test_extract_residential_complex():
    assert _extract_residential_complex("квартира в ЖК Алма Сити 4") == "Алма Сити 4"


def test_extract_landmark():
    lm = _extract_landmark("возле Сайрана")
    assert "Сайран" in lm


def test_extract_available_from():
    assert _extract_available_from("заселение с 1 сентября") == "1 сентября"
    assert _extract_available_from("срочно") == "срочно/сегодня"
    assert _extract_available_from("нет даты") == ""


# ---- Owner/agent probability (2do §11/§22) -------------------------------
def test_owner_agent_owner_signals():
    owner, agent = _owner_agent_prob("я собственник, без посредников")
    assert owner > 0.7 and agent < 0.3


def test_owner_agent_agent_signals():
    owner, agent = _owner_agent_prob("комиссия 30% риелтор")
    assert owner < 0.4 and agent > 0.6


# ---- Freshness + quality scores (2do §8/§12) -----------------------------
def test_freshness_recent():
    from datetime import datetime
    assert compute_freshness_score(datetime.now().isoformat()) == 1.0
    assert compute_freshness_score("2020-01-01") == 0.10
    assert compute_freshness_score("") == 0.0


def test_quality_score_computed_on_listing():
    results = _parse()
    for r in results:
        assert r.quality_score is not None and 0 < r.quality_score <= 100
        assert r.freshness_score is not None and 0.0 < r.freshness_score <= 1.0


# ---- Views capture (2do §16; source §24 Phase 1) --------------------------
def test_views_captured():
    results = _parse()
    apt1 = next(r for r in results if "2-ком" in r.title)
    assert apt1.views == 484
    apt2 = next(r for r in results if "1-комнатная" in r.title)
    assert apt2.views == 798


# ---- Cross-channel fingerprint dedup with master (2do §9; source §15/§16) -
def test_fingerprint_dedup_merges_same_listing_across_channels():
    parser = TelegramParser()
    # Two copies of the same ad (same phone/price/rooms/area) from different
    # channels → one master with sources_count == 2 (source §16).
    l1 = parser._build_listing(
        "Сдаётся 2-комн. 250000 тг. +7 707 123 45 67. ЖК Алма Сити.",
        "https://t.me/chA/100", "2025-11-11T09:00:00+00:00", [])
    l2 = parser._build_listing(
        "Сдам 2-комн квартиру. 250000 тг. 87071234567. ЖК Алма Сити.",
        "https://t.me/chB/200", "2025-11-11T13:00:00+00:00", [])
    listings = [l1, l2]
    parser._fingerprint_dedup_inplace(listings)
    # Same fingerprint (normalized phone, same price/rooms) → 1 master.
    assert len(listings) == 1
    assert listings[0].sources_count == 2


def test_fingerprint_dedup_keeps_distinct():
    parser = TelegramParser()
    l1 = parser._build_listing(
        "Сдаётся 1-комн. 150000 тг. мкр Аксай.",
        "https://t.me/chA/100", "2025-11-11T09:00:00+00:00", [])
    l2 = parser._build_listing(
        "Сдаётся 3-комн. 350000 тг. ЖК Мегаполис.",
        "https://t.me/chB/200", "2025-11-11T13:00:00+00:00", [])
    listings = [l1, l2]
    parser._fingerprint_dedup_inplace(listings)
    assert len(listings) == 2


def test_run_cross_channel_master_count():
    # test_run_partial_failure_keeps_results contract: same fixture from
    # two channels → 2 distinct listings (not 4), each master may have
    # sources_count ≥ 1.
    parser = TelegramParser()

    def fake_fetch(url):
        if _CHANNELS[0] in url and "before=" not in url:
            return load_fixture("telegram")
        if _CHANNELS[1] in url and "before=" not in url:
            return load_fixture("telegram")
        raise ConnectionError("blocked")

    with patch.object(parser, "fetch", side_effect=fake_fetch), \
         patch.object(parser, "_extract_before", return_value=None):
        results = parser.run(SearchParams())
    assert len(results) == 2  # URL dedup + fuzzy dedup → 2 masters


# ---- listing_id_alt / provenance (2do §3.2) --------------------------------
def test_listing_id_alt_extracted():
    results = _parse()
    apt1 = next(r for r in results if "2-ком" in r.title)
    # URL = t.me/kvartiry_almaty/368 → msg id 368.
    assert apt1.listing_id_alt == "368"


# ---- rent_per_m2 computed (2do §3.2) ---------------------------------------
def test_rent_per_m2():
    results = _parse()
    apt2 = next(r for r in results if "1-комнатная" in r.title)
    # 270000 / 38 m² ≈ 7105.26
    assert apt2.rent_per_m2 is not None
    assert 7000 < apt2.rent_per_m2 < 7500


# ---- duplicate_group_id set (2do §9) ---------------------------------------
def test_duplicate_group_id_set():
    results = _parse()
    for r in results:
        assert r.duplicate_group_id is not None


# ---- extended KZ glossary (2do §14) ----------------------------------------
def test_kz_glossary_has_multword_entries():
    # Multiword entries must be present and longest-first sorted.
    assert "жалға беремін" in _KZ_RU_GLOSS
    assert _GLOSS_KEYS[0] == max(_GLOSS_KEYS, key=len)
