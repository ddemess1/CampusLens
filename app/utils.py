from __future__ import annotations

import html
import math
import re
import unicodedata

_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")


def strip_html(value: str | None) -> str:
    if not value:
        return ""
    text = _TAG_RE.sub(" ", value)
    text = html.unescape(text)
    return _SPACE_RE.sub(" ", text).strip()


def normalize(text: str) -> str:
    """Нижний регистр, без диакритики и пунктуации, ё→е."""
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("ё", "е")
    text = re.sub(r"[_\-–—/.,:;()«»\"'’`]+", " ", text)
    return _SPACE_RE.sub(" ", text).strip()


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def to_float(value) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def human_distance(meters: float) -> str:
    if meters < 1000:
        return f"{int(round(meters))} м"
    return f"{meters / 1000:.1f} км".replace(".", ",")


def commons_file_url(filename: str) -> str:
    return "https://commons.wikimedia.org/wiki/File:" + filename.replace(" ", "_")
