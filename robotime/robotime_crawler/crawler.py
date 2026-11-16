"""Robotime Online collection crawler: one row per product, image download, checkpoints."""

from __future__ import annotations

import json
import logging
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .api import RobotimeApi
from .export import FIELDNAMES, append_csv, read_csv_rows, write_csv, write_xlsx
from .http_client import PoliteClient
from .parse import ProductRecord, record_from_product

logger = logging.getLogger(__name__)


@dataclass
class CrawlConfig:
    output_dir: Path = Path("data/robotime")
    image_dir: Path = Path("data/robotime/images")
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


def _ext_from_url(url: str) -> str:
    path = urlparse(url).path
    suffix = Path(path).suffix.lower()
    if suffix in {".webp", ".jpg", ".jpeg", ".png", ".gif", ".svg"}:
        return suffix
    return mimetypes.guess_extension("image/jpeg") or ".jpg"


def _safe_name(text: str) -> str:
    text = re.sub(r"[^\w.\-]+", "_", text.strip(), flags=re.U)
    return text.strip("_")[:100] or "item"


class RobotimeCrawler:
    def __init__(self, config: CrawlConfig) -> None:
        self.config = config
        self.state = {
            "next_page": config.start_page,
            "processed_handles": [],
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
        if not self.config.download_images:
            return
        urls = [u for u in (rec.imageUrls or "").split("|") if u]
        if not urls and rec.coverUrl:
            urls = [rec.coverUrl]
        if self.config.max_images_per_item is not None:
            urls = urls[: self.config.max_images_per_item]

        paths: list[str] = []
        cover_rel = ""
        for idx, url in enumerate(urls):
            ext = _ext_from_url(url)
            name = f"{_safe_name(rec.handle or rec.productId or 'item')}_{idx:02d}{ext}"
            dest = self.config.image_dir / name
            rel = f"data/robotime/images/{name}"
            try:
                if not dest.exists() or dest.stat().st_size == 0:
                    client.download_file(url, dest)
                paths.append(rel)
                if idx == 0:
                    cover_rel = rel
            except Exception as exc:  # noqa: BLE001
                logger.warning("Image download failed %s: %s", url, exc)
        rec.coverUrlRelative = cover_rel
        rec.imagePaths = "|".join(paths)

    def run(self) -> dict:
        cfg = self.config
        cfg.output_dir.mkdir(parents=True, exist_ok=True)
        cfg.image_dir.mkdir(parents=True, exist_ok=True)
        cfg.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

        processed = set(self.state.get("processed_handles") or [])
        existing = read_csv_rows(cfg.csv_path)
        for row in existing:
            h = row.get("handle") or ""
            if h:
                processed.add(h)
        seq = len(existing)
        success_added = 0

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
                        logger.info("OK [%s] %s", seq, rec.title[:80])
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
                self._save_checkpoint()

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
            "next_page": self.state.get("next_page"),
            "csv": str(cfg.csv_path),
            "xlsx": str(cfg.xlsx_path) if cfg.write_xlsx else None,
            "images": str(cfg.image_dir),
            "checkpoint": str(cfg.checkpoint_path),
            "fields": FIELDNAMES,
            "currency": currency,
            "note": "One row per product; variants are not split",
        }
        (cfg.output_dir / "run_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return summary
