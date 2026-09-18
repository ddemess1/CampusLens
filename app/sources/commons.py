"""Wikimedia Commons: открытые фото с лицензией, автором и часто с геометкой."""
from __future__ import annotations

import asyncio
import re
from typing import Optional

from .. import config
from ..fetcher import SourceError
from ..models import Candidate, SourceHit
from ..utils import commons_file_url, haversine_m, human_distance, normalize, strip_html, to_float

COMMONS_API = "https://commons.wikimedia.org/w/api.php"

EXTMETA = (
    "ImageDescription|Artist|LicenseShortName|LicenseUrl|GPSLatitude|GPSLongitude|"
    "Categories|DateTimeOriginal|ObjectName"
)

# Подкатегории, которые точно не про облик кампуса
SKIP_SUBCAT = re.compile(
    r"\b(people|alumni|faculty members|staff|rectors?|presidents?|professors?|logos?|"
    r"seals?|coats? of arms|maps?|documents?|videos?|audio|sounds?|diplomas?|"
    r"publications?|books?|awards?|stamps?|portraits?|personalit|signatures?|"
    r"выпускник|преподавател|ректор|логотип|карты|документ)",
    re.I,
)

# Подкатегории, которые стоит обойти в первую очередь
PRIORITY_SUBCAT = re.compile(
    r"librar|bibliot|библиотек|dormitor|residence|hostel|общежит|campus|кампус|"
    r"building|здани|корпус|lecture|auditor|laborator|аудитор|student|студент|"
    r"sport|stadium|interior|интерьер",
    re.I,
)


def _file_params(extra: dict) -> dict:
    params = {
        "action": "query",
        "prop": "imageinfo",
        "iiprop": "url|size|mime|sha1|extmetadata",
        "iiurlwidth": config.THUMB_WIDTH,
        "iiextmetadatafilter": EXTMETA,
        "iiextmetadatalanguage": "en",
        "format": "json",
        "formatversion": 2,
    }
    params.update(extra)
    return params


def parse_file_page(page: dict, hit: SourceHit) -> Optional[Candidate]:
    infos = page.get("imageinfo") or []
    if not infos:
        return None
    info = infos[0]
    meta = info.get("extmetadata", {}) or {}

    def mv(name: str) -> str:
        return (meta.get(name) or {}).get("value", "") or ""

    title = page.get("title", "").removeprefix("File:")
    lat = to_float(mv("GPSLatitude"))
    lon = to_float(mv("GPSLongitude"))
    coords = page.get("coordinates") or []
    if (lat is None or lon is None) and coords:
        lat, lon = coords[0].get("lat"), coords[0].get("lon")

    date = strip_html(mv("DateTimeOriginal"))[:40] or None
    return Candidate(
        key=normalize(title),
        title=title,
        thumb_url=info.get("thumburl") or info.get("url", ""),
        full_url=info.get("url", ""),
        page_url=info.get("descriptionurl") or commons_file_url(title),
        width=int(info.get("width") or 0),
        height=int(info.get("height") or 0),
        mime=info.get("mime", ""),
        sha1=info.get("sha1"),
        description=strip_html(mv("ImageDescription") or mv("ObjectName"))[:600],
        categories=strip_html(mv("Categories")),
        author=strip_html(mv("Artist"))[:120],
        license=strip_html(mv("LicenseShortName")),
        license_url=mv("LicenseUrl"),
        lat=lat,
        lon=lon,
        date=date,
        sources=[hit],
    )


async def _files_in_category(fetcher, category: str, limit: int, hit_type: str, label: str, detail: str = "") -> list[Candidate]:
    data = await fetcher.get_json(
        COMMONS_API,
        _file_params(
            {
                "generator": "categorymembers",
                "gcmtitle": f"Category:{category}",
                "gcmtype": "file",
                "gcmlimit": limit,
                "gcmsort": "timestamp",
                "gcmdir": "desc",  # сначала свежие загрузки
            }
        ),
    )
    url = "https://commons.wikimedia.org/wiki/Category:" + category.replace(" ", "_")
    out = []
    for page in data.get("query", {}).get("pages", []):
        cand = parse_file_page(page, SourceHit(hit_type, label, url, detail))
        if cand:
            out.append(cand)
    return out


async def _subcategories(fetcher, category: str) -> list[str]:
    data = await fetcher.get_json(
        COMMONS_API,
        {
            "action": "query",
            "list": "categorymembers",
            "cmtitle": f"Category:{category}",
            "cmtype": "subcat",
            "cmlimit": 60,
            "format": "json",
            "formatversion": 2,
        },
    )
    names = [m["title"].removeprefix("Category:") for m in data.get("query", {}).get("categorymembers", [])]
    names = [n for n in names if not SKIP_SUBCAT.search(n)]
    names.sort(key=lambda n: 0 if PRIORITY_SUBCAT.search(n) else 1)
    return names[: config.MAX_SUBCATEGORIES]


async def from_category(fetcher, category: str) -> list[Candidate]:
    """Файлы из категории вуза и её подкатегорий (один уровень)."""
    direct_task = asyncio.create_task(
        _files_in_category(fetcher, category, 50, "commons_category", "Wikimedia Commons: категория вуза")
    )
    try:
        subcats = await _subcategories(fetcher, category)
    except SourceError:
        subcats = []

    sub_tasks = [
        _files_in_category(fetcher, sc, 20, "commons_subcategory", "Wikimedia Commons: подкатегория вуза", sc)
        for sc in subcats
    ]
    results = await asyncio.gather(direct_task, *sub_tasks, return_exceptions=True)
    if isinstance(results[0], Exception):
        raise results[0]
    out: list[Candidate] = []
    for r in results:
        if isinstance(r, list):
            out.extend(r)
    return out


async def from_geosearch(fetcher, lat: float, lon: float, radius: int, limit: int,
                         hit_type: str, label: str) -> list[Candidate]:
    data = await fetcher.get_json(
        COMMONS_API,
        _file_params(
            {
                "generator": "geosearch",
                "ggscoord": f"{lat}|{lon}",
                "ggsradius": radius,
                "ggslimit": limit,
                "ggsnamespace": 6,
                "prop": "imageinfo|coordinates",
            }
        ),
    )
    out = []
    for page in data.get("query", {}).get("pages", []):
        cand = parse_file_page(page, SourceHit(hit_type, label, "", ""))
        if not cand:
            continue
        if cand.lat is not None and cand.lon is not None:
            d = haversine_m(lat, lon, cand.lat, cand.lon)
            cand.sources[0].detail = f"{human_distance(d)} от точки поиска"
        cand.sources[0].url = cand.page_url
        out.append(cand)
    return out


async def single_file(fetcher, filename: str, hit_type: str, label: str, url: str) -> list[Candidate]:
    data = await fetcher.get_json(COMMONS_API, _file_params({"titles": f"File:{filename}"}))
    out = []
    for page in data.get("query", {}).get("pages", []):
        cand = parse_file_page(page, SourceHit(hit_type, label, url))
        if cand:
            out.append(cand)
    return out
