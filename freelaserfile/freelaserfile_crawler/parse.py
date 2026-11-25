"""Normalize FreeLaserFile API payloads into crawl records."""

from __future__ import annotations

from dataclasses import dataclass, field

from .api import FreeLaserFileApi, detail_url


@dataclass
class DesignRecord:
    source_page_url: str
    detail_url: str
    title: str
    cover_url: str
    cover_url_relative: str = ""
    image_urls: str = ""
    image_paths: str = ""
    categories: str = ""
    tags: str = ""
    description: str = ""
    slug: str = ""
    design_id: str = ""
    entry_type: str = "detail"
    crawl_status: str = "success"
    skip_reason: str = ""
    extra: dict = field(default_factory=dict)


def _tag_names(payload: dict) -> list[str]:
    names: list[str] = []
    for item in payload.get("tags") or []:
        tag = item.get("tag") if isinstance(item, dict) else None
        name = None
        if isinstance(tag, dict):
            name = tag.get("name")
        elif isinstance(item, dict):
            name = item.get("name")
        if name and name not in names:
            names.append(str(name))
    return names


def _category_names(payload: dict) -> list[str]:
    names: list[str] = []
    cat = payload.get("category")
    if isinstance(cat, dict) and cat.get("name"):
        names.append(str(cat["name"]))
    elif isinstance(cat, str) and cat:
        names.append(cat)
    # V1.5: keep all Category values; tags may also appear in Category UI
    return names


def design_image_urls(api: FreeLaserFileApi, payload: dict) -> list[str]:
    """Main/design images only — exclude sidebar `similar` recommendations."""
    urls: list[str] = []
    seen: set[str] = set()
    images = sorted(payload.get("images") or [], key=lambda x: x.get("sortOrder") or 0)
    for img in images:
        if not isinstance(img, dict):
            continue
        url = api.best_image_url(img)
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def design_image_candidates(api: FreeLaserFileApi, payload: dict) -> list[list[str]]:
    """Per-image fallback URL lists (excludes sidebar `similar`)."""
    out: list[list[str]] = []
    images = sorted(payload.get("images") or [], key=lambda x: x.get("sortOrder") or 0)
    for img in images:
        if not isinstance(img, dict):
            continue
        cands = api.image_url_fallbacks(img)
        if cands:
            out.append(cands)
    return out


def record_from_payload(
    api: FreeLaserFileApi,
    payload: dict,
    *,
    source_page_url: str,
) -> DesignRecord:
    slug = (payload.get("slug") or "").strip()
    title = (payload.get("title") or "").strip()
    if payload.get("error") or not slug:
        return DesignRecord(
            source_page_url=source_page_url,
            detail_url="",
            title=title,
            cover_url="",
            entry_type="unknown",
            crawl_status="failed",
            skip_reason=str(payload.get("error") or "missing_slug"),
        )

    # Never treat `similar` as this design's images (sidebar / MOST DOWNLOADED).
    image_urls = design_image_urls(api, payload)
    cover_url = image_urls[0] if image_urls else ""
    categories = _category_names(payload)
    tags = _tag_names(payload)
    category_and_tags = list(categories)
    for t in tags:
        if t not in category_and_tags:
            category_and_tags.append(t)

    status = "success" if title and cover_url else "partial"
    return DesignRecord(
        source_page_url=source_page_url,
        detail_url=detail_url(slug),
        title=title,
        cover_url=cover_url,
        image_urls="|".join(image_urls),
        categories=",".join(categories),
        tags=",".join(tags),
        description=(payload.get("description") or "").strip(),
        slug=slug,
        design_id=str(payload.get("id") or ""),
        entry_type="detail",
        crawl_status=status,
        skip_reason="" if status == "success" else "missing_title_or_cover",
        extra={
            "categoryAndTags": ",".join(category_and_tags),
            "imageCandidates": design_image_candidates(api, payload),
        },
    )


def fix_detail_url(slug: str) -> str:
    return f"https://freelaserfile.com/design/{slug}"
