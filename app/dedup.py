"""Шаг 4. Удаление дубликатов.

1) одинаковое название файла из разных выборок → объединяем источники;
2) одинаковый SHA-1 (Commons отдаёт его в API) → точные копии;
3) dHash 64 бита → визуально почти одинаковые снимки (пересжатые, обрезанные,
   серии кадров с одной точки). Держим тот, у кого выше достоверность.
"""
from __future__ import annotations

import io
from typing import Optional

from PIL import Image, ImageOps

from . import config
from .models import Candidate


def dhash(data: bytes, size: int = 8) -> Optional[int]:
    try:
        with Image.open(io.BytesIO(data)) as img:
            img = ImageOps.exif_transpose(img)
            gray = img.convert("L").resize((size + 1, size), Image.Resampling.LANCZOS)
            px = gray.tobytes()
    except Exception:
        return None
    value = 0
    for row in range(size):
        for col in range(size):
            left = px[row * (size + 1) + col]
            right = px[row * (size + 1) + col + 1]
            value = (value << 1) | (1 if left > right else 0)
    return value


def image_info(data: bytes) -> tuple[int, int, str]:
    """Ширина, высота и mime по содержимому файла."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            fmt = (img.format or "").lower()
            mime = {"jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(fmt, f"image/{fmt}")
            return img.width, img.height, mime
    except Exception:
        return 0, 0, ""


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def merge_same_files(cands: list[Candidate]) -> tuple[list[Candidate], int]:
    by_key: dict[str, Candidate] = {}
    merged = 0
    for c in cands:
        existing = by_key.get(c.key)
        if existing is None:
            by_key[c.key] = c
            continue
        merged += 1
        known = {(s.type, s.detail) for s in existing.sources}
        for s in c.sources:
            if (s.type, s.detail) not in known:
                existing.sources.append(s)
        existing.lat = existing.lat if existing.lat is not None else c.lat
        existing.lon = existing.lon if existing.lon is not None else c.lon
    return list(by_key.values()), merged


def remove_duplicates(cands: list[Candidate]) -> tuple[list[Candidate], int]:
    """cands должны быть отсортированы по убыванию достоверности."""
    kept: list[Candidate] = []
    seen_sha: dict[str, Candidate] = {}
    removed = 0
    for c in cands:
        if c.sha1 and c.sha1 in seen_sha:
            seen_sha[c.sha1].duplicates += 1
            removed += 1
            continue
        twin = None
        if c.dhash is not None:
            for k in kept:
                if k.dhash is not None and hamming(k.dhash, c.dhash) <= config.DHASH_MAX_DISTANCE:
                    twin = k
                    break
        if twin is not None:
            twin.duplicates += 1
            removed += 1
            continue
        kept.append(c)
        if c.sha1:
            seen_sha[c.sha1] = c
    return kept, removed
