"""Tests for Almaty districts data."""
from data.districts import (
    DISTRICTS,
    DISTRICT_NAMES,
    DISTRICT_COORDS,
    get_district_list,
    get_districts_json,
)


def test_districts_count():
    assert len(DISTRICTS) == 8


def test_district_names():
    expected = {
        "Алмалинский", "Жетысуский", "Ауэзовский", "Медеуский",
        "Турксибский", "Наурызбайский", "Бостандыкский", "Алатауский",
    }
    assert set(DISTRICT_NAMES) == expected


def test_districts_have_coords():
    for d in DISTRICTS:
        assert "lat" in d
        assert "lon" in d
        assert -90 <= d["lat"] <= 90
        assert -180 <= d["lon"] <= 180


def test_districts_have_polygons():
    for d in DISTRICTS:
        assert "polygon" in d
        assert len(d["polygon"]) >= 3  # minimum 3 points for a polygon


def test_districts_have_detailed_polygons():
    """Detailed polygons should have many vertices for accurate borders."""
    for d in DISTRICTS:
        assert len(d["polygon"]) >= 15, f"{d['name']}: polygon too simple"


def test_centers_inside_polygons():
    """Each district center should fall inside its own polygon."""
    def point_in_poly(lat, lon, poly):
        x, y = lon, lat
        n = len(poly)
        inside = False
        j = n - 1
        for i in range(n):
            xi, yi = poly[i][1], poly[i][0]
            xj, yj = poly[j][1], poly[j][0]
            if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
                inside = True
            j = i
        return inside
    for d in DISTRICTS:
        assert point_in_poly(d["lat"], d["lon"], d["polygon"]), (
            f"{d['name']}: center ({d['lat']}, {d['lon']}) not inside polygon"
        )


def test_coords_dict():
    assert "Алмалинский" in DISTRICT_COORDS
    lat, lon = DISTRICT_COORDS["Алмалинский"]
    assert 43.25 <= lat <= 43.27


def test_get_district_list():
    lst = get_district_list()
    assert isinstance(lst, list)
    assert len(lst) == 8
    assert "Алмалинский" in lst


def test_get_districts_json():
    data = get_districts_json()
    assert isinstance(data, list)
    assert len(data) == 8
    assert data[0]["name"] == "Алмалинский"
