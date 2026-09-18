"""Шаг 5. Распределение по категориям.

Базовый слой: многоязычные ключевые слова в названии подкатегории, названии
файла, категориях Commons и описании. Если подключён AI-анализ изображения,
его ответ используется, когда метаданные молчат или ему противоречат слабо.
"""
from __future__ import annotations

import re

from .models import CATEGORY_LABELS, Candidate

KEYWORDS: dict[str, list[str]] = {
    "library": [
        r"librar", r"bibliot", r"библиотек", r"кітапхана", r"reading room", r"читальн",
        r"bücherei", r"mediath",
    ],
    "dormitory": [
        r"dormitor", r"\bdorms?\b", r"residence hall", r"halls? of residence",
        r"student (housing|residence|village|hostel|accommodation)", r"wohnheim",
        r"общежит", r"жатақхана", r"cit[eé] universitaire", r"residencia", r"\bhostel",
        r"дом студентов",
    ],
    "education": [
        r"lecture", r"auditori", r"classroom", r"laborator", r"\blabs?\b",
        r"seminar room", r"hörsaal", r"hoersaal", r"аудитори", r"лаборатор",
        r"учебн\w* (класс|аудитор|корпус)", r"лекци", r"дәрісхана", r"computer (room|lab|class)",
        r"study (room|space|hall)", r"coworking", r"коворкинг", r"makerspace",
    ],
    "student_life": [
        r"\bstudents?\b", r"студент", r"graduat", r"выпускн", r"convocation",
        r"commencement", r"festival", r"фестивал", r"concert", r"концерт", r"\bsports?\b",
        r"stadium", r"стадион", r"спорт", r"\bgym", r"canteen", r"cafeteria",
        r"dining hall", r"столов", r"\bclubs?\b", r"orientation", r"ceremony",
        r"церемони", r"volunteer", r"волонт", r"hackathon", r"хакатон", r"празднован",
    ],
    "campus": [
        r"campus", r"кампус", r"main building", r"главн\w* (здани|корпус)",
        r"\bbuilding", r"здани", r"корпус", r"ғимарат", r"entrance", r"\bвход",
        r"fa[cç]ade", r"фасад", r"aerial", r"\bquad", r"courtyard", r"\bgate",
        r"ворот", r"tower", r"башн", r"\bhall\b", r"rectorate", r"ректорат",
    ],
    "city": [
        r"skyline", r"panorama", r"cityscape", r"downtown", r"old town", r"street",
        r"\bулиц", r"square", r"площад", r"embankment", r"набережн", r"\bpark\b",
    ],
}

COMPILED = {cat: [re.compile(p, re.I) for p in pats] for cat, pats in KEYWORDS.items()}

# Более конкретные категории выигрывают при равенстве
PRIORITY = ["library", "dormitory", "education", "student_life", "campus", "city"]

FIELD_WEIGHTS = (("subcat", 3.0), ("title", 2.0), ("categories", 1.5), ("description", 1.0))


def keyword_scores(cand: Candidate) -> dict[str, float]:
    subcat = " ".join(s.detail for s in cand.sources if s.type == "commons_subcategory")
    fields = {
        "subcat": subcat,
        "title": cand.title,
        "categories": cand.categories,
        "description": cand.description,
    }
    scores = {c: 0.0 for c in KEYWORDS}
    for fname, weight in FIELD_WEIGHTS:
        text = fields[fname]
        if not text:
            continue
        for cat, patterns in COMPILED.items():
            if any(p.search(text) for p in patterns):
                scores[cat] += weight
    return scores


def categorize(cand: Candidate) -> None:
    if cand.subject == "city":
        cand.category = "city"
        cand.category_reason = "Фото из центра города (геопоиск/Wikidata города)"
        return

    scores = keyword_scores(cand)
    # «Город» для фото из выборок вуза ставим только по AI-анализу:
    # улица рядом с корпусом по ключевому слову не должна становиться «Городом»
    scores["city"] = 0.0
    best = max(PRIORITY, key=lambda c: (scores[c], -PRIORITY.index(c)))
    vision_cat = (cand.vision or {}).get("category")

    if scores[best] > 0:
        cand.category = best
        cand.category_reason = f"Ключевые слова в метаданных → «{CATEGORY_LABELS[best]}»"
        weak = scores[best] < 2.0
        generic = best == "campus" and vision_cat not in (None, "city")  # «кампус» — самая общая категория
        if vision_cat in CATEGORY_LABELS and vision_cat != best and (weak or generic):
            cand.category = vision_cat
            cand.category_reason = (
                f"AI-анализ изображения → «{CATEGORY_LABELS[vision_cat]}» "
                f"(метаданные слабо указывали на «{CATEGORY_LABELS[best]}»)"
            )
        return

    if vision_cat in CATEGORY_LABELS:
        cand.category = vision_cat
        cand.category_reason = f"AI-анализ изображения → «{CATEGORY_LABELS[vision_cat]}»"
        return

    cand.category = "campus"
    cand.category_reason = "Признаков категории нет; фото вуза отнесено к «Кампус» по умолчанию"
