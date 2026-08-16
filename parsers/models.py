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
        if self.price is None:
            return None
        from rates import rub_per_kzt
        return round(self.price * rub_per_kzt(), 2)

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
