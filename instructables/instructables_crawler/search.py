"""Instructables Typesense search via public page-context API key + proxy."""

from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import quote_plus, urlencode

from bs4 import BeautifulSoup

from .http_client import PoliteClient

BOOTSTRAP_URL = "https://www.instructables.com/projects"
INCLUDE_FIELDS = (
    "title,urlString,coverImageUrl,screenName,favorites,views,"
    "primaryClassification,featureFlag,prizeLevel,IMadeItCount,sponsoredFlag"
)


@dataclass
class SearchHit:
    title: str
    url_string: str
    detail_url: str
    author: str
    cover_url: str
    favorite_count: int | None
    view_count: int | None
    primary_classification: str
    sponsored: bool
    search_keyword: str
    source_page_url: str
    projects_scope: str


@dataclass
class SearchPage:
    hits: list[SearchHit]
    found: int
    page: int
    per_page: int


class TypesenseSearch:
    def __init__(self, client: PoliteClient) -> None:
        self.client = client
        self.api_key: str | None = None
        self.proxy_path: str = "/api_proxy/search"
        self._requests_since_refresh = 0

    def refresh_credentials(self) -> None:
        resp = self.client.get(BOOTSTRAP_URL)
        soup = BeautifulSoup(resp.text, "lxml")
        el = soup.select_one("#js-page-context")
        if not el or not el.string:
            raise RuntimeError("Missing #js-page-context on Instructables bootstrap page")
        ctx = json.loads(el.string)
        self.api_key = ctx["typesenseApiKey"]
        self.proxy_path = ctx.get("typesenseProxy") or "/api_proxy/search"
        self._requests_since_refresh = 0

    def ensure_credentials(self) -> None:
        if not self.api_key or self._requests_since_refresh >= 200:
            self.refresh_credentials()

    def source_page_url(self, keyword: str, page: int, *, projects_scope: str) -> str:
        q = quote_plus(keyword)
        base = f"https://www.instructables.com/projects?q={q}&projects={projects_scope}"
        if page > 1:
            return f"{base}&page={page}"
        return base

    def search(
        self,
        keyword: str,
        *,
        page: int = 1,
        per_page: int = 60,
        projects_scope: str = "featured",
    ) -> SearchPage:
        self.ensure_credentials()
        assert self.api_key

        filters = ["status:=PUBLISHED", "indexTags:!=external"]
        if projects_scope == "featured":
            filters.append("featureFlag:=true")

        params = {
            "q": keyword,
            "query_by": "title,stepBody,screenName",
            "page": page,
            "per_page": per_page,
            "sort_by": "_text_match:desc" if keyword else "publishDate:desc",
            "include_fields": INCLUDE_FIELDS,
            "filter_by": " && ".join(filters),
        }
        url = (
            f"https://www.instructables.com{self.proxy_path}"
            f"/collections/projects/documents/search?{urlencode(params)}"
        )
        headers = {
            "Accept": "application/json",
            "content-type": "application/json",
            "x-typesense-api-key": self.api_key,
            "Referer": self.source_page_url(keyword, page, projects_scope=projects_scope),
        }
        resp = self.client.get(url, headers=headers)
        self._requests_since_refresh += 1
        data = resp.json()
        source = self.source_page_url(keyword, page, projects_scope=projects_scope)
        hits: list[SearchHit] = []
        for item in data.get("hits") or []:
            doc = item.get("document") or {}
            url_string = (doc.get("urlString") or "").strip()
            if not url_string:
                continue
            detail_url = f"https://www.instructables.com/{url_string.strip('/')}/"
            hits.append(
                SearchHit(
                    title=doc.get("title") or "",
                    url_string=url_string,
                    detail_url=detail_url,
                    author=doc.get("screenName") or "",
                    cover_url=doc.get("coverImageUrl") or "",
                    favorite_count=_as_int(doc.get("favorites")),
                    view_count=_as_int(doc.get("views")),
                    primary_classification=doc.get("primaryClassification") or "",
                    sponsored=bool(doc.get("sponsoredFlag")),
                    search_keyword=keyword,
                    source_page_url=source,
                    projects_scope=projects_scope,
                )
            )
        return SearchPage(
            hits=hits,
            found=int(data.get("found") or 0),
            page=page,
            per_page=per_page,
        )


def _as_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
