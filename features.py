"""Извлечение признаков и тэгов из информации об объявлении.

``extract_tags(title, description)`` — текстовые сигналы (ремонт, мебель,
техника, санузел, дом, инфраструктура, условия), которых нет в структурированных
полях парсеров. ``feature_row()`` собирает плоскую строку признаков
(структурированные поля Listing + вычисляемые + тэги) для CSV/ML.
"""
from __future__ import annotations

import dataclasses
import re
from typing import Any, Optional

from parsers.models import Listing

# --- Канонические списки колонок (используются CSV-экспортом и ML) ---

BASE_COLUMNS: list[str] = [
    "price", "currency", "rooms", "area", "floor", "total_floors",
    "price_per_m2", "price_per_room", "floor_ratio", "is_first_floor",
    "is_last_floor", "district", "microdistrict", "residential_complex",
    "deal_type", "listing_type", "rental_period", "deposit",
    "commission_percent", "utilities", "owner_probability", "views",
    "quality_score", "sources_count", "n_photos", "has_photo", "source",
    "desc_length", "desc_words", "furnished", "pets_allowed",
    "children_allowed", "available_from",
]

NUMERIC_FEATURES: list[str] = [
    "price", "rooms", "area", "floor", "total_floors", "price_per_m2",
    "price_per_room", "floor_ratio", "year_built", "ceiling_height",
    "n_photos", "desc_length", "desc_words", "deposit", "commission_percent",
    "owner_probability", "views", "quality_score", "renovation_grade",
    "furniture_level",
]

CATEGORICAL_FEATURES: list[str] = [
    "source", "district", "deal_type", "listing_type", "building_type",
    "bathroom_type", "utilities", "currency",
]

# Столбцы-тэги (0/1): получают по колонке в CSV.
TAG_COLUMNS: list[str] = [
    "tag_euro_renovation", "tag_needs_repair", "tag_new_building",
    "tag_secondary", "tag_studio", "tag_apartments", "tag_euro_layout",
    "tag_balcony", "tag_loggia", "tag_two_bathrooms",
    "tag_separated_bathroom", "tag_combined_bathroom", "tag_shower",
    "tag_bath", "tag_washer", "tag_fridge", "tag_ac", "tag_dishwasher",
    "tag_tv", "tag_oven", "tag_microwave", "tag_internet", "tag_wifi",
    "tag_smart_lock", "tag_counters", "tag_autonomous_heating",
    "tag_central_heating", "tag_water_included", "tag_boiler",
    "tag_parking", "tag_garage", "tag_security", "tag_concierge",
    "tag_cctv", "tag_closed_yard", "tag_playground", "tag_elevator",
    "tag_freight_elevator", "tag_gym", "tag_view_mountains",
    "tag_view_park", "tag_panoramic", "tag_corner", "tag_near_metro",
    "tag_near_school", "tag_near_kindergarten", "tag_near_park",
    "tag_near_shop", "tag_urgent", "tag_bargain", "tag_no_commission",
    "tag_no_agency", "tag_owner", "tag_long_term", "tag_daily",
    "tag_for_family", "tag_for_students", "tag_fully_furnished",
    "tag_partial_furnished", "tag_unfurnished", "tag_appliances_included",
    "tag_quiet_yard", "tag_prestige",
]

# Все признаки: базовые + тэги + прочие извлечённые поля.
FEATURE_COLUMNS: list[str] = BASE_COLUMNS + [
    "year_built", "ceiling_height", "renovation_grade", "furniture_level",
    "building_type", "bathroom_type",
] + TAG_COLUMNS

# --- Текстовые паттерны (рус + каз) ---

_REPAIR_HIGH = re.compile(
    r"евроремонт|дизайнерск\w+ ремонт|нов\w+ ремонт|свеж\w+ ремонт|"
    r"ремонт\s+со\s+вкусом|капитальн\w+ ремонт|жаңа ремонт|"
    r"еуро ремонт", re.I)
_REPAIR_OK = re.compile(r"сделан ремонт|есть ремонт|хороший ремонт|ремонт 20\d\d", re.I)
_REPAIR_LOW = re.compile(
    r"требует ремонта|нужен ремонт|без ремонта|косметическ\w+ ремонт|"
    r"чернов\w+ (отделк|ремонт)|под ремонт|ремонт сделат|ремонт жоқ", re.I)

_FURN_FULL = re.compile(
    r"полностью меблирован|вся мебель|есть вся мебель|полностью furnished|"
    r"полност\w+ укомплектован|мебель\w*\s+и\s+техника\s+есть|толық жиһаз", re.I)
_FURN_PART = re.compile(
    r"частично меблирован|частично есть мебель|некоторая мебель|"
    r"минимум мебели|жиһаз ішінара", re.I)
_FURN_NONE = re.compile(r"без мебели|мебели нет|жиһазсыз", re.I)

_APPLIANCE_PATTERNS: dict[str, re.Pattern] = {
    "washer": re.compile(r"стиральн\w+ машин|ст\.?\s*машин|washing", re.I),
    "fridge": re.compile(r"холодильник|мұздатқыш", re.I),
    "ac": re.compile(r"кондиционер|сплит\s*систем|климат\s*контроль", re.I),
    "dishwasher": re.compile(r"посудомоечн\w+ машин|посудомойк", re.I),
    "tv": re.compile(r"\bтв\b|телевизор|smart\s*tv|\btv\b", re.I),
    "oven": re.compile(r"духовк|плита\s*(газ|электро)|печь", re.I),
    "microwave": re.compile(r"микроволнов|свч|мікрохвильов", re.I),
}

_NEW_BUILDING = re.compile(r"новостройк|новый дом|нов\w+ жк|сдан\w* в эксплуат|нов\w+ комплекс|жаңа тұрғын үй|new building", re.I)
_SECONDARY = re.compile(r"вторичк|вторичн\w+ рынок|старый фонд", re.I)
_STUDIO = re.compile(r"студи|studios?\b|баш көтерілім", re.I)
_APARTMENTS = re.compile(r"апартамент|апарт-\w*|penthouse|пентхаус", re.I)
_EURO_LAYOUT = re.compile(r"евро\s*планировк|евроформат|евро\s*ремонт планировк", re.I)

_BALCONY = re.compile(r"балкон|балкон\w*|балконным", re.I)
_LOGGIA = re.compile(r"лоджи", re.I)
_TWO_BATH = re.compile(r"два? санузл|2\s*санузл|два? с/у|2\s*с/у|ванная и гостев", re.I)
_BATH_SEP = re.compile(r"раздельн\w+ сануз|санузел раздельн|раздельн\w+ с/у|раздельный санузел", re.I)
_BATH_COMB = re.compile(r"совмещ\w+ сануз|санузел совмещ|совмещенн\w+ с/у", re.I)
_SHOWER = re.compile(r"душев\w+ (кабин|кабин)|душ\b", re.I)
_BATH_TUB = re.compile(r"ванн\w+\b", re.I)

_BUILDING_TYPE_PATTERNS: dict[str, re.Pattern] = {
    "панель": re.compile(r"панельн\w+ дом|панел|панельный", re.I),
    "кирпич": re.compile(r"кирпичн\w+ дом|\bкирпич\b|киір", re.I),
    "монолит": re.compile(r"монолитн\w+|монолит", re.I),
    "каркас": re.compile(r"каркасн\w+ дом|каркас", re.I),
    "блок": re.compile(r"блочн\w+ дом", re.I),
}

_CEILING = re.compile(
    r"потолк\w*\s*(?:—|–|-|:)?\s*(\d(?:[.,]\d)?)\s*(?:м\b|метр)", re.I)

_YEAR = re.compile(
    r"(?:дом|построен\w*|постройки|сдан\w*|год)\s*[:,]?\s*((?:19|20)\d{2})|"
    r"((?:19|20)\d{2})\s*года?\s*(?:постройки|сдачи)", re.I)

_METRO = re.compile(r"рядом с метро|метро\s+(?:«|\"|[\wА-Яа-я]+)|возле метро|около метро|метро\b", re.I)
_PARKING = re.compile(r"парковк|парковочное место|есть парковка| Parking|тұрақ", re.I)
_GARAGE = re.compile(r"гараж", re.I)
_SECURITY = re.compile(r"охран\w+|кпп|пропускной режим|қорғау", re.I)
_CONCIERGE = re.compile(r"консьерж", re.I)
_CCTV = re.compile(r"видеонаблюден|камеры? видеонаблюден|cctv", re.I)
_CLOSED_YARD = re.compile(r"закрыт\w+ двор|закрыт\w+ территор|огорожен\w+ территор", re.I)
_PLAYGROUND = re.compile(r"детск\w+ площадк|детская площадка|балабақша алаңы", re.I)
_ELEVATOR = re.compile(r"лифт\b", re.I)
_FREIGHT_ELEVATOR = re.compile(r"грузов\w+ лифт|грузовой лифт", re.I)
_GYM = re.compile(r"спортзал|тренажерн\w+ зал|fitness|фитнес", re.I)
_VIEW_MOUNTAINS = re.compile(r"вид на горы|горный вид|тау көрінісі", re.I)
_VIEW_PARK = re.compile(r"вид на парк|вид на дерев|парковый вид", re.I)
_PANORAMIC = re.compile(r"панорамн\w+ (окн|вид)|панорама", re.I)
_CORNER = re.compile(r"углов\w+ (квартир|дом)|угловая\b", re.I)
_NEAR_SCHOOL = re.compile(r"рядом (школа|со школой)|около школы|школа в пешей", re.I)
_NEAR_KINDERGARTEN = re.compile(r"рядом (детский сад|садик)|около (детского сада|садика)", re.I)
_NEAR_PARK = re.compile(r"рядом (парк|сквер)|около (парка|сквера)", re.I)
_NEAR_SHOP = re.compile(r"рядом (магазин|рынок|торговый центр|магnum|tspan)|в шаговой (доступности|,) ", re.I)
_URGENT = re.compile(r"срочно|сда[её]тся сегодня|заезжай сегодня|тез", re.I)
_BARGAIN = re.compile(r"торг\b|возможен торг|уместен торг|скидк", re.I)
_NO_COMMISSION = re.compile(r"без комиссии|комиссия 0|без агентских|0% комиссия", re.I)
_NO_AGENCY = re.compile(r"без посредников|без агентств", re.I)
_OWNER = re.compile(r"хозяин|от собственника|собственник сдает|иесі", re.I)
_LONG_TERM = re.compile(r"на длительный срок|долгосрочная аренда|долгосрочно", re.I)
_DAILY = re.compile(r"посуточно|на сутки|daily", re.I)
_FOR_FAMILY = re.compile(r"для семьи|семейным|для семейных|отдадим семейным", re.I)
_FOR_STUDENTS = re.compile(r"для студентов|студентам", re.I)
_COUNTERS = re.compile(r"счетчик|счётчик|счётчик", re.I)
_AUTON_HEATING = re.compile(r"автономн\w+ отоплен|индивидуальн\w+ отоплен", re.I)
_CENTRAL_HEATING = re.compile(r"центральн\w+ отоплен", re.I)
_WATER_INCL = re.compile(r"вода включена|коммунальные включены|ку включены", re.I)
_BOILER = re.compile(r"бойлер|водонагреват", re.I)
_SMART_LOCK = re.compile(r"умн\w+ замок|smart\s*lock|кодовый замок", re.I)
_INTERNET = re.compile(r"интернет\b", re.I)
_WIFI = re.compile(r"wifi|wi-?fi|вай-?фай", re.I)
_QUIET_YARD = re.compile(r"тих\w+ двор|зелен\w+ двор|ухоженн\w+ двор", re.I)
_PRESTIGE = re.compile(r"элитн\w+|престижн\w+|бизнес[- ]класс|premium|премиум", re.I)


def extract_tags(title: str, description: str) -> dict[str, Any]:
    """Извлечь тэги и признаки из title+description (рус/каз паттерны).

    Возвращает плоский dict: тэги 0/1 + числовые (year_built,
    ceiling_height, renovation_grade, furniture_level) + категориальные
    (building_type, bathroom_type).
    """
    text = f"{title or ''} {description or ''}".lower()
    has = lambda p: bool(p.search(text))  # noqa: E731

    tags: dict[str, Any] = {c: 0 for c in TAG_COLUMNS}

    renovation_grade: Optional[int] = None
    if _REPAIR_HIGH.search(text):
        renovation_grade = 3
    elif _REPAIR_OK.search(text):
        renovation_grade = 2
    elif _REPAIR_LOW.search(text):
        renovation_grade = 0
    tags["renovation_grade"] = renovation_grade
    tags["tag_euro_renovation"] = int(renovation_grade == 3)
    tags["tag_needs_repair"] = int(renovation_grade == 0)

    furniture_level: Optional[str] = None
    if _FURN_FULL.search(text):
        furniture_level = "full"
    elif _FURN_PART.search(text):
        furniture_level = "partial"
    elif _FURN_NONE.search(text):
        furniture_level = "none"
    tags["furniture_level"] = furniture_level
    tags["tag_fully_furnished"] = int(furniture_level == "full")
    tags["tag_partial_furnished"] = int(furniture_level == "partial")
    tags["tag_unfurnished"] = int(furniture_level == "none")

    building_type = None
    for name, pat in _BUILDING_TYPE_PATTERNS.items():
        if pat.search(text):
            building_type = name
            break
    tags["building_type"] = building_type

    bathroom_type = None
    if _BATH_SEP.search(text):
        bathroom_type = "separated"
    elif _BATH_COMB.search(text):
        bathroom_type = "combined"
    elif _SHOWER.search(text):
        bathroom_type = "shower"
    elif _BATH_TUB.search(text):
        bathroom_type = "bath"
    tags["bathroom_type"] = bathroom_type

    tag_map = [
        ("tag_new_building", _NEW_BUILDING), ("tag_secondary", _SECONDARY),
        ("tag_studio", _STUDIO), ("tag_apartments", _APARTMENTS),
        ("tag_euro_layout", _EURO_LAYOUT), ("tag_balcony", _BALCONY),
        ("tag_loggia", _LOGGIA), ("tag_two_bathrooms", _TWO_BATH),
        ("tag_separated_bathroom", _BATH_SEP),
        ("tag_combined_bathroom", _BATH_COMB), ("tag_shower", _SHOWER),
        ("tag_bath", _BATH_TUB), ("tag_washer", _APPLIANCE_PATTERNS["washer"]),
        ("tag_fridge", _APPLIANCE_PATTERNS["fridge"]),
        ("tag_ac", _APPLIANCE_PATTERNS["ac"]),
        ("tag_dishwasher", _APPLIANCE_PATTERNS["dishwasher"]),
        ("tag_tv", _APPLIANCE_PATTERNS["tv"]),
        ("tag_oven", _APPLIANCE_PATTERNS["oven"]),
        ("tag_microwave", _APPLIANCE_PATTERNS["microwave"]),
        ("tag_internet", _INTERNET), ("tag_wifi", _WIFI),
        ("tag_smart_lock", _SMART_LOCK), ("tag_counters", _COUNTERS),
        ("tag_autonomous_heating", _AUTON_HEATING),
        ("tag_central_heating", _CENTRAL_HEATING),
        ("tag_water_included", _WATER_INCL), ("tag_boiler", _BOILER),
        ("tag_parking", _PARKING), ("tag_garage", _GARAGE),
        ("tag_security", _SECURITY), ("tag_concierge", _CONCIERGE),
        ("tag_cctv", _CCTV), ("tag_closed_yard", _CLOSED_YARD),
        ("tag_playground", _PLAYGROUND), ("tag_elevator", _ELEVATOR),
        ("tag_freight_elevator", _FREIGHT_ELEVATOR), ("tag_gym", _GYM),
        ("tag_view_mountains", _VIEW_MOUNTAINS), ("tag_view_park", _VIEW_PARK),
        ("tag_panoramic", _PANORAMIC), ("tag_corner", _CORNER),
        ("tag_near_metro", _METRO), ("tag_near_school", _NEAR_SCHOOL),
        ("tag_near_kindergarten", _NEAR_KINDERGARTEN),
        ("tag_near_park", _NEAR_PARK), ("tag_near_shop", _NEAR_SHOP),
        ("tag_urgent", _URGENT), ("tag_bargain", _BARGAIN),
        ("tag_no_commission", _NO_COMMISSION), ("tag_no_agency", _NO_AGENCY),
        ("tag_owner", _OWNER), ("tag_long_term", _LONG_TERM),
        ("tag_daily", _DAILY), ("tag_for_family", _FOR_FAMILY),
        ("tag_for_students", _FOR_STUDENTS),
        ("tag_appliances_included", re.compile(r"техника\s+(есть|включена)|со всей техникой", re.I)),
        ("tag_quiet_yard", _QUIET_YARD), ("tag_prestige", _PRESTIGE),
    ]
    for key, pat in tag_map:
        if pat.search(text):
            tags[key] = 1

    # Числовые из текста
    tags["year_built"] = None
    tags["ceiling_height"] = None
    m = _YEAR.search(text)
    if m:
        try:
            tags["year_built"] = int(next(g for g in m.groups() if g is not None))
        except (StopIteration, TypeError, ValueError):
            pass
    m = _CEILING.search(text)
    if m:
        try:
            tags["ceiling_height"] = float(str(m.group(1)).replace(",", "."))
        except (TypeError, ValueError):
            pass

    # Список человеческих тэгов (для отображения)
    tags["tag_list"] = "|".join(
        c.removeprefix("tag_") for c, v in tags.items()
        if c.startswith("tag_") and v == 1
    )
    return tags


def _to_listing(data: dict | Listing) -> Optional[Listing]:
    """Нормализовать dict (из кэша/БД) или Listing в Listing."""
    if isinstance(data, Listing):
        return data
    if not isinstance(data, dict):
        return None
    fields = {f.name for f in dataclasses.fields(Listing)}
    return Listing(**{k: v for k, v in data.items() if k in fields})


def _point_in_district(lat: float, lon: float) -> Optional[str]:
    try:
        from geo.districts import DISTRICTS

        for d in DISTRICTS:
            poly = d["polygon"]
            inside = False
            n = len(poly)
            for i in range(n):
                x1, y1 = poly[i][1], poly[i][0]
                x2, y2 = poly[(i + 1) % n][1], poly[(i + 1) % n][0]
                if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
                    inside = not inside
            if inside:
                return d["name"]
    except Exception:
        return None
    return None


def feature_row(data: dict | Listing) -> dict[str, Any]:
    """Полная строка признаков для одного объявления (для CSV и ML)."""
    item = _to_listing(data)
    row: dict[str, Any] = {}
    for c in BASE_COLUMNS:
        row[c] = getattr(item, c, None)
    row["desc_length"] = len(item.description or "")
    row["desc_words"] = len((item.description or "").split())

    # Вычисляемые
    if item.price and item.area:
        try:
            row["price_per_m2"] = round(item.price / item.area, 1)
        except ZeroDivisionError:
            row["price_per_m2"] = None
    else:
        row["price_per_m2"] = None
    row["price_per_room"] = (
        round(item.price / item.rooms, 1) if item.price and item.rooms else None
    )
    if item.floor and item.total_floors:
        row["floor_ratio"] = round(item.floor / item.total_floors, 2)
    else:
        row["floor_ratio"] = None
    row["is_first_floor"] = int(item.floor == 1) if item.floor else 0
    row["is_last_floor"] = (
        int(item.floor == item.total_floors)
        if item.floor and item.total_floors else 0
    )
    row["n_photos"] = len([u for u in (item.photo or "").split("|") if u.startswith("http")])
    row["has_photo"] = int(row["n_photos"] > 0)

    # Район: приоритет — координаты, затем текст поля district/address
    district = None
    if item.lat is not None and item.lon is not None:
        district = _point_in_district(item.lat, item.lon)
    if not district:
        district = item.normalized_location or item.microdistrict or None
    row["district"] = district

    # Тэги из текста + структурные условия
    tags = extract_tags(item.title, item.description)
    row.update({k: v for k, v in tags.items() if k != "tag_list"})
    row["tag_list"] = tags["tag_list"]
    if item.furnished is True:
        row["tag_fully_furnished"] = 1
        row["furniture_level"] = row.get("furniture_level") or "full"
    if item.pets_allowed is True:
        row["tag_list"] = "|".join(filter(None, [row["tag_list"], "pets_ok"]))
    if item.children_allowed is True:
        row["tag_list"] = "|".join(filter(None, [row["tag_list"], "children_ok"]))
    if item.listing_type == "SHORT_TERM":
        row["tag_daily"] = 1
    if item.deal_type == "short_term":
        row["tag_daily"] = 1
    elif item.deal_type == "long_term":
        row["tag_long_term"] = 1
    row["row_key"] = f"{item.source}|{item.url}"
    row["url"] = item.url
    row["title"] = item.title
    return row


def build_rows(items: list[dict | Listing]) -> list[dict[str, Any]]:
    """feature_row для списка объявлений (пропуская невалидные)."""
    rows = []
    for it in items:
        try:
            rows.append(feature_row(it))
        except Exception:
            continue
    return rows
