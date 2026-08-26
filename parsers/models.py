from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class SearchParams:
    query: str = ""
    city: str = "almaty"
    rooms: list[int] = field(default_factory=list)
    price_min: Optional[int] = None
    price_max: Optional[int] = None
    district: str = ""
    floor_min: Optional[int] = None
    floor_max: Optional[int] = None
    area_min: Optional[int] = None
    area_max: Optional[int] = None
    limit: int = 20
    # How many result pages each site should be crawled. 0 = site default
    # (each parser defines its own max_pages).
    max_pages: int = 0

    def matches_price(self, price: Optional[int]) -> bool:
        if price is None:
            return True
        if self.price_min is not None and price < self.price_min:
            return False
        if self.price_max is not None and price > self.price_max:
            return False
        return True

    def matches_rooms(self, rooms: Optional[int]) -> bool:
        if not self.rooms:
            return True
        if rooms is None:
            return True
        return rooms in self.rooms

    def matches_area(self, area: Optional[float]) -> bool:
        if area is None:
            return True
        if self.area_min is not None and area < self.area_min:
            return False
        if self.area_max is not None and area > self.area_max:
            return False
        return True

    def matches_floor(self, floor: Optional[int]) -> bool:
        if floor is None:
            return True
        if self.floor_min is not None and floor < self.floor_min:
            return False
        if self.floor_max is not None and floor > self.floor_max:
            return False
        return True

    def matches_district(self, text: str) -> bool:
        if not self.district:
            return True
        return self.district.lower() in text.lower()


@dataclass
class Listing:
    title: str = ""
    price: Optional[int] = None
    currency: str = "тг"
    rooms: Optional[int] = None
    area: Optional[float] = None
    floor: Optional[int] = None
    total_floors: Optional[int] = None
    address: str = ""
    url: str = ""
    source: str = ""
    phone: str = ""
    description: str = ""
    photo: str = ""
    date_published: str = ""
    date_updated: str = ""
    lat: Optional[float] = None
    lon: Optional[float] = None
    # --- Extended fields (2GIS-first; shared by all parsers). All Optional
    # Listing identity / provenance
    listing_id_alt: Optional[str] = None      # source_listing_id / provider's id
    listing_type: Optional[str] = None        # WHOLE_APARTMENT|PRIVATE_ROOM|SHARED_ROOM|ROOMMATE|SUBLET|HOUSE|SHORT_TERM|WANTED|UNKNOWN
    deal_type: Optional[str] = None           # long_term | short_term | unknown
    rental_period: Optional[str] = None      # daily | monthly | long_term | unknown
    provider: Optional[str] = None            # "Этажи", "Суточно.ру", "Отелло", ...
    building_id: Optional[str] = None
    provider_org_id: Optional[str] = None
    provider_branch_id: Optional[str] = None
    views: Optional[int] = None               # post view counter
    # Cost breakdown (context-aware extraction)
    deposit: Optional[int] = None             # KZT
    deposit_refundable: Optional[bool] = None
    commission_percent: Optional[int] = None  # %
    commission_fixed: Optional[int] = None    # KZT
    utilities: Optional[str] = None           # "included" | "separate" | "unknown"
    utilities_min: Optional[int] = None      # KZT
    utilities_max: Optional[int] = None      # KZT
    rent_per_m2: Optional[float] = None       # price / area
    price_per_person: Optional[bool] = None  # roommate/sublet
    first_payment: Optional[int] = None      # "первый платёж 120к, дальше 80к"
    # Location 3-level (raw lives in `address`)
    normalized_location: Optional[str] = None
    microdistrict: Optional[str] = None
    residential_complex: Optional[str] = None # ЖК
    landmark: Optional[str] = None            # "возле Сайрана"
    # Contacts (phone already exists above)
    contact_telegram: Optional[str] = None
    contact_whatsapp: Optional[str] = None
    # Conditions (only if explicitly stated in structured/text, never via CV)
    available_from: Optional[str] = None      # ISO date
    furnished: Optional[bool] = None
    pets_allowed: Optional[bool] = None
    children_allowed: Optional[bool] = None
    smoking_allowed: Optional[bool] = None
    appliances: Optional[str] = None          # pipe-list: "washer|fridge|ac"
    infra_features: Optional[str] = None      # pipe-list: "parking|security|playground"
    # Owner/agent classification (rule-based, not LLM)
    owner_probability: Optional[float] = None # 0..1
    agent_probability: Optional[float] = None  # 0..1 (= 1 - owner)
    # Scoring / dedup grouping
    freshness_score: Optional[float] = None   # 0..1
    quality_score: Optional[int] = None        # 0..100
    duplicate_group_id: Optional[str] = None
    sources_count: Optional[int] = None       # "3 источника" merged

    def __post_init__(self):
        if self.title:
            self.title = " ".join(self.title.split())
        if self.address:
            self.address = " ".join(self.address.split())
        if self.description:
            self.description = " ".join(self.description.split())

    def to_dict(self) -> dict:
        return asdict(self)

    def price_str(self) -> str:
        if self.price is None:
            return "—"
        return f"{self.price:,} {self.currency}".replace(",", " ")

    def price_rub(self) -> Optional[float]:
        # Whole rubles only: kopecks are noise at this price magnitude.
        if self.price is None:
            return None
        from rates import rub_per_kzt
        return float(round(self.price * rub_per_kzt()))

    def price_usd(self) -> Optional[float]:
        if self.price is None:
            return None
        from rates import usd_per_kzt
        return round(self.price * usd_per_kzt(), 2)

    def price_all_str(self) -> str:
        """Price in KZT + RUB + USD."""
        if self.price is None:
            return "—"
        rub = self.price_rub()
        usd = self.price_usd()
        parts = [f"{self.price:,} тг".replace(",", " ")]
        if rub is not None:
            parts.append(f"{rub:,.0f} руб".replace(",", " "))
        if usd is not None:
            parts.append(f"${usd:,.0f}")
        return " · ".join(parts)
