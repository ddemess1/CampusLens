"""Асинхронный HTTP-клиент.

Пайплайн работает с любым объектом, у которого есть методы get_json / get_bytes /
get_text / post_json. В тестах подставляется поддельный клиент без сети.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from . import config


class SourceError(Exception):
    """Источник недоступен или вернул ошибку."""


class HttpFetcher:
    def __init__(self) -> None:
        import httpx  # импорт здесь, чтобы тесты логики не требовали httpx

        self._httpx = httpx
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(config.HTTP_TIMEOUT_S, connect=4.0),
            headers={"User-Agent": config.USER_AGENT, "Accept-Encoding": "gzip"},
            follow_redirects=True,
            limits=httpx.Limits(max_connections=40, max_keepalive_connections=20),
        )
        self._sem = asyncio.Semaphore(config.HTTP_CONCURRENCY)       # запросы к API
        self._dl_sem = asyncio.Semaphore(config.HTTP_CONCURRENCY + 4)  # скачивание превью

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, url: str, **kw) -> Any:
        last_exc: Optional[Exception] = None
        for attempt in range(2):
            try:
                async with self._sem:
                    resp = await self._client.request(method, url, **kw)
                if resp.status_code in (429, 500, 502, 503, 504) and attempt == 0:
                    await asyncio.sleep(0.4)
                    continue
                if resp.status_code >= 400:
                    raise SourceError(f"HTTP {resp.status_code} от {url.split('/')[2]}")
                return resp
            except self._httpx.HTTPError as exc:
                last_exc = exc
                if attempt == 0:
                    await asyncio.sleep(0.3)
        raise SourceError(f"{type(last_exc).__name__} при запросе к {url.split('/')[2]}")

    async def get_json(self, url: str, params: Optional[dict] = None) -> dict:
        resp = await self._request("GET", url, params=params)
        try:
            return resp.json()
        except ValueError as exc:
            raise SourceError("Некорректный JSON") from exc

    async def get_text(self, url: str, max_bytes: int = 1_500_000, timeout: float = 5.0) -> str:
        data = await self.get_bytes(url, max_bytes=max_bytes, timeout=timeout)
        return data.decode("utf-8", errors="replace")

    async def get_bytes(self, url: str, max_bytes: int = 4_000_000, timeout: Optional[float] = None) -> bytes:
        async def _go() -> bytes:
            async with self._client.stream("GET", url) as resp:
                if resp.status_code >= 400:
                    raise SourceError(f"HTTP {resp.status_code}")
                chunks, size = [], 0
                async for chunk in resp.aiter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        break
                    chunks.append(chunk)
                return b"".join(chunks)

        # таймаут считаем только на саму загрузку, а не на ожидание очереди
        async with self._dl_sem:
            try:
                if timeout:
                    return await asyncio.wait_for(_go(), timeout)
                return await _go()
            except (self._httpx.HTTPError, asyncio.TimeoutError) as exc:
                raise SourceError(type(exc).__name__) from exc

    async def post_json(self, url: str, payload: dict, headers: Optional[dict] = None, timeout: float = 10.0) -> dict:
        try:
            resp = await asyncio.wait_for(
                self._client.post(url, json=payload, headers=headers or {}, timeout=timeout), timeout + 1
            )
        except (self._httpx.HTTPError, asyncio.TimeoutError) as exc:
            raise SourceError(type(exc).__name__) from exc
        if resp.status_code >= 400:
            raise SourceError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json()
