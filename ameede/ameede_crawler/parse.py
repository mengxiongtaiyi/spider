"""HTML parsers for Ameede list and detail pages."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .classify import classify_url

# Ameede codes seen in the wild: E0025665, S0000857, L0000435, …
PROJECT_CODE_RE = re.compile(r"\b([A-Z]\d{6,8})\b", re.I)
SIZE_SUFFIX_RE = re.compile(r"-\d+x\d+(?=\.(?:webp|jpe?g|png|gif)$)", re.I)


@dataclass
class ListItem:
    detail_url: str
    list_title: str
    list_image_url: str | None
    entry_type: str
    source_page_url: str


@dataclass
class DetailRecord:
    source_page_url: str
    detail_url: str
    title: str
    project_code: str
    cover_url: str
    cover_url_relative: str = ""
    download_url: str = ""
    categories: str = ""
    tags: str = ""
    entry_type: str = "detail"
    crawl_status: str = "success"
    skip_reason: str = ""
    extra: dict = field(default_factory=dict)


def _abs(base: str, href: str | None) -> str:
    if not href:
        return ""
    return urljoin(base, href.strip())


def _best_srcset_url(srcset: str | None) -> str | None:
    if not srcset:
        return None
    best_url = None
    best_w = -1
    for part in srcset.split(","):
        bits = part.strip().split()
        if not bits:
            continue
        url = bits[0]
        width = 0
        if len(bits) >= 2 and bits[1].endswith("w"):
            try:
                width = int(bits[1][:-1])
            except ValueError:
                width = 0
        if width >= best_w:
            best_w = width
            best_url = url
    return best_url


def prefer_full_image_url(url: str | None) -> str:
    """Prefer non-resized WordPress upload URLs when a size suffix is present."""
    if not url:
        return ""
    candidate = SIZE_SUFFIX_RE.sub("", url)
    return candidate or url


def extract_project_code(text: str) -> str:
    m = PROJECT_CODE_RE.search(text or "")
    return m.group(1).upper() if m else ""


def parse_list_page(html: str, page_url: str) -> tuple[list[ListItem], int | None]:
    soup = BeautifulSoup(html, "lxml")
    items: list[ListItem] = []
    seen: set[str] = set()

    for article in soup.select("article.postbox, article.post"):
        classes = " ".join(article.get("class") or [])
        # Download CPT cards (if ever mixed into archives) are not source records.
        if "type-wpdmpro" in classes or "wpdmpro" in classes:
            link = article.select_one("a.postboxlink, a[href]")
            href = _abs(page_url, link.get("href") if link else None)
            if href and href not in seen:
                seen.add(href)
                items.append(
                    ListItem(
                        detail_url=href,
                        list_title=(article.select_one(".entry-title").get_text(strip=True)
                                    if article.select_one(".entry-title") else ""),
                        list_image_url=None,
                        entry_type="download",
                        source_page_url=page_url,
                    )
                )
            continue

        link = article.select_one("a.postboxlink") or article.select_one("a[href]")
        href = _abs(page_url, link.get("href") if link else None)
        if not href or href in seen:
            continue
        seen.add(href)

        entry_type = classify_url(href)
        title_el = article.select_one("h5.entry-title, .entry-title, h2, h3")
        img = article.select_one("img")
        img_url = None
        if img:
            img_url = prefer_full_image_url(
                _best_srcset_url(img.get("srcset"))
                or img.get("data-src")
                or img.get("src")
            )

        items.append(
            ListItem(
                detail_url=href,
                list_title=title_el.get_text(strip=True) if title_el else "",
                list_image_url=img_url,
                entry_type=entry_type,
                source_page_url=page_url,
            )
        )

    max_page = _parse_max_page(soup, page_url)
    return items, max_page


def _parse_max_page(soup: BeautifulSoup, page_url: str) -> int | None:
    max_page = None
    for a in soup.select("a.page-numbers, .nav-links a, .pagination a"):
        href = a.get("href") or ""
        m = re.search(r"/page/(\d+)/?", href)
        if m:
            n = int(m.group(1))
            max_page = n if max_page is None else max(max_page, n)
        text = re.sub(r"[^\d]", "", a.get_text(" ", strip=True))
        if text.isdigit():
            n = int(text)
            max_page = n if max_page is None else max(max_page, n)
    # Current page number from URL as lower bound.
    cur = re.search(r"/page/(\d+)/?", page_url)
    if cur:
        n = int(cur.group(1))
        max_page = n if max_page is None else max(max_page, n)
    return max_page


def parse_detail_page(html: str, detail_url: str, source_page_url: str) -> DetailRecord:
    soup = BeautifulSoup(html, "lxml")
    entry_type = classify_url(detail_url)

    if entry_type == "download":
        title = (
            (soup.select_one("h2.entry-title, .entry-title") or soup.title).get_text(strip=True)
            if (soup.select_one("h2.entry-title, .entry-title") or soup.title)
            else ""
        )
        return DetailRecord(
            source_page_url=source_page_url,
            detail_url=detail_url,
            title=title,
            project_code=extract_project_code(title + " " + detail_url),
            cover_url="",
            entry_type="download",
            crawl_status="skipped",
            skip_reason="download_entry_not_source_record",
        )

    if entry_type == "ad":
        return DetailRecord(
            source_page_url=source_page_url,
            detail_url=detail_url,
            title="",
            project_code="",
            cover_url="",
            entry_type="ad",
            crawl_status="skipped",
            skip_reason="ad_or_external_not_source_record",
        )

    article = soup.select_one("article.articlebox, article.post, article")
    title_el = None
    if article:
        title_el = article.select_one("h2.entry-title, h1.entry-title, .entry-title")
    if not title_el:
        title_el = soup.select_one("article h2.entry-title, h2.entry-title, meta[property='og:title']")
    if title_el and title_el.name == "meta":
        title = (title_el.get("content") or "").replace(" - Free Download Vector Files", "").strip()
    else:
        title = title_el.get_text(strip=True) if title_el else ""
        if not title and soup.title:
            title = soup.title.get_text(strip=True).replace(" - Free Download Vector Files", "").strip()

    cover_url = ""
    if article:
        img = article.select_one("img.wp-post-image")
        if img:
            cover_url = prefer_full_image_url(
                _best_srcset_url(img.get("srcset")) or img.get("src") or img.get("data-src")
            )
    if not cover_url:
        og = soup.select_one("meta[property='og:image']")
        cover_url = prefer_full_image_url(og.get("content") if og else None)

    download_url = ""
    categories: list[str] = []
    tags: list[str] = []
    scope = article or soup
    for a in scope.select("a[href]"):
        href = _abs(detail_url, a.get("href"))
        kind = classify_url(href)
        if kind == "download" and not download_url:
            download_url = href
        rel = " ".join(a.get("rel") or [])
        text = a.get_text(strip=True)
        if "category" in rel and text and text not in categories:
            categories.append(text)
        if rel == "tag" or (rel.endswith(" tag") if rel else False) or ("tag" in rel and "category" not in rel):
            if text and text not in tags:
                # Avoid category links double-counted via "category tag"
                if "category" not in rel:
                    tags.append(text)

    # Prefer explicit category-tag block parsing.
    cat_block = scope.select_one(".category-tag")
    if cat_block:
        categories = []
        tags = []
        for a in cat_block.select("a[rel*='category']"):
            t = a.get_text(strip=True)
            if t and t not in categories:
                categories.append(t)
        for a in cat_block.select("a[rel='tag']"):
            t = a.get_text(strip=True)
            if t and t not in tags:
                tags.append(t)

    code = extract_project_code(title) or extract_project_code(detail_url) or extract_project_code(download_url)

    return DetailRecord(
        source_page_url=source_page_url,
        detail_url=detail_url,
        title=title,
        project_code=code,
        cover_url=cover_url,
        download_url=download_url,
        categories=",".join(categories),
        tags=",".join(tags),
        entry_type="detail",
        crawl_status="success" if title and cover_url else "partial",
        skip_reason="" if title and cover_url else "missing_title_or_cover",
    )


def page_url_for(base_category_url: str, page: int) -> str:
    base = base_category_url.rstrip("/") + "/"
    if page <= 1:
        return base
    return urljoin(base, f"page/{page}/")


def slug_from_url(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    return path.split("/")[-1] if path else "item"
