"""Instructables dual-keyword crawler with dedupe, images, and checkpoints."""

from __future__ import annotations

import json
import logging
import mimetypes
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from .detail import DetailRecord, fetch_detail
from .export import (
    FIELDNAMES,
    append_csv,
    ensure_csv_schema,
    read_csv_rows,
    write_csv,
    write_xlsx,
)
from .http_client import PoliteClient
from .lockutil import file_lock
from .search import TypesenseSearch

logger = logging.getLogger(__name__)

DEFAULT_KEYWORDS = ("laser cut", "laser engrave")


@dataclass
class CrawlConfig:
    keywords: tuple[str, ...] = DEFAULT_KEYWORDS
    projects_scope: str = "featured"  # matches V1.5 ~7.5k + ~1.1k
    output_dir: Path = Path("data/instructables")
    image_dir: Path = Path("data/instructables/images")
    snapshot_dir: Path | None = None  # live delivery mirror (CSV/xlsx/PROGRESS)
    checkpoint_path: Path = Path("data/instructables/checkpoints/state.json")
    csv_path: Path = Path("data/instructables/instructables.csv")
    xlsx_path: Path = Path("data/instructables/instructables.xlsx")
    skipped_csv_path: Path = Path("data/instructables/instructables_skipped.csv")
    min_delay: float = 0.6
    max_delay: float = 1.4
    per_page: int = 60
    max_items: int | None = None
    max_pages_per_keyword: int | None = None
    max_images_per_item: int | None = None
    resume: bool = True
    download_images: bool = True  # cover + author avatar to disk (Craftseek contract)
    write_xlsx: bool = True
    snapshot_every_pages: int = 5  # rewrite xlsx + mirror every N pages


def _ext_from_url(url: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in {".webp", ".jpg", ".jpeg", ".png", ".gif", ".svg"}:
        return suffix
    guess = mimetypes.guess_extension("image/jpeg")
    return guess or ".jpg"


def _safe_name(text: str) -> str:
    text = re.sub(r"[^\w.\-]+", "_", text.strip(), flags=re.U)
    return text.strip("_")[:100] or "item"


class InstructablesCrawler:
    def __init__(self, config: CrawlConfig) -> None:
        self.config = config
        self.state = {
            "keyword_index": 0,
            "page": 1,
            "processed_detail_urls": [],
            "keyword_found": {},
            "success_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "duplicate_count": 0,
            "pages_since_snapshot": 0,
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
            "Resumed checkpoint keyword_index=%s page=%s success=%s",
            self.state.get("keyword_index"),
            self.state.get("page"),
            self.state.get("success_count"),
        )

    def _save_checkpoint(self) -> None:
        path = self.config.checkpoint_path
        path.parent.mkdir(parents=True, exist_ok=True)
        # Keep checkpoint lean: processed URLs can be large; still needed for resume.
        path.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")

    def _progress_payload(self, *, complete: bool = False) -> dict:
        cfg = self.config
        csv_rows = 0
        if cfg.csv_path.exists():
            # Fast-ish count without loading whole file into memory twice elsewhere.
            with cfg.csv_path.open("r", encoding="utf-8") as f:
                csv_rows = max(0, sum(1 for _ in f) - 1)
        return {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "complete": complete,
            "success_count": self.state.get("success_count"),
            "csv_rows": csv_rows,
            "skipped_count": self.state.get("skipped_count"),
            "failed_count": self.state.get("failed_count"),
            "duplicate_count": self.state.get("duplicate_count"),
            "keyword_found": self.state.get("keyword_found"),
            "keyword_index": self.state.get("keyword_index"),
            "page": self.state.get("page"),
            "projects_scope": cfg.projects_scope,
            "keywords": list(cfg.keywords),
            "download_images": cfg.download_images,
            "csv": str(cfg.csv_path),
            "xlsx": str(cfg.xlsx_path) if cfg.write_xlsx else None,
            "snapshot_dir": str(cfg.snapshot_dir) if cfg.snapshot_dir else None,
        }

    def _write_progress(self, *, complete: bool = False, rewrite_xlsx: bool = False) -> None:
        cfg = self.config
        progress = self._progress_payload(complete=complete)
        progress_path = cfg.output_dir / "PROGRESS.json"
        progress_path.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")

        if rewrite_xlsx and cfg.write_xlsx and cfg.csv_path.exists():
            with file_lock(cfg.output_dir / ".write.lock"):
                rows = read_csv_rows(cfg.csv_path)
                for i, row in enumerate(rows, start=1):
                    row["seq"] = i
                write_csv(cfg.csv_path, rows)
                write_xlsx(cfg.xlsx_path, rows)

        if not cfg.snapshot_dir:
            return
        snap = cfg.snapshot_dir
        try:
            snap.mkdir(parents=True, exist_ok=True)
            for name in ("instructables.csv", "instructables_skipped.csv", "PROGRESS.json"):
                src = cfg.output_dir / name
                if src.exists():
                    shutil.copy2(src, snap / name)
            if cfg.write_xlsx and cfg.xlsx_path.exists():
                shutil.copy2(cfg.xlsx_path, snap / "instructables.xlsx")
            (snap / "PROGRESS.json").write_text(
                json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            logger.warning("Snapshot metadata sync failed (non-fatal): %s", exc)
            return

        # Best-effort image mirror — store I/O errors must not abort the crawl.
        if not cfg.image_dir.exists():
            return
        try:
            for sub in ("covers", "avatars"):
                src_dir = cfg.image_dir / sub
                if not src_dir.exists():
                    continue
                dest_dir = snap / "images" / sub
                dest_dir.mkdir(parents=True, exist_ok=True)
                for f in src_dir.iterdir():
                    if not f.is_file() or f.name.endswith(".part"):
                        continue
                    target = dest_dir / f.name
                    try:
                        if not target.exists() or target.stat().st_size != f.stat().st_size:
                            shutil.copy2(f, target)
                    except OSError as exc:
                        logger.warning("Snapshot image copy failed %s: %s", f.name, exc)
        except OSError as exc:
            logger.warning("Snapshot image sync failed (non-fatal): %s", exc)

    def _record_to_row(self, seq: int, rec: DetailRecord) -> dict:
        return {
            "seq": seq,
            "sourcePageUrl": rec.source_page_url,
            "detailUrl": rec.detail_url,
            "title": rec.title,
            "author": rec.author,
            "authorAvatar": rec.author_avatar,
            "categoryPath": rec.category_path,
            "coverUrl": rec.cover_url,
            "coverUrlRelative": rec.cover_url_relative,
            "authorAvatarRelative": rec.author_avatar_relative,
            "imageUrls": rec.image_urls,
            "imagePaths": rec.image_paths,
            "favoriteCount": rec.favorite_count,
            "viewCount": rec.view_count,
            "searchKeyword": rec.search_keyword,
            "searchKeywords": rec.search_keywords,
            "projectsScope": rec.projects_scope,
            "entryType": rec.entry_type,
            "crawlStatus": rec.crawl_status,
            "skipReason": rec.skip_reason,
        }

    def _save_one_image(
        self,
        client: PoliteClient,
        *,
        url: str,
        dest: Path,
        relative: str,
    ) -> str:
        if not url:
            return ""
        if not dest.exists() or dest.stat().st_size == 0:
            try:
                client.download_file(url, dest)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Image download failed %s: %s", url, exc)
                return ""
        return relative

    def _download_images(self, client: PoliteClient, rec: DetailRecord, slug: str) -> None:
        """Persist cover + author avatar; keep gallery as imageUrls only."""
        if not self.config.download_images:
            return
        safe = _safe_name(slug)
        covers_dir = self.config.image_dir / "covers"
        avatars_dir = self.config.image_dir / "avatars"
        covers_dir.mkdir(parents=True, exist_ok=True)
        avatars_dir.mkdir(parents=True, exist_ok=True)

        cover_url = rec.cover_url or ""
        if cover_url:
            ext = _ext_from_url(cover_url)
            name = f"{safe}{ext}"
            dest = covers_dir / name
            rel = f"images/covers/{name}"
            rec.cover_url_relative = self._save_one_image(
                client, url=cover_url, dest=dest, relative=rel
            )

        avatar_url = rec.author_avatar or ""
        if avatar_url:
            ext = _ext_from_url(avatar_url)
            # Prefer author slug for avatar reuse across projects.
            avatar_key = _safe_name(rec.author) or safe
            name = f"{avatar_key}{ext}"
            dest = avatars_dir / name
            rel = f"images/avatars/{name}"
            rec.author_avatar_relative = self._save_one_image(
                client, url=avatar_url, dest=dest, relative=rel
            )

        # Optional: also download extra gallery images when capped/requested.
        if self.config.max_images_per_item is not None and self.config.max_images_per_item > 1:
            urls = [u for u in (rec.image_urls or "").split("|") if u and u != cover_url]
            extra = urls[: max(0, self.config.max_images_per_item - 1)]
            paths: list[str] = []
            if rec.cover_url_relative:
                paths.append(rec.cover_url_relative)
            for idx, url in enumerate(extra, start=1):
                ext = _ext_from_url(url)
                name = f"{safe}_{idx:02d}{ext}"
                dest = covers_dir / name
                rel = f"images/covers/{name}"
                got = self._save_one_image(client, url=url, dest=dest, relative=rel)
                if got:
                    paths.append(got)
            rec.image_paths = "|".join(paths)
        elif rec.cover_url_relative:
            rec.image_paths = rec.cover_url_relative

    def run(self) -> dict:
        cfg = self.config
        cfg.output_dir.mkdir(parents=True, exist_ok=True)
        cfg.image_dir.mkdir(parents=True, exist_ok=True)
        (cfg.image_dir / "covers").mkdir(parents=True, exist_ok=True)
        (cfg.image_dir / "avatars").mkdir(parents=True, exist_ok=True)
        cfg.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

        # Upgrade mid-run CSVs that predate authorAvatar columns.
        ensure_csv_schema(cfg.csv_path)
        ensure_csv_schema(cfg.skipped_csv_path)

        processed = set(self.state.get("processed_detail_urls") or [])
        existing_rows = read_csv_rows(cfg.csv_path)
        for row in existing_rows:
            u = row.get("detailUrl") or ""
            if u:
                processed.add(u)
        # Merge searchKeywords if we see duplicates later in full runs via processed set.
        seq = len(existing_rows)
        success_added = 0

        with PoliteClient(min_delay=cfg.min_delay, max_delay=cfg.max_delay) as client:
            searcher = TypesenseSearch(client)
            searcher.refresh_credentials()

            keywords = list(cfg.keywords)
            ki = int(self.state.get("keyword_index") or 0)
            page = int(self.state.get("page") or 1)

            while ki < len(keywords):
                keyword = keywords[ki]
                logger.info("Keyword %r scope=%s starting page=%s", keyword, cfg.projects_scope, page)

                while True:
                    if cfg.max_items is not None and success_added >= cfg.max_items:
                        break
                    if cfg.max_pages_per_keyword is not None and page > cfg.max_pages_per_keyword:
                        logger.info("Reached max pages for keyword %r", keyword)
                        break

                    result = searcher.search(
                        keyword,
                        page=page,
                        per_page=cfg.per_page,
                        projects_scope=cfg.projects_scope,
                    )
                    self.state.setdefault("keyword_found", {})[keyword] = result.found
                    logger.info(
                        "Search %r page %s: hits=%s found=%s",
                        keyword,
                        page,
                        len(result.hits),
                        result.found,
                    )

                    if not result.hits:
                        break

                    page_success: list[dict] = []
                    page_skipped: list[dict] = []

                    for hit in result.hits:
                        if cfg.max_items is not None and success_added >= cfg.max_items:
                            break
                        if hit.detail_url in processed:
                            self.state["duplicate_count"] = int(self.state.get("duplicate_count") or 0) + 1
                            continue

                        try:
                            rec = fetch_detail(client, hit)
                            if rec.entry_type != "detail":
                                skip_seq = int(self.state.get("skipped_count") or 0) + 1
                                page_skipped.append(self._record_to_row(skip_seq, rec))
                                self.state["skipped_count"] = skip_seq
                                processed.add(hit.detail_url)
                                continue

                            self._download_images(client, rec, hit.url_string)
                            seq += 1
                            page_success.append(self._record_to_row(seq, rec))
                            success_added += 1
                            self.state["success_count"] = int(self.state.get("success_count") or 0) + 1
                            processed.add(hit.detail_url)
                            logger.info("OK [%s] %s", seq, (rec.title[:80]).encode("ascii", "replace").decode("ascii"))
                        except Exception as exc:  # noqa: BLE001
                            logger.exception("Detail failed %s: %s", hit.detail_url, exc)
                            seq += 1
                            fail = DetailRecord(
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
                                entry_type="detail",
                                crawl_status="failed",
                                skip_reason=str(exc)[:300],
                            )
                            page_success.append(self._record_to_row(seq, fail))
                            self.state["failed_count"] = int(self.state.get("failed_count") or 0) + 1
                            processed.add(hit.detail_url)

                    lock = cfg.output_dir / ".write.lock"
                    with file_lock(lock):
                        if page_success:
                            append_csv(cfg.csv_path, page_success)
                        if page_skipped:
                            append_csv(cfg.skipped_csv_path, page_skipped)

                    page += 1
                    self.state["page"] = page
                    self.state["keyword_index"] = ki
                    self.state["processed_detail_urls"] = sorted(processed)
                    pages_since = int(self.state.get("pages_since_snapshot") or 0) + 1
                    self.state["pages_since_snapshot"] = pages_since
                    self._save_checkpoint()

                    every = max(1, int(cfg.snapshot_every_pages or 5))
                    if pages_since >= every:
                        self._write_progress(rewrite_xlsx=True)
                        self.state["pages_since_snapshot"] = 0
                        self._save_checkpoint()
                    else:
                        self._write_progress(rewrite_xlsx=False)

                    # End of results for this keyword.
                    if page > max(1, (result.found + result.per_page - 1) // result.per_page):
                        break

                # next keyword
                ki += 1
                page = 1
                self.state["keyword_index"] = ki
                self.state["page"] = page
                self.state["processed_detail_urls"] = sorted(processed)
                self.state["pages_since_snapshot"] = 0
                self._save_checkpoint()
                self._write_progress(rewrite_xlsx=True)

                if cfg.max_items is not None and success_added >= cfg.max_items:
                    break

        with file_lock(cfg.output_dir / ".write.lock"):
            all_rows = read_csv_rows(cfg.csv_path)
            for i, row in enumerate(all_rows, start=1):
                row["seq"] = i
            write_csv(cfg.csv_path, all_rows)
            if cfg.write_xlsx:
                write_xlsx(cfg.xlsx_path, all_rows)

        summary = {
            "success_rows": len(all_rows),
            "skipped_count": self.state.get("skipped_count"),
            "failed_count": self.state.get("failed_count"),
            "duplicate_count": self.state.get("duplicate_count"),
            "keyword_found": self.state.get("keyword_found"),
            "keyword_index": self.state.get("keyword_index"),
            "page": self.state.get("page"),
            "csv": str(cfg.csv_path),
            "xlsx": str(cfg.xlsx_path) if cfg.write_xlsx else None,
            "images": str(cfg.image_dir),
            "checkpoint": str(cfg.checkpoint_path),
            "fields": FIELDNAMES,
            "projects_scope": cfg.projects_scope,
            "keywords": list(cfg.keywords),
            "download_images": cfg.download_images,
            "complete": True,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        (cfg.output_dir / "run_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (cfg.output_dir / "CRAWL_COMPLETE.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self._write_progress(complete=True, rewrite_xlsx=False)
        if cfg.snapshot_dir:
            snap = cfg.snapshot_dir
            snap.mkdir(parents=True, exist_ok=True)
            for name in ("run_summary.json", "CRAWL_COMPLETE.json"):
                src = cfg.output_dir / name
                if src.exists():
                    shutil.copy2(src, snap / name)
        return summary
