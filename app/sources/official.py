"""Официальный сайт вуза (адрес берём из Wikidata P856, а не из поиска).

Берём только og:image / twitter:image — картинку, которую сам сайт отдаёт для
превью ссылок. Файл не сохраняем: показываем как превью со ссылкой на сайт.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from ..models import Candidate, SourceHit
from ..utils import normalize

META_RE = re.compile(r"<meta\b[^>]*>", re.I)
ATTR_RE = re.compile(r'(\w[\w:-]*)\s*=\s*("([^"]*)"|\'([^\']*)\')', re.I)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
LOGO_RE = re.compile(r"logo|favicon|icon|emblem|gerb|герб|sprite|placeholder", re.I)


def extract_preview_images(html_text: str, base_url: str) -> tuple[list[str], str]:
    found: list[str] = []
    for tag in META_RE.findall(html_text[:300_000]):
        attrs = {}
        for m in ATTR_RE.finditer(tag):
            attrs[m.group(1).lower()] = m.group(3) if m.group(3) is not None else m.group(4)
        key = (attrs.get("property") or attrs.get("name") or "").lower()
        if key in ("og:image", "og:image:url", "og:image:secure_url", "twitter:image"):
            content = (attrs.get("content") or "").strip()
            if content:
                url = urljoin(base_url, content)
                if url.startswith("http") and not LOGO_RE.search(url) and url not in found:
                    found.append(url)
    title_match = TITLE_RE.search(html_text[:300_000])
    title = re.sub(r"\s+", " ", title_match.group(1)).strip()[:150] if title_match else ""
    return found[:2], title


async def from_official_site(fetcher, website: str) -> list[Candidate]:
    html_text = await fetcher.get_text(website, timeout=5.0)
    images, page_title = extract_preview_images(html_text, website)
    host = urlparse(website).netloc
    out = []
    for url in images:
        name = url.rsplit("/", 1)[-1].split("?")[0][:80] or "preview"
        out.append(
            Candidate(
                key=normalize(url),
                title=f"Превью сайта {host}: {name}",
                thumb_url=url,
                full_url=url,
                page_url=website,
                description=page_title,
                license="© правообладатель, показано как превью со ссылкой",
                license_url=website,
                author=host,
                sources=[SourceHit("official_site", "Официальный сайт вуза", website, host)],
            )
        )
    return out
