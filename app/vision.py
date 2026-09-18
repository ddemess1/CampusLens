"""Необязательная AI-проверка содержимого изображений (Google Gemini).

Модель НЕ доказывает, что здание принадлежит конкретному вузу — это делают
происхождение файла, геометка и текст. Модель отвечает на вопросы, которые
метаданные не закрывают: это вообще фото места? что на нём (аудитория,
библиотека, общежитие…)? нет ли явного мусора (еда, машина, скан)?
Все фото отправляются одним запросом, чтобы уложиться в бюджет времени.
"""
from __future__ import annotations

import base64
import io
import json
import re

from PIL import Image

from . import config
from .models import Candidate, University

PROMPT = """You check photos for a university visual profile.
University: {name} ({city}, {country}).
For EACH image (they are numbered) answer strictly as a JSON array, one object per image:
{{"i": <number>, "is_photo": true|false, "category": "campus"|"education"|"dormitory"|"library"|"student_life"|"city"|"other",
 "relevant": true|false, "note": "<max 8 words in Russian describing what is visible>"}}
Rules:
- is_photo=false for logos, maps, diagrams, scanned documents, screenshots, drawings.
- category: campus = buildings/grounds/entrances; education = classrooms, lecture halls, labs;
  dormitory = student housing; library = libraries/reading rooms; student_life = students, events, sport, canteens;
  city = streets and views of the city without university focus; other = anything else.
- For images labeled SUBJECT=university: relevant=true only if the image plausibly shows a university
  setting (academic buildings, interiors, students). For SUBJECT=city: relevant=true if it shows the city.
- Do not guess which specific university it is. Output JSON only."""


def _shrink(data: bytes, max_side: int = 384) -> bytes | None:
    try:
        with Image.open(io.BytesIO(data)) as img:
            img = img.convert("RGB")
            img.thumbnail((max_side, max_side))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=75)
            return buf.getvalue()
    except Exception:
        return None


def parse_response(text: str) -> list[dict]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\[.*\]", text, re.S)
        if not m:
            return []
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return []
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []


async def analyze(fetcher, uni: University, cands: list[Candidate]) -> int:
    """Заполняет cand.vision. Возвращает число проанализированных фото."""
    if not config.GEMINI_API_KEY:
        return 0
    batch = [c for c in cands if c.image_bytes][: config.VISION_MAX_IMAGES]
    parts: list[dict] = [{"text": PROMPT.format(
        name=uni.name, city=uni.city_name or "?", country=uni.country or "?")}]
    indexed: dict[int, Candidate] = {}
    for i, c in enumerate(batch, start=1):
        small = _shrink(c.image_bytes)
        if not small:
            continue
        indexed[i] = c
        parts.append({"text": f"Image {i}. SUBJECT={c.subject}"})
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(small).decode()}})
    if not indexed:
        return 0

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}:generateContent"
    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
    }
    resp = await fetcher.post_json(
        url, payload, headers={"x-goog-api-key": config.GEMINI_API_KEY}, timeout=config.VISION_TIMEOUT_S
    )
    try:
        text = resp["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        return 0

    done = 0
    for item in parse_response(text):
        try:
            idx = int(item.get("i"))
        except (TypeError, ValueError):
            continue
        cand = indexed.get(idx)
        if not cand:
            continue
        cat = item.get("category")
        cand.vision = {
            "is_photo": item.get("is_photo"),
            "category": cat if cat != "other" else None,
            "relevant": item.get("relevant"),
            "note": str(item.get("note", ""))[:60],
        }
        done += 1
    return done
