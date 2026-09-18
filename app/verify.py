"""Шаг 3. Проверка принадлежности.

Показатель достоверности — это сумма объяснимых сигналов, а не «магическое»
число. Каждый сигнал сохраняется в reasons и показывается пользователю.
"""
from __future__ import annotations

import re
from datetime import date

from .models import Candidate, Reason, University
from .utils import haversine_m, human_distance, normalize

ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}

JUNK_RE = re.compile(
    r"\b(logo|logotype|emblem|seal|coat of arms|flag|map|maps|plan|floor ?plan|diagram|"
    r"chart|graph|signature|diploma|certificate|document|stamps? of|postage stamp|coin|"
    r"banknote|portrait|headshot|screenshot|poster|infographic|"
    r"герб|логотип|эмблем\w*|флаг|карта|схема|план здания|диплом|марка|портрет|скриншот)\b",
    re.I,
)
JUNK_CAT_RE = re.compile(r"\b(logos|coats of arms|maps of|diagrams|seals of|signatures|scans)\b", re.I)
PEOPLE_CAT_RE = re.compile(r"\b(people of|alumni of|portraits|faculty of .* \(people\))", re.I)

# Слова, которые есть в названии почти любого вуза и ничего не доказывают
GENERIC_WORDS = set(
    """
    university universitat universite universidad universita college institute institut
    of the and for in at de del la le der die das national state federal public private
    technical technological polytechnic research academy school higher education
    университет университеті государственный национальный федеральный технический
    технологический политехнический институт академия имени им и высшая школа
    исследовательский казахский российский мемлекеттік ұлттық
    """.split()
)

SOURCE_BASE = {
    "wikidata_p18": (0.50, "Главное фото вуза в Wikidata (P18)"),
    "commons_category": (0.40, "Файл лежит в категории вуза на Commons"),
    "commons_subcategory": (0.40, "Файл в подкатегории вуза на Commons"),
    "official_site": (0.50, "Изображение с официального сайта (адрес из Wikidata P856)"),
    "commons_geo": (0.10, "Файл с геометкой рядом с кампусом"),
    "city_p18": (0.60, "Главное фото города в Wikidata (P18)"),
    "city_geo": (0.35, "Файл с геометкой в центре города"),
}


def filter_reason(cand: Candidate) -> str | None:
    """Причина исключения или None, если кандидат годится."""
    if cand.mime and cand.mime not in ALLOWED_MIME:
        return "не фотография (svg, pdf, видео и т.п.)"
    if cand.sources[0].type != "official_site":
        if not cand.license:
            return "нет открытой лицензии"
        if cand.width and (cand.width < 500 or cand.height < 300):
            return "слишком маленькое изображение"
    if cand.width and cand.height:
        ratio = cand.width / cand.height
        if ratio > 5 or ratio < 0.25:
            return "нетипичные пропорции (баннер/скан)"
    if JUNK_RE.search(cand.title) or JUNK_CAT_RE.search(cand.categories):
        return "логотип, карта, документ или портрет"
    if PEOPLE_CAT_RE.search(cand.categories):
        return "фото персоналий, а не места"
    return None


def distinctive_names(uni: University) -> tuple[set[str], list[set[str]]]:
    """Полные нормализованные названия и наборы «отличительных» слов."""
    city_tokens = set(normalize(uni.city_name or "").split())
    full, token_sets = set(), []
    for name in uni.names:
        n = normalize(name)
        if not n:
            continue
        full.add(n)
        tokens = {t for t in n.split() if len(t) >= 3 and t not in GENERIC_WORDS and t not in city_tokens}
        if tokens:
            token_sets.append(tokens)
    return full, token_sets


def text_mentions_university(cand: Candidate, uni: University) -> bool:
    text = normalize(f"{cand.title} {cand.description} {cand.categories} "
                     + " ".join(s.detail for s in cand.sources))
    padded = f" {text} "
    full, token_sets = distinctive_names(uni)
    for n in full:
        # аббревиатуры (МГУ, KBTU) ищем как отдельные слова, полные названия — как подстроки
        if (len(n) <= 6 and f" {n} " in padded) or (len(n) > 6 and n in text):
            return True
    words = set(text.split())
    for tokens in token_sets:
        need = min(2, len(tokens))
        if len(tokens & words) >= need:
            return True
    return False


def _year(value: str | None) -> int | None:
    if not value:
        return None
    m = re.search(r"(19|20)\d{2}", value)
    return int(m.group(0)) if m else None


def score(cand: Candidate, uni: University) -> None:
    reasons: list[Reason] = []
    types = {s.type for s in cand.sources}

    best_type = max(types, key=lambda t: SOURCE_BASE.get(t, (0, ""))[0])
    base, text = SOURCE_BASE.get(best_type, (0.1, "Неизвестный источник"))
    if best_type == "commons_subcategory":
        detail = next(s.detail for s in cand.sources if s.type == "commons_subcategory")
        text = f"Файл в подкатегории вуза «{detail}»"
    reasons.append(Reason(text, base))

    if len(types) > 1:
        reasons.append(Reason(f"Найден сразу в {len(types)} независимых выборках", 0.05))

    is_city = cand.subject == "city"

    # Геометка
    if cand.lat is not None and cand.lon is not None:
        if is_city and uni.city_lat is not None:
            d = haversine_m(uni.city_lat, uni.city_lon, cand.lat, cand.lon)
            if d <= 3000:
                reasons.append(Reason(f"Снято в {human_distance(d)} от центра города", 0.15))
            elif d > 30000:
                reasons.append(Reason(f"Снято в {human_distance(d)} от центра — другое место", -0.4))
        elif not is_city and uni.lat is not None:
            d = haversine_m(uni.lat, uni.lon, cand.lat, cand.lon)
            if d <= 300:
                reasons.append(Reason(f"Геометка в {human_distance(d)} от координат кампуса", 0.25))
            elif d <= 1000:
                reasons.append(Reason(f"Геометка в {human_distance(d)} от координат кампуса", 0.18))
            elif d <= 3000:
                reasons.append(Reason(f"Геометка в {human_distance(d)} от кампуса", 0.08))
            elif d > 15000:
                reasons.append(Reason(
                    f"Снято в {human_distance(d)} от кампуса — возможно, другой корпус или место", -0.35))

    # Упоминание в тексте
    if is_city:
        if uni.city_name and normalize(uni.city_name) in normalize(
                f"{cand.title} {cand.description} {cand.categories}"):
            reasons.append(Reason("Название города есть в описании файла", 0.15))
    elif best_type != "official_site":
        if text_mentions_university(cand, uni):
            reasons.append(Reason("Название вуза есть в названии, описании или категориях файла", 0.20))
        elif best_type == "commons_geo":
            reasons.append(Reason("Вуз не упомянут в описании — только близость по карте", -0.05))

    # Лицензия
    if cand.license and best_type != "official_site":
        reasons.append(Reason(f"Открытая лицензия: {cand.license}", 0.05))

    # Свежесть
    year = _year(cand.date)
    if year:
        age = date.today().year - year
        if age <= 8:
            reasons.append(Reason(f"Снимок {year} года", 0.05))
        elif age > 15:
            reasons.append(Reason(f"Снимок {year} года — может быть устаревшим", -0.08))

    # AI-проверка изображения (если включена)
    v = cand.vision
    if v:
        if v.get("is_photo") is False:
            reasons.append(Reason("AI: это не фотография места (графика, скан, схема)", -0.5))
        elif v.get("relevant") is True:
            reasons.append(Reason(f"AI: на фото {v.get('note') or 'университетская среда'}", 0.15))
        elif v.get("relevant") is False:
            reasons.append(Reason(f"AI: не похоже на {'город' if is_city else 'вуз'} ({v.get('note') or 'нерелевантно'})", -0.45))

    total = sum(r.delta for r in reasons)
    cand.confidence = max(0.02, min(0.99, total))
    cand.reasons = reasons
