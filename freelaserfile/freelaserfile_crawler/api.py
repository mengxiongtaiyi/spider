"""FreeLaserFile public JSON API helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

from .http_client import PoliteClient

SITE = "https://freelaserfile.com"
API_DESIGNS = f"{SITE}/api/designs"
DEFAULT_UPLOAD_BASE = "https://backend-production-a0a2.up.railway.app/uploads/"


@dataclass
class ListPage:
    items: list[dict[str, Any]]
    page: int
    limit: int
    total: int
    total_pages: int
    source_page_url: str


def public_list_url(page: int = 1, *, category_slug: str | None = None) -> str:
    if category_slug:
        base = f"{SITE}/category/{category_slug}"
        return f"{base}?page={page}" if page > 1 else base
    return f"{SITE}/?page={page}" if page > 1 else f"{SITE}/"


def detail_url(slug: str) -> str:
    return f"{SITE}/design/{slug.strip('/')}"


class FreeLaserFileApi:
    def __init__(self, client: PoliteClient) -> None:
        self.client = client
        self.upload_base = DEFAULT_UPLOAD_BASE

    def discover_upload_base(self, sample_slug: str | None = None) -> str:
        """Infer CDN upload prefix from a public detail HTML page."""
        url = f"{SITE}/design/{sample_slug}" if sample_slug else SITE
        try:
            html = self.client.get(url, headers={"Accept": "text/html"}).text
        except Exception:
            return self.upload_base
        m = re.search(
            r"(https?://[^\"'\s>]+/uploads/)(?:originals|medium|watermarked|thumbs)/",
            html,
        )
        if m:
            self.upload_base = m.group(1)
        return self.upload_base

    def list_designs(
        self,
        *,
        page: int = 1,
        limit: int = 100,
        category_slug: str | None = None,
        tag: str | None = None,
    ) -> ListPage:
        params: dict[str, Any] = {"page": page, "limit": min(limit, 100)}
        if category_slug:
            params["category"] = category_slug
        if tag:
            params["tag"] = tag
        resp = self.client.get(API_DESIGNS, params=params)
        data = resp.json()
        pagination = data.get("pagination") or {}
        items = data.get("data") or []
        return ListPage(
            items=items,
            page=int(pagination.get("page") or page),
            limit=int(pagination.get("limit") or limit),
            total=int(pagination.get("total") or len(items)),
            total_pages=int(pagination.get("totalPages") or 1),
            source_page_url=public_list_url(page, category_slug=category_slug),
        )

    def get_design(self, slug: str) -> dict[str, Any]:
        resp = self.client.get(f"{API_DESIGNS}/{slug}")
        return resp.json()

    def absolute_image_url(self, relative_or_abs: str | None) -> str:
        if not relative_or_abs:
            return ""
        if relative_or_abs.startswith("http://") or relative_or_abs.startswith("https://"):
            return relative_or_abs
        return urljoin(self.upload_base, relative_or_abs.lstrip("/"))

    def best_image_url(self, image: dict[str, Any]) -> str:
        """Prefer publicly available assets (CDN originals often 404 → medium/watermarked)."""
        for key in ("mediumUrl", "watermarkedUrl", "originalUrl", "thumbUrl"):
            rel = image.get(key)
            if not rel:
                continue
            return self.absolute_image_url(rel)
        return ""

    def image_url_fallbacks(self, image: dict[str, Any]) -> list[str]:
        """Ordered candidates for download retries (medium first; originals last-resort)."""
        urls: list[str] = []
        for key in ("mediumUrl", "watermarkedUrl", "thumbUrl", "originalUrl"):
            rel = image.get(key)
            if not rel:
                continue
            url = self.absolute_image_url(rel)
            if url and url not in urls:
                urls.append(url)
        return urls
