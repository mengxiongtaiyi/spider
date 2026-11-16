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
    download_images: bool = True
    write_xlsx: bool = True


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
            "success_count": 0,
            "skipped_count": 0,
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
        # Keep processed set as list in JSON; convert when using.
        logger.info(
            "Resumed checkpoint page=%s success=%s skipped=%s",
            self.state.get("next_page"),
            self.state.get("success_count"),
            self.state.get("skipped_count"),
        )

    def _save_checkpoint(self) -> None:
        path = self.config.checkpoint_path
        path.parent.mkdir(parents=True, exist_ok=True)
        # Cap processed URL list growth in memory-friendly way for long runs:
        # keep a set-sized list; for 25k this is fine (~few MB).
        path.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")

    def _processed(self) -> set[str]:
        return set(self.state.get("processed_detail_urls") or [])

    def _mark_processed(self, url: str) -> None:
        urls = self.state.setdefault("processed_detail_urls", [])
        if url not in urls:
            urls.append(url)

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

    def _download_cover(self, client: PoliteClient, rec: DetailRecord) -> None:
        if not self.config.download_images or not rec.cover_url:
            return
        name = _safe_name(rec.project_code or slug_from_url(rec.detail_url))
        ext = _ext_from_url(rec.cover_url)
        dest = self.config.image_dir / f"{name}{ext}"
        # Stable CSV/Excel relative path as required by contract.
        rel = f"data/ameede/images/{name}{ext}"

        if not dest.exists() or dest.stat().st_size == 0:
            client.download_file(rec.cover_url, dest)
        rec.cover_url_relative = rel

    def run(self) -> dict:
        cfg = self.config
        cfg.output_dir.mkdir(parents=True, exist_ok=True)
        cfg.image_dir.mkdir(parents=True, exist_ok=True)
        cfg.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

        processed = self._processed()
        # Existing CSV rows define next seq and avoid duplicates on resume.
        existing_rows = read_csv_rows(cfg.csv_path)
        for row in existing_rows:
            u = row.get("detailUrl") or ""
            if u:
                processed.add(u)
        seq = len(existing_rows)
        success_rows_added = 0

        with PoliteClient(min_delay=cfg.min_delay, max_delay=cfg.max_delay) as client:
            page = int(self.state.get("next_page") or cfg.start_page)
            max_page = self.state.get("max_page")

            while True:
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
                    self._save_checkpoint()
                    page += 1
                    self.state["next_page"] = page
                    continue

                items, detected_max = parse_list_page(html, final_url)
                if detected_max:
                    max_page = detected_max
                    self.state["max_page"] = detected_max

                if not items:
                    logger.info("No items on page %s; treating as end.", page)
                    break

                page_success: list[dict] = []
                page_skipped: list[dict] = []

                for item in items:
                    if cfg.max_items is not None and success_rows_added >= cfg.max_items:
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
                        row = self._record_to_row(seq_skip, rec)
                        page_skipped.append(row)
                        self.state["skipped_count"] = seq_skip
                        self._mark_processed(item.detail_url)
                        processed.add(item.detail_url)
                        continue

                    try:
                        detail_final, detail_html = client.get_text(item.detail_url)
                        rec = parse_detail_page(detail_html, detail_final, item.source_page_url)
                        if rec.entry_type != "detail":
                            seq_skip = int(self.state.get("skipped_count") or 0) + 1
                            row = self._record_to_row(seq_skip, rec)
                            page_skipped.append(row)
                            self.state["skipped_count"] = seq_skip
                            self._mark_processed(item.detail_url)
                            processed.add(item.detail_url)
                            continue

                        if not rec.cover_url and item.list_image_url:
                            rec.cover_url = item.list_image_url
                            if rec.crawl_status == "partial" and rec.title:
                                rec.crawl_status = "success"
                                rec.skip_reason = ""

                        self._download_cover(client, rec)
                        seq += 1
                        row = self._record_to_row(seq, rec)
                        page_success.append(row)
                        success_rows_added += 1
                        self.state["success_count"] = int(self.state.get("success_count") or 0) + 1
                        self._mark_processed(item.detail_url)
                        processed.add(item.detail_url)
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
                        page_success.append(self._record_to_row(seq, fail))
                        self.state["failed_count"] = int(self.state.get("failed_count") or 0) + 1
                        self._mark_processed(item.detail_url)
                        processed.add(item.detail_url)

                if page_success:
                    append_csv(cfg.csv_path, page_success)
                if page_skipped:
                    append_csv(cfg.skipped_csv_path, page_skipped)

                page += 1
                self.state["next_page"] = page
                self.state["processed_detail_urls"] = sorted(processed)
                self._save_checkpoint()

        # Finalize Excel from full CSV.
        all_rows = read_csv_rows(cfg.csv_path)
        # Re-number seq for cleanliness
        for i, row in enumerate(all_rows, start=1):
            row["seq"] = i
        write_csv(cfg.csv_path, all_rows)
        if cfg.write_xlsx:
            write_xlsx(cfg.xlsx_path, all_rows)

        summary = {
            "success_rows": len(all_rows),
            "skipped_count": self.state.get("skipped_count"),
            "failed_count": self.state.get("failed_count"),
            "next_page": self.state.get("next_page"),
            "max_page": self.state.get("max_page"),
            "csv": str(cfg.csv_path),
            "xlsx": str(cfg.xlsx_path) if cfg.write_xlsx else None,
            "images": str(cfg.image_dir),
            "checkpoint": str(cfg.checkpoint_path),
            "fields": FIELDNAMES,
        }
        summary_path = cfg.output_dir / "run_summary.json"
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary
