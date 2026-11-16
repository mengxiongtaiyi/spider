#!/usr/bin/env python3
"""CLI for the Instructables laser-cut / laser-engrave crawler."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from instructables_crawler.crawler import DEFAULT_KEYWORDS, CrawlConfig, InstructablesCrawler


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Crawl Instructables projects for 'laser cut' and 'laser engrave', "
            "dedupe by detail URL, download images, export CSV/Excel."
        )
    )
    p.add_argument(
        "--keywords",
        nargs="+",
        default=list(DEFAULT_KEYWORDS),
        help="Search keywords (default: laser cut / laser engrave)",
    )
    p.add_argument(
        "--projects-scope",
        choices=["featured", "all"],
        default="featured",
        help="Typesense projects filter; featured matches V1.5 ~8.6k totals",
    )
    p.add_argument("--output-dir", type=Path, default=Path("data/instructables"))
    p.add_argument("--image-dir", type=Path, default=Path("data/instructables/images"))
    p.add_argument("--max-items", type=int, default=None)
    p.add_argument("--max-pages-per-keyword", type=int, default=None)
    p.add_argument(
        "--max-images-per-item",
        type=int,
        default=None,
        help="Cap downloaded images per project (cover first). Default: all",
    )
    p.add_argument("--per-page", type=int, default=60)
    p.add_argument("--min-delay", type=float, default=0.6)
    p.add_argument("--max-delay", type=float, default=1.4)
    p.add_argument("--no-resume", action="store_true")
    p.add_argument("--fresh", action="store_true")
    p.add_argument("--no-images", action="store_true")
    p.add_argument("--no-xlsx", action="store_true")
    p.add_argument(
        "--sample",
        type=int,
        default=None,
        help="Convenience: crawl N items, fresh run, cap images at 3",
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
    csv_path = output_dir / "instructables.csv"
    xlsx_path = output_dir / "instructables.xlsx"
    skipped = output_dir / "instructables_skipped.csv"

    max_items = args.max_items
    max_images = args.max_images_per_item
    fresh = args.fresh
    if args.sample is not None:
        max_items = args.sample
        fresh = True
        if max_images is None:
            max_images = 3

    if fresh:
        for path in [checkpoint, csv_path, xlsx_path, skipped, output_dir / "run_summary.json"]:
            if path.exists():
                path.unlink()
        if image_dir.exists():
            for f in image_dir.glob("*"):
                if f.is_file():
                    f.unlink()

    config = CrawlConfig(
        keywords=tuple(args.keywords),
        projects_scope=args.projects_scope,
        output_dir=output_dir,
        image_dir=image_dir,
        checkpoint_path=checkpoint,
        csv_path=csv_path,
        xlsx_path=xlsx_path,
        skipped_csv_path=skipped,
        min_delay=args.min_delay,
        max_delay=args.max_delay,
        per_page=args.per_page,
        max_items=max_items,
        max_pages_per_keyword=args.max_pages_per_keyword,
        max_images_per_item=max_images,
        resume=not args.no_resume and not fresh,
        download_images=not args.no_images,
        write_xlsx=not args.no_xlsx,
    )
    summary = InstructablesCrawler(config).run()
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
