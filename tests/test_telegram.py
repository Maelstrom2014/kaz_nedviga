"""Tests for the Telegram channel parser."""
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
    assert "7705" in apt1.phone.replace(" ", "")
    apt2 = next(r for r in results if "1-комнатная" in r.title)
    assert "87059008676" in apt2.phone.replace(" ", "")


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
