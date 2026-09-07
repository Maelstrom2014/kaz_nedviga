"""Тесты извлечения признаков (features.py) и CSV-экспорта (features_csv.py)."""
from __future__ import annotations

import csv
from pathlib import Path

from features import FEATURE_COLUMNS, build_rows, extract_tags, feature_row


def test_extract_tags_renovation():
    tags = extract_tags("2-комн, евроремонт", "свежий ремонт, вся техника")
    assert tags["renovation_grade"] == 3
    assert tags["tag_euro_renovation"] == 1

    tags = extract_tags("квартира", "требует ремонта, мебель забираем")
    assert tags["renovation_grade"] == 0
    assert tags["tag_needs_repair"] == 1
    assert tags["tag_fully_furnished"] == 0


def test_extract_tags_furniture():
    tags = extract_tags("", "полностью меблирована, есть вся техника")
    assert tags["furniture_level"] == "full"
    tags = extract_tags("", "без мебели")
    assert tags["furniture_level"] == "none"
    tags = extract_tags("", "частично меблирована")
    assert tags["furniture_level"] == "partial"


def test_extract_tags_building_and_bathroom():
    tags = extract_tags("монолитный дом, новостройка", "раздельный санузел")
    assert tags["building_type"] == "монолит"
    assert tags["tag_new_building"] == 1
    assert tags["bathroom_type"] == "separated"


def test_extract_tags_numeric():
    tags = extract_tags("дом 2019 года постройки", "потолки 3.1 метра")
    assert tags["year_built"] == 2019
    assert tags["ceiling_height"] == 3.1


def test_extract_tags_infra_and_conditions():
    tags = extract_tags(
        "срочно сдам",
        "консьерж, видеонаблюдение, детская площадка, парковка, "
        "без комиссии, можно торг, рядом школа",
    )
    for key in ("tag_concierge", "tag_cctv", "tag_playground",
                "tag_parking", "tag_no_commission", "tag_bargain",
                "tag_near_school", "tag_urgent"):
        assert tags[key] == 1, key
    assert "|" in tags["tag_list"]
    assert "concierge" in tags["tag_list"]


def test_feature_row_derived():
    row = feature_row({
        "title": "2-к евроремонт", "price": 300000, "currency": "тг",
        "rooms": 2, "area": 60.0, "floor": 5, "total_floors": 9,
        "description": "полностью меблирована, кондиционер",
        "photo": "http://a/1.jpg|http://a/2.jpg",
        "source": "krisha", "url": "https://x/1",
    })
    assert row["price_per_m2"] == 5000.0
    assert row["price_per_room"] == 150000.0
    assert row["floor_ratio"] == round(5 / 9, 2)
    assert row["is_first_floor"] == 0 and row["is_last_floor"] == 0
    assert row["n_photos"] == 2 and row["has_photo"] == 1
    assert row["tag_ac"] == 1 and row["tag_fully_furnished"] == 1
    assert row["source"] == "krisha"


def test_feature_row_floor_edges():
    row = feature_row({
        "price": 100000, "floor": 1, "total_floors": 5, "area": 40,
        "rooms": 1, "source": "olx", "url": "u",
    })
    assert row["is_first_floor"] == 1 and row["is_last_floor"] == 0
    row = feature_row({
        "price": 100000, "floor": 5, "total_floors": 5, "area": 40,
        "rooms": 1, "source": "olx", "url": "u",
    })
    assert row["is_first_floor"] == 0 and row["is_last_floor"] == 1


def test_build_rows_and_csv(tmp_path: Path):
    items = [
        {"title": "1-к студия", "price": 150000, "rooms": 1, "area": 32,
         "source": "krisha", "url": "https://x/1", "description": "студия"},
        {"title": "2-к", "price": 250000, "rooms": 2, "area": 55,
         "source": "olx", "url": "https://x/2", "description": ""},
        "junk",  # невалидные записи пропускаются
    ]
    rows = build_rows(items)
    assert len(rows) == 2
    assert all(set(FEATURE_COLUMNS) <= set(r) for r in rows)

    from features_csv import write_csv

    out = tmp_path / "features.csv"
    n = write_csv(rows, out)
    assert n == 2
    with out.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        data = list(reader)
    assert len(data) == 2
    assert data[0]["price"] == "150000"


def test_auto_label_group_median():
    from ml.train_price_model import area_bucket, auto_label

    def mk(price, rooms="2", area="60", district="almal"):
        return {"price": price, "rooms": rooms, "area": area,
                "district": district}

    rows = [mk(p) for p in (100000, 110000, 120000, 200000, 300000)]
    labels = auto_label(rows)
    # медиана 120000 → первые три «хорошие»
    assert labels == [1, 1, 1, 0, 0]
    assert area_bucket(15.0) == "0" and area_bucket(31.0) == "2"
