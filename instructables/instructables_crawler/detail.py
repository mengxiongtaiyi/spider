"""Detail fetch + field extraction for Instructables projects."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse

from .http_client import PoliteClient
from .search import SearchHit

DETAIL_API = "https://www.instructables.com/json-api/showInstructableModel"

AD_HOST_FRAGMENTS = (
    "doubleclick",
    "googlesyndication",
    "googleadservices",
    "amazon-adsystem",
    "adsystem",
    "outbrain",
    "taboola",
    "adservice",
)


@dataclass
class DetailRecord:
    source_page_url: str
    detail_url: str
    title: str
    author: str
    category_path: str
    cover_url: str
    cover_url_relative: str = ""
    image_urls: str = ""
    image_paths: str = ""
    favorite_count: str = ""
    view_count: str = ""
    search_keyword: str = ""
    search_keywords: str = ""
    projects_scope: str = "featured"
    entry_type: str = "detail"
    crawl_status: str = "success"
    skip_reason: str = ""
    extra: dict = field(default_factory=dict)


def is_ad_image_url(url: str) -> bool:
    if not url:
        return True
    host = (urlparse(url).netloc or "").lower()
    if any(frag in host for frag in AD_HOST_FRAGMENTS):
        return True
    # Keep Instructables CDN / site media only.
    if host and "instructables.com" not in host and "amazonaws.com" not in host:
        # Unknown off-site image — treat as ad/unrelated for this contract.
        return True
    return False


def category_path_from_classifications(classifications: list | None) -> str:
    if not classifications:
        return ""
    parts: list[str] = []
    for cls in classifications:
        title = (cls or {}).get("title") or ""
        if title:
            parts.append(title)
        channels = (cls or {}).get("channels") or []
        for ch in channels:
            ch_title = (ch or {}).get("title") or ""
            if ch_title:
                parts.append(ch_title)
        break  # primary classification path
    return " > ".join(parts)


def collect_image_urls(payload: dict, *, cover_url: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()

    def add(url: str | None) -> None:
        if not url or url in seen or is_ad_image_url(url):
            return
        seen.add(url)
        urls.append(url)

    add(cover_url)
    cover = payload.get("coverImage") or {}
    if isinstance(cover, dict):
        add(cover.get("downloadUrl"))

    for fobj in payload.get("files") or []:
        if not isinstance(fobj, dict):
            continue
        if fobj.get("image") is False:
            continue
        add(fobj.get("downloadUrl"))

    for step in payload.get("steps") or []:
        for fobj in step.get("files") or []:
            if not isinstance(fobj, dict):
                continue
            if fobj.get("image") is False:
                continue
            add(fobj.get("downloadUrl"))

    return urls


def fetch_detail(client: PoliteClient, hit: SearchHit) -> DetailRecord:
    if hit.sponsored:
        return DetailRecord(
            source_page_url=hit.source_page_url,
            detail_url=hit.detail_url,
            title=hit.title,
            author=hit.author,
            category_path="",
            cover_url=hit.cover_url,
            favorite_count="" if hit.favorite_count is None else str(hit.favorite_count),
            search_keyword=hit.search_keyword,
            search_keywords=hit.search_keyword,
            projects_scope=hit.projects_scope,
            entry_type="ad",
            crawl_status="skipped",
            skip_reason="sponsored_or_ad_not_source_record",
        )

    resp = client.get(
        DETAIL_API,
        params={"urlString": hit.url_string},
        headers={"Accept": "application/json", "Referer": hit.detail_url},
    )
    payload = resp.json()
    if payload.get("error"):
        raise RuntimeError(str(payload.get("error")))

    if payload.get("sponsoredFlag"):
        return DetailRecord(
            source_page_url=hit.source_page_url,
            detail_url=hit.detail_url,
            title=payload.get("title") or hit.title,
            author=((payload.get("author") or {}).get("screenName") or hit.author),
            category_path=category_path_from_classifications(payload.get("classifications")),
            cover_url=hit.cover_url,
            favorite_count=str(payload.get("favorites", hit.favorite_count or "")),
            search_keyword=hit.search_keyword,
            search_keywords=hit.search_keyword,
            projects_scope=hit.projects_scope,
            entry_type="ad",
            crawl_status="skipped",
            skip_reason="sponsored_or_ad_not_source_record",
        )

    tags = payload.get("indexTags") or []
    if any(str(t).lower() == "external" for t in tags):
        return DetailRecord(
            source_page_url=hit.source_page_url,
            detail_url=hit.detail_url,
            title=payload.get("title") or hit.title,
            author=((payload.get("author") or {}).get("screenName") or hit.author),
            category_path="",
            cover_url=hit.cover_url,
            entry_type="ad",
            crawl_status="skipped",
            skip_reason="external_index_tag_not_source_record",
            search_keyword=hit.search_keyword,
            search_keywords=hit.search_keyword,
            projects_scope=hit.projects_scope,
        )

    author = ((payload.get("author") or {}).get("screenName") or hit.author or "").strip()
    title = (payload.get("title") or hit.title or "").strip()
    category_path = category_path_from_classifications(payload.get("classifications"))
    if not category_path and hit.primary_classification:
        # Fallback: workshop/laser-cutting -> Workshop > Laser Cutting (best-effort)
        category_path = " > ".join(
            part.replace("-", " ").title() for part in hit.primary_classification.split("/") if part
        )

    cover_obj = payload.get("coverImage") or {}
    cover_url = ""
    if isinstance(cover_obj, dict):
        cover_url = cover_obj.get("downloadUrl") or ""
    cover_url = cover_url or hit.cover_url or payload.get("downloadUrl") or ""

    image_urls = collect_image_urls(payload, cover_url=cover_url)
    favorites = payload.get("favorites")
    if favorites is None:
        favorites = hit.favorite_count
    views = payload.get("views")
    if views is None:
        views = hit.view_count

    status = "success" if title and cover_url else "partial"
    return DetailRecord(
        source_page_url=hit.source_page_url,
        detail_url=hit.detail_url,
        title=title,
        author=author,
        category_path=category_path,
        cover_url=cover_url,
        image_urls="|".join(image_urls),
        favorite_count="" if favorites is None else str(favorites),
        view_count="" if views is None else str(views),
        search_keyword=hit.search_keyword,
        search_keywords=hit.search_keyword,
        projects_scope=hit.projects_scope,
        entry_type="detail",
        crawl_status=status,
        skip_reason="" if status == "success" else "missing_title_or_cover",
    )
