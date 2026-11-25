"""Robotime Online collection crawler: one row per product, image download, checkpoints."""

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

from .api import RobotimeApi
from .export import FIELDNAMES, append_csv, read_csv_rows, write_csv, write_xlsx
from .http_client import PoliteClient
from .parse import ProductRecord, record_from_product

logger = logging.getLogger(__name__)


@dataclass
class CrawlConfig:
    output_dir: Path = Path("data/robotime")
    image_dir: Path = Path("data/robotime/images")
    snapshot_dir: Path | None = None  # live delivery mirror (CSV/xlsx/PROGRESS)
    checkpoint_path: Path = Path("data/robotime/checkpoints/state.json")
    csv_path: Path = Path("data/robotime/robotime.csv")
    xlsx_path: Path = Path("data/robotime/robotime.xlsx")
    min_delay: float = 0.35
    max_delay: float = 0.9
    page_limit: int = 250
    max_items: int | None = None
    max_pages: int | None = None
    start_page: int = 1
    max_images_per_item: int | None = None
    resume: bool = True
    download_images: bool = True
    write_xlsx: bool = True
    enrich_html: bool = True  # resolve final URL + JSON-LD brand/availability
    snapshot_every_pages: int = 1  # rewrite xlsx + mirror every N pages
    expected_total: int | None = None  # for ETA when Shopify JSON has no total


def _ext_from_url(url: str) -> str:
    path = urlparse(url).path
    suffix = Path(path).suffix.lower()
    if suffix in {".webp", ".jpg", ".jpeg", ".png", ".gif", ".svg"}:
        return suffix
    return mimetypes.guess_extension("image/jpeg") or ".jpg"


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


class RobotimeCrawler:
    def __init__(self, config: CrawlConfig) -> None:
        self.config = config
        self.state = {
            "next_page": config.start_page,
            "processed_handles": [],
            "success_count": 0,
            "failed_count": 0,
            "cover_ok": 0,
            "cover_fail": 0,
            "pages_since_snapshot": 0,
            "expected_total": config.expected_total,
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
        handle: str,
        kind: str,
        url: str,
        exc: BaseException,
    ) -> str:
        status, reason = _failure_reason(exc, url)
        entry = {
            "handle": handle,
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
            "expected_total": self.state.get("expected_total") or cfg.expected_total,
            "download_images": cfg.download_images,
            "csv": str(cfg.csv_path),
            "xlsx": str(cfg.xlsx_path) if cfg.write_xlsx else None,
            "snapshot_dir": str(cfg.snapshot_dir) if cfg.snapshot_dir else None,
            "note": (
                "One row per product (variants not split). "
                "No per-product author/avatar on Robotime Online; "
                "coverUrl/coverUrlRelative (+ imagePaths). avatars/ reserved empty."
            ),
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
                "robotime.csv",
                "PROGRESS.json",
                "IMAGE_DOWNLOAD_FAILURES.json",
                "run_summary.json",
                "CRAWL_COMPLETE.json",
            ):
                src = cfg.output_dir / name
                if src.exists():
                    shutil.copy2(src, snap / name)
            if cfg.write_xlsx and cfg.xlsx_path.exists():
                shutil.copy2(cfg.xlsx_path, snap / "robotime.xlsx")
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

    def _record_to_row(self, seq: int, rec: ProductRecord) -> dict:
        return {
            "seq": seq,
            "sourceUrl": rec.sourceUrl,
            "actualSourceUrl": rec.actualSourceUrl,
            "title": rec.title,
            "brand": rec.brand,
            "designer": rec.designer,
            "price": rec.price,
            "currency": rec.currency,
            "compareAtPrice": rec.compareAtPrice,
            "availabilityStatus": rec.availabilityStatus,
            "coverUrl": rec.coverUrl,
            "coverUrlRelative": rec.coverUrlRelative,
            "imageUrls": rec.imageUrls,
            "imagePaths": rec.imagePaths,
            "handle": rec.handle,
            "productId": rec.productId,
            "productType": rec.productType,
            "sku": rec.sku,
            "variantCount": rec.variantCount,
            "tags": rec.tags,
            "collectionSourcePageUrl": (rec.extra or {}).get("collectionSourcePageUrl", ""),
            "entryType": rec.entryType,
            "crawlStatus": rec.crawlStatus,
            "skipReason": rec.skipReason,
        }

    def _download_images(self, client: PoliteClient, rec: ProductRecord) -> None:
        """Persist cover (+ optional gallery). Site has no per-product author avatars."""
        if not self.config.download_images:
            return
        covers_dir = self.config.image_dir / "covers"
        covers_dir.mkdir(parents=True, exist_ok=True)
        # avatars/ reserved for parity with Instructables/Ameede live layout.
        (self.config.image_dir / "avatars").mkdir(parents=True, exist_ok=True)

        urls = [u for u in (rec.imageUrls or "").split("|") if u]
        if not urls and rec.coverUrl:
            urls = [rec.coverUrl]
        # Default: cover only (Ameede/Instructables/FLF bar). Use --max-images-per-item for gallery.
        limit = self.config.max_images_per_item
        if limit is None:
            limit = 1
        urls = urls[: max(1, limit)]

        paths: list[str] = []
        cover_rel = ""
        fail_notes: list[str] = []
        for idx, url in enumerate(urls):
            ext = _ext_from_url(url)
            name = f"{_safe_name(rec.handle or rec.productId or 'item')}_{idx:02d}{ext}"
            dest = covers_dir / name
            rel = f"images/covers/{name}"
            try:
                if not dest.exists() or dest.stat().st_size == 0:
                    client.download_file(url, dest)
                paths.append(rel)
                if idx == 0:
                    cover_rel = rel
                    self.state["cover_ok"] = int(self.state.get("cover_ok") or 0) + 1
            except Exception as exc:  # noqa: BLE001
                kind = "cover" if idx == 0 else "gallery"
                reason = self._record_failure(
                    handle=rec.handle or "",
                    kind=kind,
                    url=url,
                    exc=exc,
                )
                fail_notes.append(reason)
                if idx == 0:
                    self.state["cover_fail"] = int(self.state.get("cover_fail") or 0) + 1

        rec.coverUrlRelative = cover_rel
        rec.imagePaths = "|".join(paths)
        if fail_notes and not cover_rel:
            note = "; ".join(fail_notes)[:300]
            if rec.crawlStatus == "success":
                rec.crawlStatus = "partial"
            if not rec.skipReason:
                rec.skipReason = f"cover_download_failed: {note}"
            elif "cover_download_failed" not in rec.skipReason:
                rec.skipReason = f"{rec.skipReason}; cover_download_failed: {note}"[:300]

    def run(self) -> dict:
        cfg = self.config
        cfg.output_dir.mkdir(parents=True, exist_ok=True)
        cfg.image_dir.mkdir(parents=True, exist_ok=True)
        (cfg.image_dir / "covers").mkdir(parents=True, exist_ok=True)
        (cfg.image_dir / "avatars").mkdir(parents=True, exist_ok=True)
        cfg.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

        processed = set(self.state.get("processed_handles") or [])
        existing = read_csv_rows(cfg.csv_path)
        for row in existing:
            h = row.get("handle") or ""
            if h:
                processed.add(h)
        seq = len(existing)
        success_added = 0
        started_at = datetime.now(timezone.utc)
        if cfg.expected_total is not None:
            self.state["expected_total"] = cfg.expected_total

        with PoliteClient(min_delay=cfg.min_delay, max_delay=cfg.max_delay) as client:
            api = RobotimeApi(client)
            currency = api.refresh_shop_currency()
            page = int(self.state.get("next_page") or cfg.start_page)

            while True:
                if cfg.max_pages is not None and (page - cfg.start_page + 1) > cfg.max_pages:
                    logger.info("Stopping: reached --max-pages")
                    break
                if cfg.max_items is not None and success_added >= cfg.max_items:
                    logger.info("Stopping: reached --max-items")
                    break

                logger.info("Collection page %s", page)
                listing = api.list_products(page=page, limit=cfg.page_limit)
                if not listing.products:
                    logger.info("Empty page %s; done", page)
                    break

                page_rows: list[dict] = []
                for product in listing.products:
                    if cfg.max_items is not None and success_added >= cfg.max_items:
                        break
                    handle = (product.get("handle") or "").strip()
                    if not handle or handle in processed:
                        continue
                    try:
                        enrich = {}
                        if cfg.enrich_html:
                            enrich = api.enrich_from_html(handle)
                        rec = record_from_product(
                            product,
                            collection_source_page_url=listing.source_page_url,
                            currency=currency,
                            enrich=enrich,
                        )
                        self._download_images(client, rec)
                        seq += 1
                        page_rows.append(self._record_to_row(seq, rec))
                        success_added += 1
                        self.state["success_count"] = int(self.state.get("success_count") or 0) + 1
                        processed.add(handle)
                        title_safe = (rec.title[:80]).encode("ascii", "replace").decode("ascii")
                        logger.info("OK [%s] %s", seq, title_safe)
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("Failed %s: %s", handle, exc)
                        seq += 1
                        fail = ProductRecord(
                            sourceUrl=api.product_url(handle),
                            actualSourceUrl=api.product_url(handle),
                            title=product.get("title") or "",
                            handle=handle,
                            productId=str(product.get("id") or ""),
                            crawlStatus="failed",
                            skipReason=str(exc)[:300],
                            extra={"collectionSourcePageUrl": listing.source_page_url},
                        )
                        page_rows.append(self._record_to_row(seq, fail))
                        self.state["failed_count"] = int(self.state.get("failed_count") or 0) + 1
                        processed.add(handle)

                if page_rows:
                    append_csv(cfg.csv_path, page_rows)

                page += 1
                self.state["next_page"] = page
                self.state["processed_handles"] = sorted(processed)
                pages_since = int(self.state.get("pages_since_snapshot") or 0) + 1
                self.state["pages_since_snapshot"] = pages_since
                self._save_checkpoint()

                every = max(1, int(cfg.snapshot_every_pages or 1))
                if pages_since >= every:
                    self._write_progress(rewrite_xlsx=True)
                    self.state["pages_since_snapshot"] = 0
                    self._save_checkpoint()
                else:
                    self._write_progress(rewrite_xlsx=False)

                expected = int(self.state.get("expected_total") or 0) or None
                done = int(self.state.get("success_count") or 0) + int(self.state.get("failed_count") or 0)
                elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
                if expected and done and elapsed > 0:
                    rate = done / elapsed
                    remain = max(0, expected - done)
                    eta_s = remain / rate if rate > 0 else 0
                    logger.info(
                        "Progress %s/%s (%.1f%%) rate=%.2f/s ETA~%.0fs cover_ok=%s cover_fail=%s",
                        done,
                        expected,
                        100.0 * done / expected,
                        rate,
                        eta_s,
                        self.state.get("cover_ok"),
                        self.state.get("cover_fail"),
                    )

                if len(listing.products) < cfg.page_limit:
                    break

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
            "expected_total": self.state.get("expected_total") or cfg.expected_total,
            "csv": str(cfg.csv_path),
            "xlsx": str(cfg.xlsx_path) if cfg.write_xlsx else None,
            "images": str(cfg.image_dir),
            "checkpoint": str(cfg.checkpoint_path),
            "fields": FIELDNAMES,
            "currency": currency,
            "download_images": cfg.download_images,
            "note": "One row per product; variants are not split",
            "author_note": (
                "Robotime Online has no per-product author/avatar (Shopify vendor/brand only); "
                "fields are coverUrl/coverUrlRelative + imagePaths. images/avatars/ reserved empty."
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
