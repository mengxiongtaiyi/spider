"""CSV / Excel export helpers."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable

from openpyxl import Workbook

# Confirmed V1.5 Ameede fields + contract dual-image paths + light operational cols.
FIELDNAMES = [
    "seq",
    "sourcePageUrl",
    "detailUrl",
    "title",
    "projectCode",
    "coverUrl",
    "coverUrlRelative",
    "downloadUrl",
    "categories",
    "tags",
    "entryType",
    "crawlStatus",
    "skipReason",
]


def write_csv(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in FIELDNAMES})


def append_csv(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        if new_file:
            writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in FIELDNAMES})


def write_xlsx(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "ameede"
    ws.append(FIELDNAMES)
    for row in rows:
        ws.append([row.get(k, "") for k in FIELDNAMES])
    wb.save(path)


def read_csv_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))
