#!/usr/bin/env python3
"""CLI for the Ameede Vector CNC-Laser crawler."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from ameede_crawler.crawler import DEFAULT_CATEGORY, AmeedeCrawler, CrawlConfig


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Crawl Ameede Vector CNC-Laser category: list → detail → local images. "
            "Ads and /download/ entries are skipped as source records."
        )
    )
    p.add_argument("--category-url", default=DEFAULT_CATEGORY, help="Category entry URL")
    p.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/ameede"),
        help="Output directory for CSV/XLSX/summary (default: data/ameede)",
    )
    p.add_argument(
        "--image-dir",
        type=Path,
        default=Path("data/ameede/images"),
        help="Directory for downloaded images (default: data/ameede/images)",
    )
    p.add_argument("--max-pages", type=int, default=None, help="Stop after N list pages")
    p.add_argument("--max-items", type=int, default=None, help="Stop after N detail records")
    p.add_argument("--start-page", type=int, default=1, help="First list page (default 1)")
    p.add_argument("--min-delay", type=float, default=0.8, help="Min seconds between requests")
    p.add_argument("--max-delay", type=float, default=1.6, help="Max seconds between requests")
    p.add_argument("--no-resume", action="store_true", help="Ignore checkpoint / do not resume")
    p.add_argument("--fresh", action="store_true", help="Delete prior outputs/checkpoint then crawl")
    p.add_argument("--no-images", action="store_true", help="Do not download images (debug only)")
    p.add_argument("--no-xlsx", action="store_true", help="Skip Excel export")
    p.add_argument(
        "--xlsx-every-pages",
        type=int,
        default=25,
        help="Flush Excel every N completed list pages (0=only at end)",
    )
    p.add_argument(
        "--sample",
        type=int,
        default=None,
        help="Convenience: crawl N detail items from page 1 (sets --max-items and --fresh)",
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
    csv_path = output_dir / "ameede.csv"
    xlsx_path = output_dir / "ameede.xlsx"
    skipped = output_dir / "ameede_skipped.csv"

    max_items = args.max_items
    fresh = args.fresh
    if args.sample is not None:
        max_items = args.sample
        fresh = True

    if fresh:
        for path in [checkpoint, csv_path, xlsx_path, skipped, output_dir / "run_summary.json"]:
            if path.exists():
                path.unlink()
        if image_dir.exists():
            for f in image_dir.glob("*"):
                if f.is_file():
                    f.unlink()

    config = CrawlConfig(
        category_url=args.category_url,
        output_dir=output_dir,
        image_dir=image_dir,
        checkpoint_path=checkpoint,
        csv_path=csv_path,
        xlsx_path=xlsx_path,
        skipped_csv_path=skipped,
        min_delay=args.min_delay,
        max_delay=args.max_delay,
        max_pages=args.max_pages,
        max_items=max_items,
        start_page=args.start_page,
        resume=not args.no_resume and not fresh,
        download_images=not args.no_images,
        write_xlsx=not args.no_xlsx,
        xlsx_every_pages=args.xlsx_every_pages,
    )

    summary = AmeedeCrawler(config).run()
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
