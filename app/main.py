"""HTTP-слой.

GET /api/profile?q=...         — готовый профиль (JSON)
GET /api/profile/stream?q=...  — те же данные + реальные этапы в реальном времени (SSE)
GET /api/suggest?q=...         — подсказки вузов для поля ввода
GET /api/health                — проверка живости (для мониторинга и «будильника» хостинга)
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .fetcher import HttpFetcher, SourceError
from .pipeline import build_profile
from .resolver import NotFound, get_entities, is_university, _search_wikidata, _label, _description

log = logging.getLogger("campuslens")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.fetcher = HttpFetcher()
    yield
    await app.state.fetcher.close()


app = FastAPI(title="CampusLens", version="1.0.0", lifespan=lifespan)


def _clean_query(q: str) -> str:
    q = " ".join(q.split())
    if len(q) < 2:
        raise HTTPException(400, "Введите название университета (минимум 2 символа).")
    if len(q) > 120:
        raise HTTPException(400, "Слишком длинный запрос (максимум 120 символов).")
    return q


def _not_found_payload(exc: NotFound) -> dict:
    return {
        "error": "not_found",
        "message": str(exc),
        "suggestion": exc.suggestion,
        "alternatives": exc.alternatives,
    }


@app.get("/api/health")
async def health():
    return {"ok": True, "vision": bool(config.GEMINI_API_KEY)}


@app.get("/api/profile")
async def profile(q: str = Query(..., description="Название университета или QID Wikidata")):
    q = _clean_query(q)
    try:
        return await build_profile(app.state.fetcher, q)
    except NotFound as exc:
        return JSONResponse(_not_found_payload(exc), status_code=404)
    except SourceError as exc:
        return JSONResponse({"error": "source_unavailable", "message": str(exc)}, status_code=503)


@app.get("/api/profile/stream")
async def profile_stream(q: str = Query(...)):
    q = _clean_query(q)
    queue: asyncio.Queue = asyncio.Queue()

    async def emit(stage: str, state: str, detail: str) -> None:
        await queue.put(("stage", {"stage": stage, "state": state, "detail": detail}))

    async def run() -> None:
        try:
            result = await build_profile(app.state.fetcher, q, emit)
            await queue.put(("result", result))
        except NotFound as exc:
            await queue.put(("fail", _not_found_payload(exc)))
        except SourceError as exc:
            await queue.put(("fail", {"error": "source_unavailable", "message": str(exc)}))
        except Exception:
            log.exception("profile failed for %r", q)
            await queue.put(("fail", {"error": "internal", "message": "Внутренняя ошибка сервиса. Попробуйте ещё раз."}))
        finally:
            await queue.put(None)

    async def events():
        task = asyncio.create_task(run())
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if item is None:
                    break
                name, data = item
                yield f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/suggest")
async def suggest(q: str = Query(..., min_length=2, max_length=120)):
    try:
        qids = await _search_wikidata(app.state.fetcher, q)
        ents = await get_entities(app.state.fetcher, qids[:12])
    except SourceError:
        return {"items": []}
    items = [
        {"qid": qid, "name": _label(ents[qid]), "description": _description(ents[qid])}
        for qid in qids[:12]
        if qid in ents and is_university(ents[qid])
    ]
    return {"items": items[:6]}


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")
