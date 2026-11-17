"""Ameede category crawler: list → detail → image download, with checkpoints."""

from __future__ import annotations

import json
import logging
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .export import FIELDNAMES, append_csv, read_csv_rows, write_csv, write_xlsx
from .http_client import PoliteClient
from .parse import DetailRecord, page_url_for, parse_detail_page, parse_list_page, slug_from_url

logger = logging.getLogger(__name__)

DEFAULT_CATEGORY = "https://www.ameede.com/amee/vector-cnc-laser/"


@dataclass
class CrawlConfig:
    category_url: str = DEFAULT_CATEGORY
    output_dir: Path = Path("data/ameede")
    image_dir: Path = Path("data/ameede/images")
    checkpoint_path: Path = Path("data/ameede/checkpoints/state.json")
    csv_path: Path = Path("data/ameede/ameede.csv")
    xlsx_path: Path = Path("data/ameede/ameede.xlsx")
    skipped_csv_path: Path = Path("data/ameede/ameede_skipped.csv")
    min_delay: float = 0.8
    max_delay: float = 1.6
    max_pages: int | None = None
    max_items: int | None = None
    start_page: int = 1
    resume: bool = True
    # Craftseek Task 2 field 4 confirmed URL-only (no local image download).
    download_images: bool = False
    write_xlsx: bool = True
    xlsx_every_pages: int = 25


def _ext_from_url(url: str, content_type: str | None = None) -> str:
    path = urlparse(url).path
    suffix = Path(path).suffix.lower()
    if suffix in {".webp", ".jpg", ".jpeg", ".png", ".gif", ".svg"}:
        return suffix
    if content_type:
        guess = mimetypes.guess_extension(content_type.split(";")[0].strip())
        if guess:
            return ".jpg" if guess == ".jpe" else guess
    return ".webp"


def _safe_name(text: str) -> str:
    text = re.sub(r"[^\w.\-]+", "_", text.strip(), flags=re.U)
    return text.strip("_")[:120] or "item"


class AmeedeCrawler:
    def __init__(self, config: CrawlConfig) -> None:
        self.config = config
        self.state = {
            "next_page": config.start_page,
            "max_page": None,
            "processed_detail_urls": [],
            "listed_count": 0,
            "success_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "images_downloaded": 0,
            "page_complete": True,
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
            "Resumed checkpoint page=%s success=%s skipped=%s images=%s",
            self.state.get("next_page"),
            self.state.get("success_count"),
            self.state.get("skipped_count"),
            self.state.get("images_downloaded"),
        )

    def _processed_path(self) -> Path:
        return self.config.checkpoint_path.with_name("processed_urls.txt")

    def _save_checkpoint(self) -> None:
        path = self.config.checkpoint_path
        path.parent.mkdir(parents=True, exist_ok=True)
        # Keep state.json small — URL set lives in processed_urls.txt
        slim = {k: v for k, v in self.state.items() if k != "processed_detail_urls"}
        slim["processed_count"] = len(self.state.get("processed_detail_urls") or [])
        path.write_text(json.dumps(slim, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    def _processed(self) -> set[str]:
        urls = set(self.state.get("processed_detail_urls") or [])
        p = self._processed_path()
        if p.exists():
            urls.update(line.strip() for line in p.read_text(encoding="utf-8").splitlines() if line.strip())
        self.state["processed_detail_urls"] = list(urls)
        return urls

    def _mark_processed(self, url: str, processed: set[str]) -> None:
        if url in processed:
            return
        processed.add(url)
        self.state.setdefault("processed_detail_urls", []).append(url)
        with self._processed_path().open("a", encoding="utf-8") as f:
            f.write(url + "\n")

    def _record_to_row(self, seq: int, rec: DetailRecord) -> dict:
        return {
            "seq": seq,
            "sourcePageUrl": rec.source_page_url,
            "detailUrl": rec.detail_url,
            "title": rec.title,
            "projectCode": rec.project_code,
            "coverUrl": rec.cover_url,
            "coverUrlRelative": rec.cover_url_relative,
            "downloadUrl": rec.download_url,
            "categories": rec.categories,
            "tags": rec.tags,
            "entryType": rec.entry_type,
            "crawlStatus": rec.crawl_status,
            "skipReason": rec.skip_reason,
        }

    def _download_cover(self, client: PoliteClient, rec: DetailRecord) -> bool:
        """Optionally download cover image; default is URL-only (no download)."""
        # Always keep coverUrl; leave coverUrlRelative empty unless explicitly downloading.
        rec.cover_url_relative = rec.cover_url_relative or ""
        if not self.config.download_images or not rec.cover_url:
            return False
        code = _safe_name(rec.project_code) if rec.project_code else ""
        slug = _safe_name(slug_from_url(rec.detail_url))
        # Unique per detail page (code prefix when available for human browsing).
        name = f"{code}_{slug}" if code else slug
        ext = _ext_from_url(rec.cover_url)
        dest = self.config.image_dir / f"{name}{ext}"
        rel = f"data/ameede/images/{name}{ext}"
        wrote = False
        if not dest.exists() or dest.stat().st_size == 0:
            client.download_file(rec.cover_url, dest)
            wrote = True
        rec.cover_url_relative = rel
        return wrote

    def _flush_xlsx(self) -> None:
        if not self.config.write_xlsx:
            return
        rows = read_csv_rows(self.config.csv_path)
        for i, row in enumerate(rows, start=1):
            row["seq"] = i
        write_xlsx(self.config.xlsx_path, rows)
        logger.info("Flushed Excel (%s rows) → %s", len(rows), self.config.xlsx_path)

    def _write_progress_summary(self) -> dict:
        csv_rows = read_csv_rows(self.config.csv_path)
        image_count = 0
        if self.config.image_dir.exists():
            image_count = sum(
                1
                for p in self.config.image_dir.iterdir()
                if p.is_file() and p.name != ".gitkeep" and not p.name.endswith(".part")
            )
        summary = {
            "listed_count": self.state.get("listed_count"),
            "detail_rows": len(csv_rows),
            "success_count": self.state.get("success_count"),
            "skipped_count": self.state.get("skipped_count"),
            "failed_count": self.state.get("failed_count"),
            "images_on_disk": image_count,
            "images_downloaded": self.state.get("images_downloaded"),
            "next_page": self.state.get("next_page"),
            "max_page": self.state.get("max_page"),
            "page_complete": self.state.get("page_complete"),
            "csv": str(self.config.csv_path.resolve()),
            "xlsx": str(self.config.xlsx_path.resolve()) if self.config.write_xlsx else None,
            "images": str(self.config.image_dir.resolve()),
            "checkpoint": str(self.config.checkpoint_path.resolve()),
            "fields": FIELDNAMES,
        }
        path = self.config.output_dir / "run_summary.json"
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary

    def run(self) -> dict:
        cfg = self.config
        cfg.output_dir.mkdir(parents=True, exist_ok=True)
        cfg.image_dir.mkdir(parents=True, exist_ok=True)
        cfg.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

        processed = self._processed()
        existing_rows = read_csv_rows(cfg.csv_path)
        for row in existing_rows:
            u = row.get("detailUrl") or ""
            if u:
                processed.add(u)
        # Keep checkpoint URL list in sync with CSV on resume.
        self.state["processed_detail_urls"] = list(processed)
        seq = len(existing_rows)
        success_rows_added = 0
        pages_since_xlsx = 0
        stop_requested = False

        with PoliteClient(min_delay=cfg.min_delay, max_delay=cfg.max_delay) as client:
            page = int(self.state.get("next_page") or cfg.start_page)
            max_page = self.state.get("max_page")

            while not stop_requested:
                if cfg.max_pages is not None and (page - cfg.start_page + 1) > cfg.max_pages:
                    logger.info("Stopping: reached --max-pages=%s", cfg.max_pages)
                    break
                if max_page is not None and page > int(max_page):
                    logger.info("Stopping: passed last page %s", max_page)
                    break
                if cfg.max_items is not None and success_rows_added >= cfg.max_items:
                    logger.info("Stopping: reached --max-items=%s", cfg.max_items)
                    break

                list_url = page_url_for(cfg.category_url, page)
                logger.info("List page %s: %s", page, list_url)
                try:
                    final_url, html = client.get_text(list_url)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Failed list page %s: %s", page, exc)
                    self.state["failed_count"] = int(self.state.get("failed_count") or 0) + 1
                    self.state["page_complete"] = False
                    self.state["next_page"] = page
                    self._save_checkpoint()
                    self._write_progress_summary()
                    # Retry same page next resume; advance once to avoid hard stuck loops
                    # after repeated failures — leave page as-is for resume.
                    page += 1
                    self.state["next_page"] = page
                    self._save_checkpoint()
                    continue

                items, detected_max = parse_list_page(html, final_url)
                if detected_max:
                    max_page = detected_max
                    self.state["max_page"] = detected_max

                if not items:
                    logger.info("No items on page %s; treating as end.", page)
                    self.state["page_complete"] = True
                    self.state["next_page"] = page + 1
                    self._save_checkpoint()
                    break

                # listed_count ≈ unique URLs observed on list pages so far
                self.state["listed_count"] = max(
                    int(self.state.get("listed_count") or 0),
                    len(processed) + sum(1 for it in items if it.detail_url not in processed),
                )

                page_interrupted = False
                items_done_on_page = 0
                items_todo_on_page = sum(1 for it in items if it.detail_url not in processed)

                for item in items:
                    if cfg.max_items is not None and success_rows_added >= cfg.max_items:
                        page_interrupted = True
                        stop_requested = True
                        break
                    if item.detail_url in processed:
                        continue

                    if item.entry_type in {"download", "ad"}:
                        seq_skip = int(self.state.get("skipped_count") or 0) + 1
                        rec = DetailRecord(
                            source_page_url=item.source_page_url,
                            detail_url=item.detail_url,
                            title=item.list_title,
                            project_code="",
                            cover_url=item.list_image_url or "",
                            entry_type=item.entry_type,
                            crawl_status="skipped",
                            skip_reason=(
                                "download_entry_not_source_record"
                                if item.entry_type == "download"
                                else "ad_or_external_not_source_record"
                            ),
                        )
                        append_csv(cfg.skipped_csv_path, [self._record_to_row(seq_skip, rec)])
                        self.state["skipped_count"] = seq_skip
                        self._mark_processed(item.detail_url, processed)
                        items_done_on_page += 1
                        self.state["page_complete"] = False
                        self.state["next_page"] = page
                        self._save_checkpoint()
                        continue

                    try:
                        detail_final, detail_html = client.get_text(item.detail_url)
                        rec = parse_detail_page(detail_html, detail_final, item.source_page_url)
                        if rec.entry_type != "detail":
                            seq_skip = int(self.state.get("skipped_count") or 0) + 1
                            append_csv(cfg.skipped_csv_path, [self._record_to_row(seq_skip, rec)])
                            self.state["skipped_count"] = seq_skip
                            self._mark_processed(item.detail_url, processed)
                            items_done_on_page += 1
                            self.state["next_page"] = page
                            self.state["page_complete"] = False
                            self._save_checkpoint()
                            continue

                        if not rec.cover_url and item.list_image_url:
                            rec.cover_url = item.list_image_url
                            if rec.crawl_status == "partial" and rec.title:
                                rec.crawl_status = "success"
                                rec.skip_reason = ""

                        wrote = self._download_cover(client, rec)
                        if wrote:
                            self.state["images_downloaded"] = int(self.state.get("images_downloaded") or 0) + 1
                        seq += 1
                        append_csv(cfg.csv_path, [self._record_to_row(seq, rec)])
                        success_rows_added += 1
                        self.state["success_count"] = int(self.state.get("success_count") or 0) + 1
                        self._mark_processed(item.detail_url, processed)
                        items_done_on_page += 1
                        if seq % 25 == 0:
                            logger.info(
                                "Progress detail=%s images=%s page=%s/%s",
                                seq,
                                self.state.get("images_downloaded"),
                                page,
                                max_page,
                            )
                            self._write_progress_summary()
                        else:
                            logger.info("OK [%s] %s", seq, rec.title[:80])
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("Detail failed %s: %s", item.detail_url, exc)
                        seq += 1
                        fail = DetailRecord(
                            source_page_url=item.source_page_url,
                            detail_url=item.detail_url,
                            title=item.list_title,
                            project_code="",
                            cover_url=item.list_image_url or "",
                            entry_type="detail",
                            crawl_status="failed",
                            skip_reason=str(exc)[:300],
                        )
                        append_csv(cfg.csv_path, [self._record_to_row(seq, fail)])
                        self.state["failed_count"] = int(self.state.get("failed_count") or 0) + 1
                        self._mark_processed(item.detail_url, processed)
                        items_done_on_page += 1

                    # Per-item checkpoint so a hung socket cannot lose a whole page.
                    self.state["page_complete"] = False
                    self.state["next_page"] = page
                    self._save_checkpoint()

                if page_interrupted:
                    self.state["page_complete"] = False
                    self.state["next_page"] = page
                elif items_todo_on_page == 0 or items_done_on_page >= items_todo_on_page:
                    self.state["page_complete"] = True
                    page += 1
                    self.state["next_page"] = page
                    pages_since_xlsx += 1
                else:
                    self.state["page_complete"] = False
                    self.state["next_page"] = page

                self._save_checkpoint()
                self._write_progress_summary()

                if (
                    not page_interrupted
                    and self.state.get("page_complete")
                    and cfg.write_xlsx
                    and cfg.xlsx_every_pages > 0
                    and pages_since_xlsx >= cfg.xlsx_every_pages
                ):
                    self._flush_xlsx()
                    pages_since_xlsx = 0

        # Finalize Excel from full CSV.
        all_rows = read_csv_rows(cfg.csv_path)
        for i, row in enumerate(all_rows, start=1):
            row["seq"] = i
        write_csv(cfg.csv_path, all_rows)
        if cfg.write_xlsx:
            write_xlsx(cfg.xlsx_path, all_rows)

        summary = self._write_progress_summary()
        summary["fields"] = FIELDNAMES
        return summary
