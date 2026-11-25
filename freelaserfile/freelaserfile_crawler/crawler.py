"""FreeLaserFile list→detail crawler with image download and checkpoints."""

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

import httpx

from .api import FreeLaserFileApi
from .export import FIELDNAMES, append_csv, read_csv_rows, write_csv, write_xlsx
from .http_client import PoliteClient
from .parse import DesignRecord, record_from_payload

logger = logging.getLogger(__name__)


@dataclass
class CrawlConfig:
    output_dir: Path = Path("data/freelaserfile")
    image_dir: Path = Path("data/freelaserfile/images")
    snapshot_dir: Path | None = None  # live delivery mirror (CSV/xlsx/PROGRESS)
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
    snapshot_every_pages: int = 2  # rewrite xlsx + mirror every N pages


def _ext_from_url(url: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in {".webp", ".jpg", ".jpeg", ".png", ".gif", ".svg"}:
        return suffix
    return mimetypes.guess_extension("image/webp") or ".webp"


def _safe_name(text: str) -> str:
    text = re.sub(r"[^\w.\-]+", "_", text.strip(), flags=re.U)
    return text.strip("_")[:100] or "item"


def _failure_reason(exc: BaseException, url: str) -> tuple[str, str]:
    """Return (status, reason) for download failure documentation."""
    ext = _ext_from_url(url)
    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        code = str(exc.response.status_code)
        return code, f"HTTP {code} ext={ext}"
    return "error", f"{type(exc).__name__}: {exc} ext={ext}"


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
            "cover_ok": 0,
            "cover_fail": 0,
            "pages_since_snapshot": 0,
        }
        self.download_failures: list[dict] = []
        if config.resume:
            self._load_checkpoint()
            self._load_failures()

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

    def _failures_path(self) -> Path:
        return self.config.output_dir / "IMAGE_DOWNLOAD_FAILURES.json"

    def _load_failures(self) -> None:
        path = self._failures_path()
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                self.download_failures = data
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not load IMAGE_DOWNLOAD_FAILURES.json: %s", exc)

    def _save_failures(self) -> None:
        path = self._failures_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.download_failures, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _record_failure(
        self,
        *,
        slug: str,
        kind: str,
        url: str,
        exc: BaseException,
    ) -> str:
        status, reason = _failure_reason(exc, url)
        entry = {
            "slug": slug,
            "kind": kind,
            "url": url,
            "status": status,
            "extension": _ext_from_url(url),
            "reason": reason,
            "at": datetime.now(timezone.utc).isoformat(),
        }
        self.download_failures.append(entry)
        logger.warning("Image download failed %s: %s", url, reason)
        return reason

    def _progress_payload(self, *, complete: bool = False) -> dict:
        cfg = self.config
        csv_rows = 0
        if cfg.csv_path.exists():
            with cfg.csv_path.open("r", encoding="utf-8") as f:
                csv_rows = max(0, sum(1 for _ in f) - 1)
        return {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "complete": complete,
            "success_count": self.state.get("success_count"),
            "csv_rows": csv_rows,
            "failed_count": self.state.get("failed_count"),
            "cover_ok": self.state.get("cover_ok"),
            "cover_fail": self.state.get("cover_fail"),
            "image_download_failures": len(self.download_failures),
            "next_page": self.state.get("next_page"),
            "total_pages": self.state.get("total_pages"),
            "api_total": self.state.get("total"),
            "download_images": cfg.download_images,
            "csv": str(cfg.csv_path),
            "xlsx": str(cfg.xlsx_path) if cfg.write_xlsx else None,
            "snapshot_dir": str(cfg.snapshot_dir) if cfg.snapshot_dir else None,
            "note": "No per-design author/avatar on FreeLaserFile; coverUrl/coverUrlRelative only.",
        }

    def _write_progress(self, *, complete: bool = False, rewrite_xlsx: bool = False) -> None:
        cfg = self.config
        progress = self._progress_payload(complete=complete)
        progress_path = cfg.output_dir / "PROGRESS.json"
        progress_path.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")
        self._save_failures()

        if rewrite_xlsx and cfg.write_xlsx and cfg.csv_path.exists():
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
            for name in (
                "freelaserfile.csv",
                "PROGRESS.json",
                "IMAGE_DOWNLOAD_FAILURES.json",
                "run_summary.json",
            ):
                src = cfg.output_dir / name
                if src.exists():
                    shutil.copy2(src, snap / name)
            if cfg.write_xlsx and cfg.xlsx_path.exists():
                shutil.copy2(cfg.xlsx_path, snap / "freelaserfile.xlsx")
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
                    except OSError as copy_exc:
                        logger.warning("Snapshot image copy failed %s: %s", f.name, copy_exc)
        except OSError as exc:
            logger.warning("Snapshot image sync failed (non-fatal): %s", exc)

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
        """Persist cover (and optional extra gallery files); keep full imageUrls from API."""
        if not self.config.download_images:
            return
        covers_dir = self.config.image_dir / "covers"
        covers_dir.mkdir(parents=True, exist_ok=True)
        # avatars/ reserved for parity with Instructables live layout; site has no authors.
        (self.config.image_dir / "avatars").mkdir(parents=True, exist_ok=True)

        # Preserve full gallery URLs from parse; only download cover by default.
        api_image_urls = [u for u in (rec.image_urls or "").split("|") if u]
        candidate_groups = rec.extra.get("imageCandidates") if rec.extra else None
        if not candidate_groups:
            urls = list(api_image_urls)
            if not urls and rec.cover_url:
                urls = [rec.cover_url]
            candidate_groups = [[u] for u in urls]

        # Default: cover only (Instructables bar). Set --max-images-per-item >1 for extras.
        limit = self.config.max_images_per_item
        if limit is None:
            limit = 1
        candidate_groups = candidate_groups[: max(1, limit)]

        paths: list[str] = []
        cover_rel = ""
        fail_notes: list[str] = []
        for idx, candidates in enumerate(candidate_groups):
            saved = False
            last_reason = ""
            for url in candidates:
                ext = _ext_from_url(url)
                name = f"{_safe_name(rec.slug or rec.design_id or 'item')}_{idx:02d}{ext}"
                dest = covers_dir / name
                rel = f"images/covers/{name}"
                try:
                    if not dest.exists() or dest.stat().st_size == 0:
                        client.download_file(url, dest)
                    paths.append(rel)
                    if idx == 0:
                        cover_rel = rel
                        rec.cover_url = url
                        self.state["cover_ok"] = int(self.state.get("cover_ok") or 0) + 1
                    saved = True
                    break
                except Exception as exc:  # noqa: BLE001
                    kind = "cover" if idx == 0 else "gallery"
                    last_reason = self._record_failure(
                        slug=rec.slug or "",
                        kind=kind,
                        url=url,
                        exc=exc,
                    )
                    continue
            if not saved:
                fail_notes.append(last_reason or f"all_candidates_failed idx={idx}")
                if idx == 0:
                    self.state["cover_fail"] = int(self.state.get("cover_fail") or 0) + 1
                logger.warning("All image candidates failed for %s idx=%s", rec.slug, idx)

        rec.cover_url_relative = cover_rel
        rec.image_paths = "|".join(paths)
        if api_image_urls:
            rec.image_urls = "|".join(api_image_urls)
        elif rec.cover_url:
            rec.image_urls = rec.cover_url
        if fail_notes and not cover_rel:
            # Keep crawl success for metadata; surface download reason like Instructables.
            note = "; ".join(fail_notes)[:300]
            if rec.crawl_status == "success":
                rec.crawl_status = "partial"
            if not rec.skip_reason:
                rec.skip_reason = f"cover_download_failed: {note}"
            elif "cover_download_failed" not in rec.skip_reason:
                rec.skip_reason = f"{rec.skip_reason}; cover_download_failed: {note}"[:300]

    def run(self) -> dict:
        cfg = self.config
        cfg.output_dir.mkdir(parents=True, exist_ok=True)
        cfg.image_dir.mkdir(parents=True, exist_ok=True)
        (cfg.image_dir / "covers").mkdir(parents=True, exist_ok=True)
        (cfg.image_dir / "avatars").mkdir(parents=True, exist_ok=True)
        cfg.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

        processed = set(self.state.get("processed_slugs") or [])
        existing_rows = read_csv_rows(cfg.csv_path)
        for row in existing_rows:
            slug = row.get("slug") or ""
            if slug:
                processed.add(slug)
        seq = len(existing_rows)
        success_added = 0
        started_at = datetime.now(timezone.utc)

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
                        rec = record_from_payload(
                            api,
                            payload,
                            source_page_url=listing.source_page_url,
                        )
                        self._download_images(client, rec)
                        seq += 1
                        page_rows.append(self._record_to_row(seq, rec))
                        success_added += 1
                        self.state["success_count"] = int(self.state.get("success_count") or 0) + 1
                        processed.add(slug)
                        title_safe = (rec.title[:80]).encode("ascii", "replace").decode("ascii")
                        logger.info("OK [%s] %s", seq, title_safe)
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
                pages_since = int(self.state.get("pages_since_snapshot") or 0) + 1
                self.state["pages_since_snapshot"] = pages_since
                self._save_checkpoint()

                every = max(1, int(cfg.snapshot_every_pages or 2))
                if pages_since >= every:
                    self._write_progress(rewrite_xlsx=True)
                    self.state["pages_since_snapshot"] = 0
                    self._save_checkpoint()
                else:
                    self._write_progress(rewrite_xlsx=False)

                # Rough ETA for operators.
                api_total = int(self.state.get("total") or 0) or None
                done = int(self.state.get("success_count") or 0) + int(self.state.get("failed_count") or 0)
                elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
                if api_total and done and elapsed > 0:
                    rate = done / elapsed
                    remain = max(0, api_total - done)
                    eta_s = remain / rate if rate > 0 else 0
                    logger.info(
                        "Progress %s/%s (%.1f%%) rate=%.2f/s ETA~%.0fs cover_ok=%s cover_fail=%s",
                        done,
                        api_total,
                        100.0 * done / api_total,
                        rate,
                        eta_s,
                        self.state.get("cover_ok"),
                        self.state.get("cover_fail"),
                    )

        all_rows = read_csv_rows(cfg.csv_path)
        for i, row in enumerate(all_rows, start=1):
            row["seq"] = i
        write_csv(cfg.csv_path, all_rows)
        if cfg.write_xlsx:
            write_xlsx(cfg.xlsx_path, all_rows)

        summary = {
            "success_rows": len(all_rows),
            "failed_count": self.state.get("failed_count"),
            "cover_ok": self.state.get("cover_ok"),
            "cover_fail": self.state.get("cover_fail"),
            "image_download_failures": len(self.download_failures),
            "next_page": self.state.get("next_page"),
            "total_pages": self.state.get("total_pages"),
            "api_total": self.state.get("total"),
            "csv": str(cfg.csv_path),
            "xlsx": str(cfg.xlsx_path) if cfg.write_xlsx else None,
            "images": str(cfg.image_dir),
            "checkpoint": str(cfg.checkpoint_path),
            "fields": FIELDNAMES,
            "download_images": cfg.download_images,
            "upload_base_note": (
                "images from backend uploads CDN; originals often 404 → watermarked/medium"
            ),
            "author_note": (
                "FreeLaserFile has no per-design author/avatar (site Organization only); "
                "fields are coverUrl/coverUrlRelative + imagePaths (Ameede-equivalent)."
            ),
            "complete": True,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        (cfg.output_dir / "run_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (cfg.output_dir / "CRAWL_COMPLETE.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self._save_failures()
        self._write_progress(complete=True, rewrite_xlsx=False)
        if cfg.snapshot_dir:
            snap = cfg.snapshot_dir
            try:
                snap.mkdir(parents=True, exist_ok=True)
                for name in (
                    "run_summary.json",
                    "CRAWL_COMPLETE.json",
                    "IMAGE_DOWNLOAD_FAILURES.json",
                ):
                    src = cfg.output_dir / name
                    if src.exists():
                        shutil.copy2(src, snap / name)
            except OSError as exc:
                logger.warning("Final snapshot copy failed (non-fatal): %s", exc)
        return summary
