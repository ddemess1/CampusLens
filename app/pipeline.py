"""Оркестратор. Все источники опрашиваются параллельно, у каждого этапа есть
бюджет времени, падение одного источника не ломает профиль."""
from __future__ import annotations

import asyncio
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Awaitable, Callable

from . import config, vision
from .categorize import categorize
from .dedup import dhash, image_info, merge_same_files, remove_duplicates
from .fetcher import SourceError
from .models import CATEGORIES, CATEGORY_LABELS, Candidate, University
from .resolver import resolve
from .sources import commons, official
from .utils import normalize
from .verify import filter_reason, score

Emit = Callable[[str, str, str], Awaitable[None]]

_profile_cache: dict[str, tuple[float, dict]] = {}
_query_cache: dict[str, tuple[float, str]] = {}


async def _noop(stage: str, state: str, detail: str) -> None:
    return None


def _remaining(deadline: float) -> float:
    return deadline - time.monotonic()


async def _collect(fetcher, uni: University, deadline: float) -> tuple[list[Candidate], list[dict]]:
    jobs: list[tuple[str, Awaitable]] = []
    if uni.image:
        jobs.append(("Wikidata: главное фото вуза", commons.single_file(
            fetcher, uni.image, "wikidata_p18", "Wikidata: главное фото вуза",
            f"https://www.wikidata.org/wiki/{uni.qid}")))
    if uni.commons_category:
        jobs.append(("Commons: категория вуза", commons.from_category(fetcher, uni.commons_category)))
    if uni.lat is not None:
        jobs.append(("Commons: геопоиск у кампуса", commons.from_geosearch(
            fetcher, uni.lat, uni.lon, config.CAMPUS_GEO_RADIUS_M, 40,
            "commons_geo", "Wikimedia Commons: геопоиск у кампуса")))
    if config.INCLUDE_OFFICIAL_SITE and uni.website:
        jobs.append(("Официальный сайт", official.from_official_site(fetcher, uni.website)))
    if uni.city_image and uni.city_qid:
        jobs.append(("Wikidata: фото города", commons.single_file(
            fetcher, uni.city_image, "city_p18", f"Wikidata: главное фото города {uni.city_name}",
            f"https://www.wikidata.org/wiki/{uni.city_qid}")))
    if uni.city_lat is not None:
        jobs.append(("Commons: центр города", commons.from_geosearch(
            fetcher, uni.city_lat, uni.city_lon, config.CITY_GEO_RADIUS_M, 20,
            "city_geo", f"Wikimedia Commons: центр города {uni.city_name}")))

    budget = max(3.0, min(10.0, _remaining(deadline) - 10))

    async def guarded(name: str, job: Awaitable):
        try:
            return name, await asyncio.wait_for(job, budget), None
        except asyncio.TimeoutError:
            return name, [], f"не ответил за {budget:.0f} с"
        except SourceError as exc:
            return name, [], str(exc)
        except Exception as exc:  # источник не должен ронять весь профиль
            return name, [], f"ошибка: {type(exc).__name__}"

    results = await asyncio.gather(*(guarded(n, j) for n, j in jobs))
    cands: list[Candidate] = []
    statuses = []
    for name, items, err in results:
        statuses.append({"name": name, "ok": err is None, "count": len(items), "error": err})
        cands.extend(items)
    return cands, statuses


async def _download_thumbs(fetcher, cands: list[Candidate], deadline: float) -> None:
    budget = max(2.0, _remaining(deadline) - (9 if config.GEMINI_API_KEY else 3))

    async def one(c: Candidate) -> None:
        try:
            data = await fetcher.get_bytes(c.thumb_url, max_bytes=3_000_000, timeout=4.0)
        except SourceError:
            return
        if c.sources[0].type == "official_site":
            c.width, c.height, c.mime = image_info(data)
        c.image_bytes = data
        c.dhash = await asyncio.to_thread(dhash, data)

    tasks = [asyncio.create_task(one(c)) for c in cands]
    done, pending = await asyncio.wait(tasks, timeout=budget)
    for t in pending:
        t.cancel()


def _subject(c: Candidate) -> str:
    return "university" if any(not s.type.startswith("city") for s in c.sources) else "city"


async def build_profile(fetcher, query: str, emit: Emit = _noop) -> dict:
    t0 = time.monotonic()
    deadline = t0 + config.TOTAL_DEADLINE_S
    timings: dict[str, int] = {}

    def mark(stage: str, start: float) -> None:
        timings[stage] = int((time.monotonic() - start) * 1000)

    # 1. Университет
    await emit("resolve", "running", "Ищем вуз в Wikidata")
    s = time.monotonic()
    alternatives: list[dict] = []
    qkey = normalize(query)
    cached_q = _query_cache.get(qkey)
    lookup = cached_q[1] if cached_q and time.time() - cached_q[0] < config.CACHE_TTL_S else query
    uni, alternatives = await resolve(fetcher, lookup)
    _query_cache[qkey] = (time.time(), uni.qid)
    mark("resolve", s)
    await emit("resolve", "done", uni.name)

    cached = _profile_cache.get(uni.qid)
    if cached and time.time() - cached[0] < config.CACHE_TTL_S:
        result = dict(cached[1])
        result["cached"] = True
        result["query"] = query
        if alternatives:
            result["alternatives"] = alternatives
        result["timing"] = {"total_ms": int((time.monotonic() - t0) * 1000), "stages": timings,
                            "original_total_ms": cached[1]["timing"]["total_ms"]}
        for stage in ("search", "verify", "dedup", "categorize"):
            await emit(stage, "done", "из кэша")
        return result

    warnings: list[str] = []
    if not uni.commons_category:
        warnings.append("У вуза нет категории на Wikimedia Commons — проверенных фото будет меньше.")
    if uni.lat is None:
        warnings.append("В Wikidata нет координат кампуса — проверка по геометке недоступна.")

    # 2. Поиск
    await emit("search", "running", "Опрашиваем источники параллельно")
    s = time.monotonic()
    raw, statuses = await _collect(fetcher, uni, deadline)
    for st in statuses:
        if not st["ok"]:
            warnings.append(f"Источник «{st['name']}» недоступен ({st['error']}) — результат может быть неполным.")
    mark("search", s)
    await emit("search", "done", f"{len(raw)} кандидатов из {sum(1 for x in statuses if x['ok'])} источников")

    # 3. Проверка
    await emit("verify", "running", "Фильтры, геометки, лицензии")
    s = time.monotonic()
    merged, same_file = merge_same_files(raw)
    excluded = Counter()
    pool: list[Candidate] = []
    for c in merged:
        c.subject = _subject(c)
        reason = filter_reason(c)
        if reason:
            excluded[reason] += 1
            continue
        score(c, uni)
        pool.append(c)
    pool.sort(key=lambda c: c.confidence, reverse=True)
    uni_pool = [c for c in pool if c.subject == "university"][: config.MAX_HASH_CANDIDATES]
    city_pool = [c for c in pool if c.subject == "city"][:10]
    pool = uni_pool + city_pool
    await _download_thumbs(fetcher, pool, deadline)

    checked: list[Candidate] = []
    for c in pool:
        if c.sources[0].type == "official_site":
            if not c.image_bytes:
                excluded["изображение с сайта не открылось"] += 1
                continue
            reason = filter_reason(c)
            if reason or c.width < 400:
                excluded[reason or "слишком маленькое изображение"] += 1
                continue
        checked.append(c)
    mark("verify", s)
    await emit("verify", "done", f"{len(checked)} прошли фильтры, {sum(excluded.values())} отсеяно")

    # 4. Дубликаты
    await emit("dedup", "running", "Сравниваем SHA-1 и перцептивные хеши")
    s = time.monotonic()
    checked.sort(key=lambda c: c.confidence, reverse=True)
    unique, dup_count = remove_duplicates(checked)
    mark("dedup", s)
    await emit("dedup", "done", f"убрано дублей: {dup_count + same_file}")

    # 5. AI-проверка и категории
    vision_note = "AI-анализ выключен (нет GEMINI_API_KEY)"
    await emit("categorize", "running", "Распределяем по категориям")
    s = time.monotonic()
    if config.GEMINI_API_KEY:
        remaining = _remaining(deadline) - 1.0
        if remaining > 3:
            order = sorted(unique, key=lambda c: abs(c.confidence - 0.7))
            try:
                n = await asyncio.wait_for(vision.analyze(fetcher, uni, order), remaining)
                vision_note = f"AI-анализ изображений: {n} фото"
            except (asyncio.TimeoutError, SourceError, Exception) as exc:
                vision_note = "AI-анализ не успел или недоступен — использованы только метаданные"
                warnings.append(f"{vision_note} ({type(exc).__name__}).")
        else:
            vision_note = "AI-анализ пропущен: исчерпан бюджет времени"
            warnings.append(vision_note + ".")
    else:
        warnings.append("AI-анализ изображений выключен — категории и проверка только по метаданным.")

    for c in unique:
        if c.vision:
            score(c, uni)
        categorize(c)
        c.image_bytes = None
    mark("categorize", s)
    await emit("categorize", "done", vision_note)

    # 6. Профиль
    visible: list[Candidate] = []
    hidden: list[Candidate] = []
    by_cat: dict[str, list[Candidate]] = {k: [] for k in CATEGORIES}
    for c in sorted(unique, key=lambda c: c.confidence, reverse=True):
        by_cat.setdefault(c.category or "campus", []).append(c)
    for cat, items in by_cat.items():
        shown = [c for c in items if c.status != "unverified"][: config.MAX_PER_CATEGORY]
        visible.extend(shown)
        hidden.extend([c for c in items if c.status == "unverified"][:5])
        if not shown:
            warnings.append(
                f"«{CATEGORY_LABELS[cat]}»: проверенных фото не нашли — посторонние снимки не подставляем.")

    visible.sort(key=lambda c: (CATEGORIES.index(c.category), -c.confidence))
    counts = Counter(c.category for c in visible)
    status_counts = Counter(c.status for c in visible)

    result = {
        "query": query,
        "cached": False,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "university": uni.to_dict(),
        "alternatives": alternatives,
        "categories": [{"key": k, "label": CATEGORY_LABELS[k], "count": counts.get(k, 0)} for k in CATEGORIES],
        "photos": [c.to_dict() for c in visible],
        "hidden": [c.to_dict() for c in hidden],
        "stats": {
            "candidates": len(raw),
            "same_file_merged": same_file,
            "excluded": sum(excluded.values()),
            "excluded_reasons": dict(excluded.most_common()),
            "duplicates": dup_count,
            "shown": len(visible),
            "verified": status_counts.get("verified", 0),
            "likely": status_counts.get("likely", 0),
            "hidden_unverified": len(hidden),
        },
        "sources": statuses,
        "warnings": warnings,
        "vision_enabled": bool(config.GEMINI_API_KEY),
        "method": vision_note,
        "timing": {"total_ms": int((time.monotonic() - t0) * 1000), "stages": timings},
    }
    _profile_cache[uni.qid] = (time.time(), result)
    return result

