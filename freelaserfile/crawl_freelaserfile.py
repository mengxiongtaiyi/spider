#!/usr/bin/env python3
"""CLI for the FreeLaserFile crawler."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from freelaserfile_crawler.crawler import CrawlConfig, FreeLaserFileCrawler


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Crawl FreeLaserFile designs via public JSON API: list → detail → local images. "
            "Excludes sidebar `similar` recommendation images."
        )
    )
    p.add_argument("--output-dir", type=Path, default=Path("data/freelaserfile"))
    p.add_argument("--image-dir", type=Path, default=Path("data/freelaserfile/images"))
    p.add_argument(
        "--snapshot-dir",
        type=Path,
        default=None,
        help="Optional live delivery dir; receives progress CSV/xlsx/images snapshots",
    )
    p.add_argument("--page-limit", type=int, default=100, help="API page size (max 100)")
    p.add_argument("--max-items", type=int, default=None)
    p.add_argument("--max-pages", type=int, default=None)
    p.add_argument("--start-page", type=int, default=1)
    p.add_argument("--category", default=None, help="Optional category slug filter")
    p.add_argument("--tag", default=None, help="Optional tag filter (e.g. laser-cut)")
    p.add_argument("--max-images-per-item", type=int, default=None)
    p.add_argument("--min-delay", type=float, default=0.4)
    p.add_argument("--max-delay", type=float, default=1.0)
    p.add_argument("--snapshot-every-pages", type=int, default=2)
    p.add_argument("--no-resume", action="store_true")
    p.add_argument("--fresh", action="store_true")
    p.add_argument("--no-images", action="store_true")
    p.add_argument("--no-xlsx", action="store_true")
    p.add_argument("--no-detail", action="store_true", help="Skip detail API enrich (list fields only)")
    p.add_argument(
        "--sample",
        type=int,
        default=None,
        help="Convenience: crawl N items, fresh run",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    output_dir = args.output_dir
    image_dir = args.image_dir
    checkpoint = output_dir / "checkpoints" / "state.json"
    csv_path = output_dir / "freelaserfile.csv"
    xlsx_path = output_dir / "freelaserfile.xlsx"

    max_items = args.max_items
    fresh = args.fresh
    if args.sample is not None:
        max_items = args.sample
        fresh = True

    if fresh:
        for path in [
            checkpoint,
            csv_path,
            xlsx_path,
            output_dir / "run_summary.json",
            output_dir / "PROGRESS.json",
            output_dir / "CRAWL_COMPLETE.json",
            output_dir / "IMAGE_DOWNLOAD_FAILURES.json",
        ]:
            if path.exists():
                path.unlink()
        if image_dir.exists():
            for f in image_dir.rglob("*"):
                if f.is_file():
                    f.unlink()

    config = CrawlConfig(
        output_dir=output_dir,
        image_dir=image_dir,
        snapshot_dir=args.snapshot_dir,
        checkpoint_path=checkpoint,
        csv_path=csv_path,
        xlsx_path=xlsx_path,
        min_delay=args.min_delay,
        max_delay=args.max_delay,
        page_limit=min(args.page_limit, 100),
        max_items=max_items,
        max_pages=args.max_pages,
        start_page=args.start_page,
        category_slug=args.category,
        tag=args.tag,
        max_images_per_item=args.max_images_per_item,
        resume=not args.no_resume and not fresh,
        download_images=not args.no_images,
        write_xlsx=not args.no_xlsx,
        enrich_detail=not args.no_detail,
        snapshot_every_pages=max(1, args.snapshot_every_pages),
    )
    print(FreeLaserFileCrawler(config).run())
    return 0


if __name__ == "__main__":
    sys.exit(main())
