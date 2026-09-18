"""Шаг 1. Находим университет по названию.

Wikidata даёт однозначный идентификатор (QID) и проверенные факты: координаты
кампуса (P625), официальный сайт (P856), категорию на Commons (P373), главное
фото (P18), город (P131), страну (P17).
"""
from __future__ import annotations

import asyncio
import re
from typing import Optional

from .fetcher import SourceError
from .models import University
from .utils import haversine_m, normalize

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
LANGS = ["ru", "en", "kk"]

# Классы Wikidata, которые считаем «вузом»
UNIVERSITY_CLASSES = {
    "Q3918",       # university
    "Q875538",     # public university
    "Q902104",     # private university
    "Q15936437",   # research university
    "Q38723",      # higher education institution
    "Q1371037",    # institute of technology
    "Q62078547",   # public research university
    "Q3354859",    # collegiate university
    "Q189004",     # college
    "Q23002054",   # private not-for-profit educational institution
}

UNIVERSITY_WORDS = re.compile(
    r"universit|университет|университеті|институт|академи|college|colleg|"
    r"hochschule|école|ecole|politecnic|polytechn|политехн|institute of technology|"
    r"higher education|высш\w* учебн|жоғары оқу",
    re.I,
)

CITY_CLASSES = {
    "Q515", "Q1549591", "Q1637706", "Q200250", "Q5119", "Q3957",
    "Q7930989", "Q1093829", "Q183342", "Q1115575", "Q2074737",
}


class NotFound(Exception):
    def __init__(self, message: str, alternatives: list[dict] | None = None, suggestion: str | None = None):
        super().__init__(message)
        self.alternatives = alternatives or []
        self.suggestion = suggestion


def _claim_values(entity: dict, prop: str) -> list:
    out = []
    for claim in entity.get("claims", {}).get(prop, []):
        snak = claim.get("mainsnak", {})
        if snak.get("snaktype") != "value":
            continue
        if claim.get("rank") == "deprecated":
            continue
        out.append(snak.get("datavalue", {}).get("value"))
    return out


def _first(entity: dict, prop: str):
    vals = _claim_values(entity, prop)
    return vals[0] if vals else None


def _label(entity: dict) -> str:
    labels = entity.get("labels", {})
    for lang in LANGS:
        if lang in labels:
            return labels[lang]["value"]
    if labels:
        return next(iter(labels.values()))["value"]
    return entity.get("id", "")


def _description(entity: dict) -> str:
    descs = entity.get("descriptions", {})
    for lang in LANGS:
        if lang in descs:
            return descs[lang]["value"]
    return ""


def _all_descriptions(entity: dict) -> str:
    return " ".join(d.get("value", "") for d in entity.get("descriptions", {}).values())


def is_university(entity: dict) -> bool:
    classes = {v.get("id") for v in _claim_values(entity, "P31") if isinstance(v, dict)}
    if classes & UNIVERSITY_CLASSES:
        return True
    return bool(UNIVERSITY_WORDS.search(_all_descriptions(entity)))


def _ids_of(entity: dict, prop: str) -> list[str]:
    return [v["id"] for v in _claim_values(entity, prop) if isinstance(v, dict) and "id" in v]


async def _search_wikidata(fetcher, query: str) -> list[str]:
    async def one(lang: str) -> list[str]:
        try:
            data = await fetcher.get_json(
                WIKIDATA_API,
                {
                    "action": "wbsearchentities",
                    "search": query,
                    "language": lang,
                    "uselang": lang,
                    "type": "item",
                    "limit": 10,
                    "format": "json",
                },
            )
        except SourceError:
            return []
        return [r["id"] for r in data.get("search", [])]

    results = await asyncio.gather(*(one(lang) for lang in ["ru", "en"]))
    seen, ordered = set(), []
    # чередуем результаты двух языков, сохраняя порядок релевантности
    for pair in zip(*[r + [None] * (10 - len(r)) for r in results]):
        for qid in pair:
            if qid and qid not in seen:
                seen.add(qid)
                ordered.append(qid)
    return ordered


async def _search_wikipedia(fetcher, query: str) -> tuple[list[str], Optional[str]]:
    """Полнотекстовый поиск Википедии терпимее к опечаткам и даёт подсказку."""
    suggestion = None
    qids: list[str] = []
    for lang in ["ru", "en"]:
        api = f"https://{lang}.wikipedia.org/w/api.php"
        try:
            data = await fetcher.get_json(
                api,
                {
                    "action": "query",
                    "list": "search",
                    "srsearch": query,
                    "srlimit": 5,
                    "srinfo": "suggestion",
                    "format": "json",
                    "formatversion": 2,
                },
            )
            titles = [r["title"] for r in data.get("query", {}).get("search", [])]
            suggestion = suggestion or data.get("query", {}).get("searchinfo", {}).get("suggestion")
            if not titles and suggestion:
                data = await fetcher.get_json(
                    api,
                    {"action": "query", "list": "search", "srsearch": suggestion,
                     "srlimit": 5, "format": "json", "formatversion": 2},
                )
                titles = [r["title"] for r in data.get("query", {}).get("search", [])]
            if not titles:
                continue
            props = await fetcher.get_json(
                api,
                {"action": "query", "prop": "pageprops", "ppprop": "wikibase_item",
                 "titles": "|".join(titles), "redirects": 1, "format": "json", "formatversion": 2},
            )
            by_title = {
                p["title"]: p.get("pageprops", {}).get("wikibase_item")
                for p in props.get("query", {}).get("pages", [])
            }
            for t in titles:
                q = by_title.get(t)
                if q and q not in qids:
                    qids.append(q)
        except SourceError:
            continue
        if qids:
            break
    return qids, suggestion


async def get_entities(fetcher, qids: list[str]) -> dict[str, dict]:
    if not qids:
        return {}
    data = await fetcher.get_json(
        WIKIDATA_API,
        {
            "action": "wbgetentities",
            "ids": "|".join(qids[:50]),
            "props": "labels|descriptions|claims|sitelinks|aliases",
            "languages": "|".join(LANGS),
            "format": "json",
        },
    )
    return data.get("entities", {})


def _sort_key(query: str, entity: dict, position: int) -> tuple:
    nq = normalize(query)
    names = [normalize(_label(entity))] + [
        normalize(a["value"]) for vals in entity.get("aliases", {}).values() for a in vals
    ]
    exact = 0 if nq in names else 1
    richness = -(
        bool(_first(entity, "P373")) + bool(_first(entity, "P625")) + bool(_first(entity, "P18"))
    )
    popularity = -len(entity.get("sitelinks", {}))
    return (exact, richness, position, popularity)


def _alt(entity: dict) -> dict:
    return {"qid": entity["id"], "name": _label(entity), "description": _description(entity)}


async def resolve(fetcher, query: str) -> tuple[University, list[dict]]:
    """Возвращает университет и список альтернатив («Возможно, вы искали…»)."""
    query = query.strip()
    suggestion = None
    if re.fullmatch(r"Q\d+", query):
        qids = [query]
    else:
        qids = await _search_wikidata(fetcher, query)

    try:
        entities = await get_entities(fetcher, qids[:20])
    except SourceError as exc:
        raise SourceError(f"Wikidata недоступна: {exc}") from exc

    valid = [(i, entities[q]) for i, q in enumerate(qids) if q in entities and is_university(entities[q])]

    if not valid and not re.fullmatch(r"Q\d+", query):
        wp_qids, suggestion = await _search_wikipedia(fetcher, query)
        if wp_qids:
            entities = await get_entities(fetcher, wp_qids[:20])
            valid = [(i, entities[q]) for i, q in enumerate(wp_qids) if q in entities and is_university(entities[q])]

    if not valid:
        raise NotFound(
            "Не нашли университет с таким названием. Проверьте написание или укажите город.",
            suggestion=suggestion,
        )

    valid.sort(key=lambda pair: _sort_key(query, pair[1], pair[0]))
    best = valid[0][1]
    alternatives = [_alt(e) for _, e in valid[1:5]]
    uni = await build_university(fetcher, best)
    return uni, alternatives


async def build_university(fetcher, entity: dict) -> University:
    coords = _first(entity, "P625")
    names = {_label(entity)}
    for lang_vals in entity.get("labels", {}).values():
        names.add(lang_vals["value"])
    for vals in entity.get("aliases", {}).values():
        for a in vals:
            names.add(a["value"])

    sitelinks = entity.get("sitelinks", {})
    wiki_url = None
    for lang in ["ru", "en", "kk"]:
        link = sitelinks.get(f"{lang}wiki")
        if link:
            wiki_url = f"https://{lang}.wikipedia.org/wiki/" + link["title"].replace(" ", "_")
            break

    uni = University(
        qid=entity["id"],
        name=_label(entity),
        description=_description(entity),
        names=sorted(n for n in names if n),
        lat=coords.get("latitude") if isinstance(coords, dict) else None,
        lon=coords.get("longitude") if isinstance(coords, dict) else None,
        website=_first(entity, "P856"),
        commons_category=_first(entity, "P373"),
        image=_first(entity, "P18"),
        wikipedia_url=wiki_url,
    )

    # Страна и город: получаем подписи и поднимаемся по P131 до уровня города
    country_ids = _ids_of(entity, "P17")[:1]
    located = _ids_of(entity, "P131")[:1]
    try:
        await _attach_place(fetcher, uni, country_ids, located)
    except SourceError:
        pass
    return uni


async def _attach_place(fetcher, uni: University, country_ids: list[str], located: list[str]) -> None:
    ents = await get_entities(fetcher, country_ids + located)
    if country_ids and country_ids[0] in ents:
        uni.country = _label(ents[country_ids[0]])

    current = ents.get(located[0]) if located else None
    for _ in range(3):
        if current is None:
            return
        classes = set(_ids_of(current, "P31"))
        population = _first(current, "P1082")
        pop = 0.0
        if isinstance(population, dict):
            try:
                pop = float(population.get("amount", "0"))
            except ValueError:
                pop = 0.0
        if classes & CITY_CLASSES or pop >= 50_000:
            break
        parents = _ids_of(current, "P131")
        if not parents:
            break
        current = (await get_entities(fetcher, parents[:1])).get(parents[0])
    if current is None:
        return

    uni.city_qid = current["id"]
    uni.city_name = _label(current)
    c = _first(current, "P625")
    if isinstance(c, dict):
        uni.city_lat, uni.city_lon = c.get("latitude"), c.get("longitude")
    uni.city_image = _first(current, "P18")
    if None not in (uni.lat, uni.lon, uni.city_lat, uni.city_lon):
        uni.distance_to_center_km = round(
            haversine_m(uni.lat, uni.lon, uni.city_lat, uni.city_lon) / 1000, 1
        )
