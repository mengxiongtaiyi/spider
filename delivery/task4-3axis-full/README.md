# Task 4 — 3axis LaserCut (full delivery)

Complete Task 4 package: compliance CSV + cover images + zip archives + notes.

## Contents

| Path | Description |
|------|-------------|
| `data.csv` | Compliance schema, **English camelCase** headers only |
| `任务四-规范交付说明.md` | Field mapping / fix notes (Chinese) |
| `images/main/` | Cover images referenced by `coverUrlRelative` |
| `images/main/missing-images-part-*.zip` | Optional archives (parts 02–20); same images also present unzipped when available |

## `data.csv` schema

`seq,sourcePageUrl,detailUrl,title,description,coverUrl,coverUrlRelative,author,authorAvatar,fileNames,fileFormats,productPrice,currency,crawlStatus,skipReason`

- Headers are English camelCase (no Chinese column names).
- `coverUrlRelative` is a path **relative to this folder**, e.g. `images/main/6ro66653ox.jpg`.
- Some rows use `.jpeg` while an on-disk twin may originally have been `.jpg`; this package includes exact-name files (or hardlink aliases) so every `coverUrlRelative` resolves.

## How `coverUrlRelative` maps

1. Resolve from the Task 4 folder root:  
   `Task4Root / coverUrlRelative` → e.g. `.../images/main/<id>.jpg`
2. Prefer the exact filename in `images/main/`.
3. If you only downloaded the zip parts: extract every `missing-images-part-*.zip` into `images/main/` (flat — files at the zip root), then re-check paths.
4. Zip parts are **not** a substitute for the full loose set: thousands of covers exist only as loose files (not inside the zips). Keep both if you can.

## Size note

Loose images are multi‑GB with ~18k files. Zip parts are ~78 MB each (under GitHub’s 100 MB file limit). No Git LFS required for individual files under 90 MB.
