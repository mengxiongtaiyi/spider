#!/usr/bin/env python3
"""CLI for the Robotime Online crawler."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from robotime_crawler.crawler import CrawlConfig, RobotimeCrawler


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Crawl Robotime Online /collections/all via Shopify JSON. "
            "One row per product (variants not split). Downloads images locally."
        )
    )
    p.add_argument("--output-dir", type=Path, default=Path("data/robotime"))
    p.add_argument("--image-dir", type=Path, default=Path("data/robotime/images"))
    p.add_argument("--page-limit", type=int, default=250)
    p.add_argument("--max-items", type=int, default=None)
    p.add_argument("--max-pages", type=int, default=None)
    p.add_argument("--start-page", type=int, default=1)
    p.add_argument("--max-images-per-item", type=int, default=None)
    p.add_argument("--min-delay", type=float, default=0.35)
    p.add_argument("--max-delay", type=float, default=0.9)
    p.add_argument("--no-resume", action="store_true")
    p.add_argument("--fresh", action="store_true")
    p.add_argument("--no-images", action="store_true")
    p.add_argument("--no-xlsx", action="store_true")
    p.add_argument("--no-html-enrich", action="store_true", help="Skip HTML final-URL/brand enrich")
    p.add_argument("--sample", type=int, default=None, help="Crawl N products (fresh)")
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
    csv_path = output_dir / "robotime.csv"
    xlsx_path = output_dir / "robotime.xlsx"

    max_items = args.max_items
    max_images = args.max_images_per_item
    fresh = args.fresh
    if args.sample is not None:
        max_items = args.sample
        fresh = True
        if max_images is None:
            max_images = 3

    if fresh:
        for path in [checkpoint, csv_path, xlsx_path, output_dir / "run_summary.json"]:
            if path.exists():
                path.unlink()
        if image_dir.exists():
            for f in image_dir.glob("*"):
                if f.is_file():
                    f.unlink()

    config = CrawlConfig(
        output_dir=output_dir,
        image_dir=image_dir,
        checkpoint_path=checkpoint,
        csv_path=csv_path,
        xlsx_path=xlsx_path,
        min_delay=args.min_delay,
        max_delay=args.max_delay,
        page_limit=min(args.page_limit, 250),
        max_items=max_items,
        max_pages=args.max_pages,
        start_page=args.start_page,
        max_images_per_item=max_images,
        resume=not args.no_resume and not fresh,
        download_images=not args.no_images,
        write_xlsx=not args.no_xlsx,
        enrich_html=not args.no_html_enrich,
    )
    print(RobotimeCrawler(config).run())
    return 0


if __name__ == "__main__":
    sys.exit(main())
