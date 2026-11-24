#!/usr/bin/env python3
"""Backfill cover + author avatar downloads for existing Instructables CSV rows.

Safe to run while a resumed crawl appends new rows: merges by detailUrl under a
file lock so concurrent appends are preserved.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from instructables_crawler.detail import DETAIL_API, is_ad_image_url
from instructables_crawler.export import (
    ensure_csv_schema,
    read_csv_rows,
    write_csv,
    write_xlsx,
)
from instructables_crawler.http_client import PoliteClient
from instructables_crawler.lockutil import file_lock

logger = logging.getLogger(__name__)


def _ext_from_url(url: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in {".webp", ".jpg", ".jpeg", ".png", ".gif", ".svg"}:
        return suffix
    return ".jpg"


def _safe_name(text: str) -> str:
    text = re.sub(r"[^\w.\-]+", "_", text.strip(), flags=re.U)
    return text.strip("_")[:100] or "item"


def _slug_from_detail(detail_url: str) -> str:
    path = urlparse(detail_url).path.strip("/")
    return path.split("/")[-1] if path else "item"


def _download(client: PoliteClient, url: str, dest: Path) -> bool:
    if not url:
        return False
    if dest.exists() and dest.stat().st_size > 0:
        return True
    try:
        client.download_file(url, dest)
        return dest.exists() and dest.stat().st_size > 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("download failed %s: %s", url, exc)
        return False


def _apply_updates(rows: list[dict], updates: dict[str, dict]) -> list[dict]:
    merged: list[dict] = []
    seen: set[str] = set()
    for r in rows:
        u = (r.get("detailUrl") or "").strip()
        if u and u in updates:
            patch = updates[u]
            for key, val in patch.items():
                if val:
                    r[key] = val
        if u:
            seen.add(u)
        merged.append(r)
    for u, patch in updates.items():
        if u and u not in seen:
            merged.append(dict(patch))
    for j, r in enumerate(merged, start=1):
        r["seq"] = j
    return merged


def backfill(
    *,
    csv_path: Path,
    image_dir: Path,
    xlsx_path: Path | None,
    snapshot_dir: Path | None,
    min_delay: float,
    max_delay: float,
    save_every: int,
) -> dict:
    ensure_csv_schema(csv_path)
    rows = read_csv_rows(csv_path)
    covers_dir = image_dir / "covers"
    avatars_dir = image_dir / "avatars"
    covers_dir.mkdir(parents=True, exist_ok=True)
    avatars_dir.mkdir(parents=True, exist_ok=True)

    updates: dict[str, dict] = {}
    stats = {
        "rows": len(rows),
        "cover_ok": 0,
        "avatar_ok": 0,
        "cover_fail": 0,
        "avatar_fail": 0,
        "avatar_fetched": 0,
        "skipped_complete": 0,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    def persist(processed: int) -> None:
        lock = csv_path.parent / ".write.lock"
        with file_lock(lock):
            latest = read_csv_rows(csv_path)
            merged = _apply_updates(latest, updates)
            write_csv(csv_path, merged)
            if xlsx_path:
                write_xlsx(xlsx_path, merged)
        stats["updated_at"] = datetime.now(timezone.utc).isoformat()
        stats["processed"] = processed
        progress_path = csv_path.parent / "IMAGE_BACKFILL_PROGRESS.json"
        progress_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
        if snapshot_dir:
            snapshot_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(csv_path, snapshot_dir / "instructables.csv")
            if xlsx_path and xlsx_path.exists():
                shutil.copy2(xlsx_path, snapshot_dir / "instructables.xlsx")
            shutil.copy2(progress_path, snapshot_dir / "IMAGE_BACKFILL_PROGRESS.json")
            for sub in ("covers", "avatars"):
                src_dir = image_dir / sub
                if not src_dir.exists():
                    continue
                dest_dir = snapshot_dir / "images" / sub
                dest_dir.mkdir(parents=True, exist_ok=True)
                for f in src_dir.iterdir():
                    if f.is_file() and not f.name.endswith(".part"):
                        target = dest_dir / f.name
                        if not target.exists() or target.stat().st_size != f.stat().st_size:
                            shutil.copy2(f, target)
        logger.info(
            "backfill %s/%s cover_ok=%s avatar_ok=%s",
            processed,
            stats["rows"],
            stats["cover_ok"],
            stats["avatar_ok"],
        )

    with PoliteClient(min_delay=min_delay, max_delay=max_delay) as client:
        for i, row in enumerate(rows, start=1):
            detail_url = (row.get("detailUrl") or "").strip()
            if not detail_url:
                continue

            if (row.get("coverUrlRelative") or "").strip() and (
                row.get("authorAvatarRelative") or ""
            ).strip():
                stats["skipped_complete"] += 1
                stats["cover_ok"] += 1
                stats["avatar_ok"] += 1
                updates[detail_url] = {
                    "authorAvatar": row.get("authorAvatar") or "",
                    "coverUrl": row.get("coverUrl") or "",
                    "coverUrlRelative": row.get("coverUrlRelative") or "",
                    "authorAvatarRelative": row.get("authorAvatarRelative") or "",
                    "imagePaths": row.get("imagePaths") or "",
                    "author": row.get("author") or "",
                }
                if i % save_every == 0 or i == len(rows):
                    persist(i)
                continue

            slug = _slug_from_detail(detail_url)
            cover_url = (row.get("coverUrl") or "").strip()
            avatar_url = (row.get("authorAvatar") or "").strip()
            patch: dict[str, str] = {
                "author": (row.get("author") or "").strip(),
                "coverUrl": cover_url,
                "authorAvatar": avatar_url,
            }

            if not avatar_url or not (row.get("authorAvatarRelative") or "").strip():
                try:
                    resp = client.get(
                        DETAIL_API,
                        params={"urlString": slug},
                        headers={"Accept": "application/json", "Referer": detail_url},
                    )
                    payload = resp.json()
                    author = payload.get("author") or {}
                    if isinstance(author, dict):
                        candidate = (author.get("downloadUrl") or "").strip()
                        if candidate and not is_ad_image_url(candidate):
                            avatar_url = candidate
                            patch["authorAvatar"] = avatar_url
                            stats["avatar_fetched"] += 1
                        if not patch["author"]:
                            patch["author"] = (author.get("screenName") or "").strip()
                    if not cover_url:
                        cover_obj = payload.get("coverImage") or {}
                        if isinstance(cover_obj, dict):
                            cover_url = (cover_obj.get("downloadUrl") or "").strip()
                            if cover_url:
                                patch["coverUrl"] = cover_url
                except Exception as exc:  # noqa: BLE001
                    logger.warning("detail fetch failed %s: %s", detail_url, exc)

            if cover_url:
                name = f"{_safe_name(slug)}{_ext_from_url(cover_url)}"
                dest = covers_dir / name
                rel = f"images/covers/{name}"
                if _download(client, cover_url, dest):
                    patch["coverUrlRelative"] = rel
                    patch["imagePaths"] = rel
                    stats["cover_ok"] += 1
                else:
                    stats["cover_fail"] += 1
            else:
                stats["cover_fail"] += 1

            if avatar_url:
                author_key = _safe_name(patch.get("author") or "") or _safe_name(slug)
                name = f"{author_key}{_ext_from_url(avatar_url)}"
                dest = avatars_dir / name
                rel = f"images/avatars/{name}"
                if _download(client, avatar_url, dest):
                    patch["authorAvatarRelative"] = rel
                    stats["avatar_ok"] += 1
                else:
                    stats["avatar_fail"] += 1
            else:
                stats["avatar_fail"] += 1

            updates[detail_url] = patch

            if i % save_every == 0 or i == len(rows):
                persist(i)

    return stats


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--csv", type=Path, default=Path("data/instructables/instructables.csv"))
    p.add_argument("--image-dir", type=Path, default=Path("data/instructables/images"))
    p.add_argument("--xlsx", type=Path, default=Path("data/instructables/instructables.xlsx"))
    p.add_argument("--snapshot-dir", type=Path, default=None)
    p.add_argument("--min-delay", type=float, default=0.35)
    p.add_argument("--max-delay", type=float, default=0.8)
    p.add_argument("--save-every", type=int, default=25)
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    stats = backfill(
        csv_path=args.csv,
        image_dir=args.image_dir,
        xlsx_path=None if str(args.xlsx) in {"", "none"} else args.xlsx,
        snapshot_dir=args.snapshot_dir,
        min_delay=args.min_delay,
        max_delay=args.max_delay,
        save_every=args.save_every,
    )
    print(stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
