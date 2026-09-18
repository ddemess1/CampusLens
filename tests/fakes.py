"""Поддельный HTTP-клиент: отвечает заготовками вместо реальных API."""
from __future__ import annotations

import io
import random

from PIL import Image, ImageDraw

from app.fetcher import SourceError

UNI_LAT, UNI_LON = 43.2220, 76.8512
CITY_LAT, CITY_LON = 43.2567, 76.9286


def make_image(seed: int, size=(640, 480)) -> bytes:
    rnd = random.Random(seed)
    img = Image.new("RGB", size, (rnd.randint(0, 255), rnd.randint(0, 255), rnd.randint(0, 255)))
    d = ImageDraw.Draw(img)
    for _ in range(25):
        x, y = rnd.randint(0, size[0]), rnd.randint(0, size[1])
        d.rectangle([x, y, x + rnd.randint(20, 200), y + rnd.randint(20, 200)],
                    fill=(rnd.randint(0, 255), rnd.randint(0, 255), rnd.randint(0, 255)))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def entity(qid, label_ru, desc_en, claims, sitelinks=None, aliases=None, label_en=None):
    return {
        "id": qid,
        "labels": {"ru": {"value": label_ru}, "en": {"value": label_en or label_ru}},
        "descriptions": {"en": {"value": desc_en}},
        "aliases": {"ru": [{"value": a} for a in (aliases or [])]},
        "sitelinks": sitelinks or {},
        "claims": claims,
    }


def item(pid_value):
    return {"mainsnak": {"snaktype": "value", "datavalue": {"value": pid_value}}, "rank": "normal"}


UNI = entity(
    "Q1", "Казахский национальный университет имени аль-Фараби", "public university in Almaty",
    {
        "P31": [item({"id": "Q875538"})],
        "P625": [item({"latitude": UNI_LAT, "longitude": UNI_LON})],
        "P856": [item("https://www.kaznu.kz")],
        "P373": [item("Al-Farabi Kazakh National University")],
        "P18": [item("KazNU main building.jpg")],
        "P17": [item({"id": "Q232"})],
        "P131": [item({"id": "Q35493"})],
    },
    sitelinks={"ruwiki": {"title": "Казахский национальный университет"}},
    aliases=["КазНУ"],
    label_en="Al-Farabi Kazakh National University",
)
NOT_UNI = entity("Q2", "Аль-Фараби", "philosopher", {"P31": [item({"id": "Q5"})]})
COUNTRY = entity("Q232", "Казахстан", "country", {})
CITY = entity(
    "Q35493", "Алматы", "city in Kazakhstan",
    {
        "P31": [item({"id": "Q515"})],
        "P625": [item({"latitude": CITY_LAT, "longitude": CITY_LON})],
        "P18": [item("Almaty panorama.jpg")],
    },
)
ENTITIES = {e["id"]: e for e in [UNI, NOT_UNI, COUNTRY, CITY]}


def file_page(title, *, lat=None, lon=None, desc="", cats="", sha1=None, mime="image/jpeg",
              w=2000, h=1500, license="CC BY-SA 4.0", date="2022-05-01"):
    meta = {
        "ImageDescription": {"value": desc},
        "Artist": {"value": "<a href='x'>Photographer</a>"},
        "LicenseShortName": {"value": license},
        "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0"},
        "Categories": {"value": cats},
        "DateTimeOriginal": {"value": date},
    }
    if lat is not None:
        meta["GPSLatitude"] = {"value": str(lat)}
        meta["GPSLongitude"] = {"value": str(lon)}
    slug = title.replace(" ", "_")
    return {
        "title": "File:" + title,
        "imageinfo": [{
            "url": f"https://upload.example/{slug}",
            "thumburl": f"https://upload.example/thumb/{slug}",
            "descriptionurl": f"https://commons.wikimedia.org/wiki/File:{slug}",
            "width": w, "height": h, "mime": mime,
            "sha1": sha1 or slug,
            "extmetadata": meta,
        }],
    }


CATEGORY_FILES = [
    file_page("KazNU main building.jpg", lat=UNI_LAT, lon=UNI_LON, cats="Al-Farabi Kazakh National University"),
    file_page("KazNU logo.svg", mime="image/svg+xml"),
    file_page("Al-Farabi university lecture hall 2.jpg", cats="Lecture halls"),
    file_page("Al-Farabi university lecture hall 2 copy.jpg", cats="Lecture halls"),  # визуальный дубль
    file_page("Tiny KazNU photo.jpg", w=200, h=150),
    file_page("KazNU entrance old.jpg", date="1998", lat=UNI_LAT + 0.001, lon=UNI_LON),
    file_page("KazNU no license.jpg", license=""),
]
SUBCATS = ["Library of Al-Farabi Kazakh National University", "Rectors of Al-Farabi Kazakh National University",
           "Dormitories of Al-Farabi Kazakh National University"]
SUBCAT_FILES = {
    "Library of Al-Farabi Kazakh National University": [file_page("KazNU reading room.jpg")],
    "Dormitories of Al-Farabi Kazakh National University": [file_page("Hostel 5 building.jpg")],
}
GEO_CAMPUS = [
    {**file_page("KazNU main building.jpg", lat=UNI_LAT, lon=UNI_LON), "coordinates": [{"lat": UNI_LAT, "lon": UNI_LON}]},
    {**file_page("Random shop near Timiryazev street.jpg", lat=UNI_LAT + 0.002, lon=UNI_LON),
     "coordinates": [{"lat": UNI_LAT + 0.002, "lon": UNI_LON}]},
]
GEO_CITY = [
    {**file_page("Almaty old square.jpg", lat=CITY_LAT, lon=CITY_LON, desc="Old square in Almaty"),
     "coordinates": [{"lat": CITY_LAT, "lon": CITY_LON}]},
]

OFFICIAL_HTML = """<html><head><title>KazNU — official</title>
<meta content="/img/logo.png" property="og:image">
<meta property="og:image" content="https://www.kaznu.kz/img/campus-photo.jpg">
</head><body></body></html>"""

IMAGES = {}
for i, page in enumerate(CATEGORY_FILES + GEO_CAMPUS + GEO_CITY
                         + [p for v in SUBCAT_FILES.values() for p in v]
                         + [file_page("Almaty panorama.jpg")]):
    IMAGES.setdefault(page["imageinfo"][0]["thumburl"], make_image(i))
# визуальная копия лекционной аудитории (тот же рисунок, другое сжатие)
_orig = Image.open(io.BytesIO(IMAGES["https://upload.example/thumb/Al-Farabi_university_lecture_hall_2.jpg"]))
_buf = io.BytesIO()
_orig.save(_buf, format="JPEG", quality=55)
IMAGES["https://upload.example/thumb/Al-Farabi_university_lecture_hall_2_copy.jpg"] = _buf.getvalue()
IMAGES["https://www.kaznu.kz/img/campus-photo.jpg"] = make_image(99, (1200, 630))


class FakeFetcher:
    def __init__(self, fail_hosts=(), unknown_query=False):
        self.fail_hosts = set(fail_hosts)
        self.unknown_query = unknown_query
        self.calls = []

    def _check(self, url):
        host = url.split("/")[2]
        if host in self.fail_hosts:
            raise SourceError(f"HTTP 503 от {host}")

    async def get_json(self, url, params=None):
        self._check(url)
        p = params or {}
        self.calls.append((url, p))
        if "wikidata" in url:
            if p["action"] == "wbsearchentities":
                return {"search": [] if self.unknown_query else [{"id": "Q2"}, {"id": "Q1"}]}
            if p["action"] == "wbgetentities":
                ids = p["ids"].split("|")
                return {"entities": {i: ENTITIES[i] for i in ids if i in ENTITIES}}
        if "wikipedia" in url:
            return {"query": {"search": [], "searchinfo": {"suggestion": "kaznu" if self.unknown_query else None}}}
        if "commons" in url:
            if p.get("list") == "categorymembers":
                return {"query": {"categorymembers": [{"title": "Category:" + s} for s in SUBCATS]}}
            if p.get("generator") == "categorymembers":
                cat = p["gcmtitle"].removeprefix("Category:")
                pages = CATEGORY_FILES if cat == "Al-Farabi Kazakh National University" else SUBCAT_FILES.get(cat, [])
                return {"query": {"pages": pages}}
            if p.get("generator") == "geosearch":
                lat = float(p["ggscoord"].split("|")[0])
                return {"query": {"pages": GEO_CAMPUS if abs(lat - UNI_LAT) < 0.01 else GEO_CITY}}
            if "titles" in p:
                name = p["titles"].removeprefix("File:")
                return {"query": {"pages": [file_page(name, lat=UNI_LAT if "KazNU" in name else CITY_LAT,
                                                      lon=UNI_LON if "KazNU" in name else CITY_LON)]}}
        raise SourceError("unexpected call " + url)

    async def get_text(self, url, max_bytes=0, timeout=0):
        self._check(url)
        return OFFICIAL_HTML

    async def get_bytes(self, url, max_bytes=0, timeout=None):
        self._check(url)
        if url not in IMAGES:
            raise SourceError("404")
        return IMAGES[url]

    async def post_json(self, url, payload, headers=None, timeout=0):
        raise SourceError("no vision in tests")
