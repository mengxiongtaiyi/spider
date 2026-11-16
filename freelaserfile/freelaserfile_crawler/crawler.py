"""FreeLaserFile list→detail crawler with image download and checkpoints."""

from __future__ import annotations

import json
import logging
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .api import FreeLaserFileApi
from .export import FIELDNAMES, append_csv, read_csv_rows, write_csv, write_xlsx
from .http_client import PoliteClient
from .parse import DesignRecord, record_from_payload

logger = logging.getLogger(__name__)


@dataclass
class CrawlConfig:
    output_dir: Path = Path("data/freelaserfile")
    image_dir: Path = Path("data/freelaserfile/images")
    checkpoint_path: Path = Path("data/freelaserfile/checkpoints/state.json")
    csv_path: Path = Path("data/freelaserfile/freelaserfile.csv")
    xlsx_path: Path = Path("data/freelaserfile/freelaserfile.xlsx")
    min_delay: float = 0.4
    max_delay: float = 1.0
    page_limit: int = 100
    max_items: int | None = None
    max_pages: int | None = None
    start_page: int = 1
    category_slug: str | None = None
    tag: str | None = None
    max_images_per_item: int | None = None
    resume: bool = True
    download_images: bool = True
    write_xlsx: bool = True
    enrich_detail: bool = True  # fetch /api/designs/{slug} for full title/images


def _ext_from_url(url: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in {".webp", ".jpg", ".jpeg", ".png", ".gif", ".svg"}:
        return suffix
    return mimetypes.guess_extension("image/webp") or ".webp"


def _safe_name(text: str) -> str:
    text = re.sub(r"[^\w.\-]+", "_", text.strip(), flags=re.U)
    return text.strip("_")[:100] or "item"


class FreeLaserFileCrawler:
    def __init__(self, config: CrawlConfig) -> None:
        self.config = config
        self.state = {
            "next_page": config.start_page,
            "total_pages": None,
            "total": None,
            "processed_slugs": [],
            "success_count": 0,
            "failed_count": 0,
        }
        if config.resume:
            self._load_checkpoint()

    def _load_checkpoint(self) -> None:
        path = self.config.checkpoint_path
        if not path.exists():
            return
        data = json.loads(path.read_text(encoding="utf-8"))
        self.state.update(data)
        logger.info(
            "Resumed checkpoint page=%s success=%s",
            self.state.get("next_page"),
            self.state.get("success_count"),
        )

    def _save_checkpoint(self) -> None:
        path = self.config.checkpoint_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")

    def _record_to_row(self, seq: int, rec: DesignRecord) -> dict:
        return {
            "seq": seq,
            "sourcePageUrl": rec.source_page_url,
            "detailUrl": rec.detail_url,
            "title": rec.title,
            "coverUrl": rec.cover_url,
            "coverUrlRelative": rec.cover_url_relative,
            "imageUrls": rec.image_urls,
            "imagePaths": rec.image_paths,
            "categories": rec.categories,
            "tags": rec.tags,
            "description": rec.description,
            "slug": rec.slug,
            "designId": rec.design_id,
            "entryType": rec.entry_type,
            "crawlStatus": rec.crawl_status,
            "skipReason": rec.skip_reason,
        }

    def _download_images(self, client: PoliteClient, rec: DesignRecord) -> None:
        if not self.config.download_images:
            return
        candidate_groups = rec.extra.get("imageCandidates") if rec.extra else None
        if not candidate_groups:
            urls = [u for u in (rec.image_urls or "").split("|") if u]
            if not urls and rec.cover_url:
                urls = [rec.cover_url]
            candidate_groups = [[u] for u in urls]
        if self.config.max_images_per_item is not None:
            candidate_groups = candidate_groups[: self.config.max_images_per_item]

        paths: list[str] = []
        chosen_urls: list[str] = []
        cover_rel = ""
        for idx, candidates in enumerate(candidate_groups):
            saved = False
            for url in candidates:
                ext = _ext_from_url(url)
                name = f"{_safe_name(rec.slug or rec.design_id or 'item')}_{idx:02d}{ext}"
                dest = self.config.image_dir / name
                rel = f"data/freelaserfile/images/{name}"
                try:
                    if not dest.exists() or dest.stat().st_size == 0:
                        client.download_file(url, dest)
                    paths.append(rel)
                    chosen_urls.append(url)
                    if idx == 0:
                        cover_rel = rel
                        rec.cover_url = url
                    saved = True
                    break
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Image download failed %s: %s", url, exc)
                    continue
            if not saved:
                logger.warning("All image candidates failed for %s idx=%s", rec.slug, idx)

        rec.cover_url_relative = cover_rel
        rec.image_paths = "|".join(paths)
        if chosen_urls:
            rec.image_urls = "|".join(chosen_urls)

    def run(self) -> dict:
        cfg = self.config
        cfg.output_dir.mkdir(parents=True, exist_ok=True)
        cfg.image_dir.mkdir(parents=True, exist_ok=True)
        cfg.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

        processed = set(self.state.get("processed_slugs") or [])
        existing_rows = read_csv_rows(cfg.csv_path)
        for row in existing_rows:
            slug = row.get("slug") or ""
            if slug:
                processed.add(slug)
        seq = len(existing_rows)
        success_added = 0

        with PoliteClient(min_delay=cfg.min_delay, max_delay=cfg.max_delay) as client:
            api = FreeLaserFileApi(client)
            # Warm upload base from homepage HTML.
            api.discover_upload_base()

            page = int(self.state.get("next_page") or cfg.start_page)
            total_pages = self.state.get("total_pages")

            while True:
                if cfg.max_pages is not None and (page - cfg.start_page + 1) > cfg.max_pages:
                    logger.info("Stopping: reached --max-pages")
                    break
                if total_pages is not None and page > int(total_pages):
                    logger.info("Stopping: passed last page %s", total_pages)
                    break
                if cfg.max_items is not None and success_added >= cfg.max_items:
                    logger.info("Stopping: reached --max-items")
                    break

                logger.info("List API page %s", page)
                listing = api.list_designs(
                    page=page,
                    limit=cfg.page_limit,
                    category_slug=cfg.category_slug,
                    tag=cfg.tag,
                )
                total_pages = listing.total_pages
                self.state["total_pages"] = total_pages
                self.state["total"] = listing.total

                if not listing.items:
                    logger.info("Empty page %s; done", page)
                    break

                # Refine CDN base using first slug if needed.
                if page == cfg.start_page and listing.items:
                    api.discover_upload_base(listing.items[0].get("slug"))

                page_rows: list[dict] = []
                for item in listing.items:
                    if cfg.max_items is not None and success_added >= cfg.max_items:
                        break
                    slug = (item.get("slug") or "").strip()
                    if not slug or slug in processed:
                        continue

                    try:
                        payload = item
                        if cfg.enrich_detail:
                            payload = api.get_design(slug)
                            # Keep list source URL even after detail fetch.
                        rec = record_from_payload(
                            api,
                            payload,
                            source_page_url=listing.source_page_url,
                        )
                        # Ensure similar sidebar images never leak even if list payload odd.
                        if "similar" in payload:
                            # already excluded in parse.design_image_urls
                            pass
                        self._download_images(client, rec)
                        seq += 1
                        page_rows.append(self._record_to_row(seq, rec))
                        success_added += 1
                        self.state["success_count"] = int(self.state.get("success_count") or 0) + 1
                        processed.add(slug)
                        logger.info("OK [%s] %s", seq, rec.title[:80])
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("Failed %s: %s", slug, exc)
                        seq += 1
                        fail = DesignRecord(
                            source_page_url=listing.source_page_url,
                            detail_url=f"https://freelaserfile.com/design/{slug}",
                            title=item.get("title") or "",
                            cover_url="",
                            slug=slug,
                            design_id=str(item.get("id") or ""),
                            entry_type="detail",
                            crawl_status="failed",
                            skip_reason=str(exc)[:300],
                        )
                        page_rows.append(self._record_to_row(seq, fail))
                        self.state["failed_count"] = int(self.state.get("failed_count") or 0) + 1
                        processed.add(slug)

                if page_rows:
                    append_csv(cfg.csv_path, page_rows)

                page += 1
                self.state["next_page"] = page
                self.state["processed_slugs"] = sorted(processed)
                self._save_checkpoint()

        all_rows = read_csv_rows(cfg.csv_path)
        for i, row in enumerate(all_rows, start=1):
            row["seq"] = i
        write_csv(cfg.csv_path, all_rows)
        if cfg.write_xlsx:
            write_xlsx(cfg.xlsx_path, all_rows)

        summary = {
            "success_rows": len(all_rows),
            "failed_count": self.state.get("failed_count"),
            "next_page": self.state.get("next_page"),
            "total_pages": self.state.get("total_pages"),
            "api_total": self.state.get("total"),
            "csv": str(cfg.csv_path),
            "xlsx": str(cfg.xlsx_path) if cfg.write_xlsx else None,
            "images": str(cfg.image_dir),
            "checkpoint": str(cfg.checkpoint_path),
            "fields": FIELDNAMES,
            "upload_base_note": "images from backend uploads CDN; originals often 404 → watermarked/medium",
        }
        (cfg.output_dir / "run_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return summary
