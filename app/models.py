"""Структуры данных, которые проходят через пайплайн."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

CATEGORIES = ["campus", "education", "dormitory", "library", "student_life", "city"]

CATEGORY_LABELS = {
    "campus": "Кампус",
    "education": "Учебные пространства",
    "dormitory": "Общежития",
    "library": "Библиотека",
    "student_life": "Студенческая жизнь",
    "city": "Город",
}


@dataclass
class University:
    qid: str
    name: str
    description: str = ""
    names: list[str] = field(default_factory=list)  # все подписи и алиасы
    lat: Optional[float] = None
    lon: Optional[float] = None
    website: Optional[str] = None
    commons_category: Optional[str] = None
    image: Optional[str] = None  # имя файла P18
    wikipedia_url: Optional[str] = None
    country: Optional[str] = None
    city_qid: Optional[str] = None
    city_name: Optional[str] = None
    city_lat: Optional[float] = None
    city_lon: Optional[float] = None
    city_image: Optional[str] = None
    distance_to_center_km: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "qid": self.qid,
            "name": self.name,
            "description": self.description,
            "lat": self.lat,
            "lon": self.lon,
            "website": self.website,
            "wikipedia_url": self.wikipedia_url,
            "wikidata_url": f"https://www.wikidata.org/wiki/{self.qid}",
            "commons_category": self.commons_category,
            "country": self.country,
            "city": self.city_name,
            "distance_to_center_km": self.distance_to_center_km,
        }


@dataclass
class SourceHit:
    type: str  # wikidata_p18 | commons_category | commons_subcategory | commons_geo | official_site | city_p18 | city_geo
    label: str
    url: str
    detail: str = ""  # имя подкатегории, расстояние и т.п.


@dataclass
class Reason:
    text: str
    delta: float

    def to_dict(self) -> dict:
        return {"text": self.text, "delta": round(self.delta, 2)}


@dataclass
class Candidate:
    key: str
    title: str
    thumb_url: str
    full_url: str
    page_url: str
    width: int = 0
    height: int = 0
    mime: str = ""
    sha1: Optional[str] = None
    description: str = ""
    categories: str = ""
    author: str = ""
    license: str = ""
    license_url: str = ""
    lat: Optional[float] = None
    lon: Optional[float] = None
    date: Optional[str] = None
    sources: list[SourceHit] = field(default_factory=list)

    # заполняется пайплайном
    subject: str = "university"  # university | city
    confidence: float = 0.0
    reasons: list[Reason] = field(default_factory=list)
    category: Optional[str] = None
    category_reason: str = ""
    dhash: Optional[int] = None
    image_bytes: Optional[bytes] = None
    vision: Optional[dict] = None
    excluded: Optional[str] = None
    duplicates: int = 0

    @property
    def status(self) -> str:
        from . import config

        if self.confidence >= config.VERIFIED_THRESHOLD:
            return "verified"
        if self.confidence >= config.LIKELY_THRESHOLD:
            return "likely"
        return "unverified"

    def to_dict(self) -> dict:
        main = self.sources[0] if self.sources else None
        return {
            "id": self.key,
            "title": self.title,
            "thumb": self.thumb_url,
            "full": self.full_url,
            "page": self.page_url,
            "width": self.width,
            "height": self.height,
            "category": self.category,
            "category_label": CATEGORY_LABELS.get(self.category or "", ""),
            "category_reason": self.category_reason,
            "status": self.status,
            "confidence": round(self.confidence, 2),
            "reasons": [r.to_dict() for r in self.reasons],
            "source": {
                "type": main.type,
                "label": main.label,
                "url": main.url,
                "detail": main.detail,
            }
            if main
            else None,
            "all_sources": [
                {"type": s.type, "label": s.label, "url": s.url, "detail": s.detail}
                for s in self.sources
            ],
            "license": self.license,
            "license_url": self.license_url,
            "author": self.author,
            "date": self.date,
            "description": self.description[:300],
            "duplicates_removed": self.duplicates,
        }
