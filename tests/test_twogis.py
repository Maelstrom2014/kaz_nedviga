"""Tests for the 2GIS parser (task_2gis_2do.md).

Covers JSON-XHR parsing, context-aware cost extraction, listing-type
classification, phone normalization, fingerprint dedup, graceful
``not_configured`` degradation, and building/organization storage.
No OCR, no LLM — all paths deterministic.
"""
import json

import pytest

from parsers.base import normalize_phone, property_fingerprint
from parsers.models import Listing, SearchParams
from parsers.twogis import (
    TwoGisParser,
    _classify_listing_type,
    _extract_area,
    _extract_costs,
    _extract_floor,
    _extract_rooms,
    _owner_agent_prob,
    compute_freshness_score,
    compute_quality_score,
)

from .loaders import load_json_fixture


def _parse():
    payload = load_json_fixture("2gis_realty")
    return TwoGisParser().parse(json.dumps(payload), SearchParams())


# ---------------------------------------------------------------------------
# Realty JSON → Listing
# ---------------------------------------------------------------------------
def test_parse_extracts_listings_skips_wanted_and_daily():
    results = _parse()
    # 5 items in fixture: 2 whole-apartment (one is a duplicate), 1 roommate,
    # 1 wanted (skipped), 1 daily (skipped). → 3 listings after parse().
    titles = [r.title for r in results]
    assert len(results) == 3, f"expected 3, got {len(results)}: {titles}"
    assert not any("срочно" in t.lower() for t in titles)  # wanted ad dropped
    assert not any("посуточно" in t.lower() for t in titles)  # daily dropped


def test_listing_has_source_and_url():
    for r in _parse():
        assert r.source == "twogis"
        assert r.url.startswith("https://2gis.kz/")


def test_structured_fields_preferred_over_regex():
    a = next(r for r in _parse() if "алма сити" in r.title.lower())
    assert a.price == 250000
    assert a.rooms == 2
    assert a.area == 55
    assert a.floor == 7
    assert a.total_floors == 12
    assert a.building_id == "b-almacity-4"
    assert a.provider == "Этажи"
    assert a.provider_org_id == "org-etagi-1"
    assert a.views == 142
    assert a.rental_period == "long_term"
    assert a.deal_type == "long_term"


def test_photos_strip_2gis_map_tiles():
    a = next(r for r in _parse() if "алма сити" in r.title.lower())
    photos = a.photo.split("|") if a.photo else []
    assert len(photos) == 2
    assert not any("maps.2gis.com" in p for p in photos)


def test_contact_normalization_and_whatsapp():
    a = next(r for r in _parse() if "алма сити" in r.title.lower())
    assert a.phone == "+77071234567"
    assert a.contact_whatsapp == "+77071234567"


# ---------------------------------------------------------------------------
# Context-aware cost extraction (§10) — no "first number = price"
# ---------------------------------------------------------------------------
def test_costs_split_by_context():
    text = ("Сдаётся 2-комн квартира. 250 000 тг/месяц. "
            "Депозит 50 000, возвратный. Комиссия 30%. "
            "Коммунальные 5–6 тыс отдельно.")
    c = _extract_costs(text)
    assert c["rent"] == 250000
    assert c["deposit"] == 50000
    assert c["deposit_refundable"] is True
    assert c["commission_percent"] == 30
    assert c["utilities"] == "separate"
    assert c["utilities_min"] == 5000
    assert c["utilities_max"] == 6000


def test_costs_first_payment_not_rent():
    # "первый платёж 120к, дальше 80к" → first_payment=120000, rent=80000
    text = "первый платёж 120 000, дальше 80 000 тг/месяц"
    c = _extract_costs(text)
    assert c["first_payment"] == 120000
    assert c["rent"] == 80000


def test_costs_per_person_for_roommate():
    text = "Ищем девушку на подселение. 70 000 за человека + коммунальные"
    c = _extract_costs(text)
    assert c["rent"] == 70000
    assert c["price_per_person"] is True


def test_costs_k_shorthand_without_currency():
    text = "аренда 180к в месяц"
    c = _extract_costs(text)
    assert c["rent"] == 180000


def test_costs_included_overrides_separate():
    text = "аренда 200 000 тг, всё включено"
    c = _extract_costs(text)
    assert c["rent"] == 200000
    assert c["utilities"] == "included"


def test_costs_skips_implausible_low_price():
    text = "5 минут до метро. аренда 150000 тг"
    c = _extract_costs(text)
    # "5" alone isn't rent; one real rent mention wins
    assert c["rent"] == 150000


# ---------------------------------------------------------------------------
# listing_type classification (§4.2)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text,expected", [
    ("Ищу квартиру, сниму срочно", "WANTED"),
    ("Квартира посуточно, 15 000 за сутки", "SHORT_TERM"),
    ("Ищем девушку на подселение", "ROOMMATE"),
    ("Сдаю комнату в квартире", "SUBLET"),
    ("Сдаётся частный дом", "HOUSE"),
    ("Сдаётся 2-комнатная квартира", "WHOLE_APARTMENT"),
    ("комната светлая", "PRIVATE_ROOM"),
    ("какой-то текст без маркеров", "UNKNOWN"),
])
def test_classify_listing_type(text, expected):
    assert _classify_listing_type(text) == expected


# ---------------------------------------------------------------------------
# Rooms / area / floor extractors
# ---------------------------------------------------------------------------
def test_extract_rooms_variants():
    assert _extract_rooms("2-комн квартира") == 2
    assert _extract_rooms("студия") == 0
    assert _extract_rooms("2 бөлмелі") == 2
    assert _extract_rooms("трёхкомнатная") == 3
    assert _extract_rooms("без указания комнат") is None


def test_extract_area_variants():
    assert _extract_area("55 м²") == 55
    assert _extract_area("43/27/6") == 43
    assert _extract_area("45 шаршы") == 45
    assert _extract_area("не указана") is None


def test_extract_floor_not_confused_with_area():
    f, tf = _extract_floor("7/12 этаж")
    assert (f, tf) == (7, 12)
    f, tf = _extract_floor("43/27/6")  # area triple, not floor
    assert f is None and tf is None


# ---------------------------------------------------------------------------
# Phone normalization (shared helper)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("87071234567", "+77071234567"),
    ("+7 707 123 45 67", "+77071234567"),
    ("8 (707) 123-45-67", "+77071234567"),
    ("wa.me/77071234567", "+77071234567"),
    ("api.whatsapp.com/send?phone=77071234567", "+77071234567"),
    ("номер не указан", ""),
    ("123", ""),
])
def test_normalize_phone(raw, expected):
    assert normalize_phone(raw) == expected


# ---------------------------------------------------------------------------
# owner/agent probability
# ---------------------------------------------------------------------------
def test_owner_signals_high_probability():
    owner, agent = _owner_agent_prob("собственник, без посредников")
    assert owner > 0.7
    assert agent < 0.3


def test_agent_signals_low_probability():
    owner, agent = _owner_agent_prob("комиссия 30%, риелтор", has_org=True)
    assert owner < 0.4
    assert agent > 0.6


# ---------------------------------------------------------------------------
# fingerprint + cross-run dedup (§9)
# ---------------------------------------------------------------------------
def test_property_fingerprint_groups_duplicates():
    a = property_fingerprint(building_id="b-1", rooms=2, area=55,
                             floor=7, price=250000)
    b = property_fingerprint(building_id="b-1", rooms=2, area=55.0,
                             floor=7, price=255000)  # price within ±5% bucket
    c = property_fingerprint(building_id="b-1", rooms=2, area=55,
                             floor=7, price=300000)  # different price bucket
    assert a == b
    assert a != c


def test_property_fingerprint_returns_empty_without_signal():
    assert property_fingerprint() == ""


def test_fingerprint_dedup_merges_within_run():
    # fixture has two whole-apartment listings that map to the same
    # building/rooms/area/floor/price → one master with sources_count == 2.
    parser = TwoGisParser()
    payload = load_json_fixture("2gis_realty")
    raw = parser.parse(json.dumps(payload), SearchParams())
    parser._fingerprint_dedup_inplace(raw)
    whole = [r for r in raw if "алма сити" in r.title.lower()]
    assert len(whole) == 1
    assert whole[0].sources_count == 2
    # Master should be the earliest-published one.
    assert whole[0].date_published.startswith("2026-08-16T09:12")


# ---------------------------------------------------------------------------
# Quality + freshness scorers
# ---------------------------------------------------------------------------
def test_quality_score_full_listing_beats_sparse():
    full = Listing(title="x", price=250000, area=55, rooms=2, floor=7,
                   photo="a|b", address="Алматы", lat=43.2, lon=76.9,
                   deposit=50000, utilities="included", available_from="сент")
    full.owner_probability = 0.9
    sparse = Listing(title="y")
    assert compute_quality_score(full) > compute_quality_score(sparse)


def test_freshness_score_recent_is_high():
    from datetime import datetime
    recent = datetime.now().isoformat()
    assert compute_freshness_score(recent) == 1.0
    assert compute_freshness_score("2020-01-01") == 0.10
    assert compute_freshness_score("") == 0.0


# ---------------------------------------------------------------------------
# Live endpoint wiring (Stage-1 recon done 2026-08-16): URL + params verified.
# ---------------------------------------------------------------------------
def test_run_uses_live_endpoint_constants():
    # The recon set REALTY_CONFIGURED=True; parser now builds real URLs.
    import parsers.twogis as tg
    assert tg.REALTY_CONFIGURED is True
    assert tg.REALTY_ENDPOINT == "https://market-backend.api.2gis.ru/5.0/realty/items"
    assert tg.REGION_ID == "9430034490064971"            # Almaty realty region
    assert tg.CLIENT_ID == "4"                            # 2GIS web
    assert tg.RUBRIC_RENT_LONG == "70241201812768719"     # Аренда жилой
    # build_url must include the verified required params.
    p = TwoGisParser()
    url = p._build_realty_url(SearchParams(), page=2, page_size=10)
    for needle in ("region_id=9430034490064971", "locale=ru_KZ",
                   "category_ids=70241201812768719", "page=2",
                   "page_size=10", "point1=76.78", "platform_code=4",
                   "fields=items."):
        assert needle in url, f"url missing {needle!r}: {url}"


def test_run_live_returns_empty_gracefully_when_no_listings():
    # Almaty realty Canton returns 0 listings for rent categories (verified
    # 2026-08-16 — matches the source-doc §2/§44 warning). The parser must
    # request the live endpoint (HTTP 200, JSON) and return [] with status
    # "empty", no exception, no spurious `not_configured`.
    parser = TwoGisParser()
    results = parser.run(SearchParams(max_pages=1))
    assert results == []
    assert parser.last_stats.status in ("empty", "ok")
    # The live endpoint must have been called (page fetched).
    assert parser.last_stats.pages_fetched >= 1


def test_parse_html_returns_empty():
    # 2GIS is JSON-first; an HTML SPA shell carries no listings.
    assert TwoGisParser().parse("<html><body>SPA shell</body></html>",
                                SearchParams()) == []


def test_parse_malformed_json_returns_empty():
    assert TwoGisParser().parse("{not valid json", SearchParams()) == []


# ---------------------------------------------------------------------------
# Realty JSON item path tolerates alternate wrappers
# ---------------------------------------------------------------------------
def test_find_items_under_result_adverts():
    # 2GIS realty items wrap price under an `ads` array. The parser must
    # dig the first ad's price even under the alternate `adverts` array key.
    payload = {"result": {"adverts": [{"id": "1", "title": "t",
                                        "ads": [{"price": {"value": 100000,
                                                            "currency": "KZT"}}]}]}}
    parser = TwoGisParser()
    results = parser.parse(json.dumps(payload), SearchParams())
    assert len(results) == 1
    assert results[0].price == 100000


def test_listing_type_and_rent_per_m2_computed():
    a = next(r for r in _parse() if "алма сити" in r.title.lower())
    assert a.listing_type == "WHOLE_APARTMENT"
    assert a.rent_per_m2 == round(250000 / 55, 2)
    assert a.duplicate_group_id  # fingerprint set
    assert a.quality_score is not None and a.quality_score > 0


# ---------------------------------------------------------------------------
# Building enrichment + storage (§11)
# ---------------------------------------------------------------------------
def test_building_upsert_and_get(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", tmp_path / "f.db")
    dbmod.reset_db()
    b = dbmod.upsert_building({
        "building_id": "b-1", "address": "Саина 92",
        "lat": 43.2389, "lon": 76.882, "floors_total": 12,
        "residential_complex": "Алма Сити"})
    assert b is not None
    got = dbmod.get_building("b-1")
    assert got["residential_complex"] == "Алма Сити"
    assert got["floors_total"] == 12
    listing = Listing(title="x", building_id="b-1")
    from data.buildings import _enrich_one, _ZHK_INDEX
    _enrich_one(listing, dbmod.get_building)
    assert listing.residential_complex == "Алма Сити"
    assert listing.total_floors == 12
    dbmod.close_db()


def test_organization_upsert_and_list(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", tmp_path / "f.db")
    dbmod.reset_db()
    dbmod.upsert_organization({
        "org_id": "org-1", "name": "Этажи Алматы",
        "phones": ["+77071234567"], "rubrics": ["Агентства недвижимости"]})
    found = dbmod.list_organizations("Этажи")
    assert len(found) == 1
    assert found[0]["phones"] == "+77071234567"
    dbmod.close_db()
