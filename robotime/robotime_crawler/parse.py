"""Normalize Shopify product payloads into one row per product (variants not split)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProductRecord:
    sourceUrl: str
    actualSourceUrl: str
    title: str
    brand: str = ""
    designer: str = ""
    price: str = ""
    currency: str = "USD"
    compareAtPrice: str = ""
    availabilityStatus: str = ""
    coverUrl: str = ""
    coverUrlRelative: str = ""
    imageUrls: str = ""
    imagePaths: str = ""
    handle: str = ""
    productId: str = ""
    productType: str = ""
    sku: str = ""
    variantCount: str = ""
    tags: str = ""
    entryType: str = "product"
    crawlStatus: str = "success"
    skipReason: str = ""
    extra: dict = field(default_factory=dict)


def _pick_variant(variants: list[dict[str, Any]]) -> dict[str, Any]:
    """One commercial snapshot for the product — do not emit per-variant rows."""
    if not variants:
        return {}
    for v in variants:
        if v.get("available") is True:
            return v
    return variants[0]


def _availability_status(variant: dict[str, Any], tags: str, schema_avail: str = "") -> str:
    tags_l = (tags or "").lower()
    title_bits = f"{tags_l} {schema_avail.lower()}"
    if "pre-order" in title_bits or "preorder" in title_bits or "pre_order" in title_bits:
        return "preorder"
    if schema_avail:
        if "InStock" in schema_avail:
            return "inStock"
        if "OutOfStock" in schema_avail or "SoldOut" in schema_avail:
            return "soldOut"
        if "PreOrder" in schema_avail:
            return "preorder"
    if variant.get("available") is True:
        return "inStock"
    if variant.get("available") is False:
        return "soldOut"
    return "unknown"


def _image_urls(product: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for img in product.get("images") or []:
        src = (img or {}).get("src") or ""
        if not src or src in seen:
            continue
        seen.add(src)
        urls.append(src)
    # Fallback featured image object
    if not urls:
        featured = product.get("image") or {}
        src = featured.get("src") if isinstance(featured, dict) else ""
        if src:
            urls.append(src)
    return urls


def record_from_product(
    product: dict[str, Any],
    *,
    collection_source_page_url: str,
    currency: str = "USD",
    enrich: dict[str, Any] | None = None,
) -> ProductRecord:
    handle = (product.get("handle") or "").strip()
    source_url = f"https://www.robotimeonline.com/products/{handle}" if handle else ""
    enrich = enrich or {}
    actual = (enrich.get("finalUrl") or source_url).strip() or source_url

    variants = product.get("variants") or []
    variant = _pick_variant(variants)
    tags = product.get("tags") or ""
    if isinstance(tags, list):
        tags = ", ".join(str(t) for t in tags)

    # Brand from Shopify vendor / JSON-LD. Never copy brand into designer.
    brand = (enrich.get("brand") or product.get("vendor") or "").strip()
    designer = (enrich.get("designer") or "").strip()
    if designer and designer.lower() == brand.lower():
        # Spec: do not treat brand as designer when designer is not shown.
        designer = ""

    price = variant.get("price")
    compare = variant.get("compare_at_price")
    # Prefer explicit currency fields when present.
    cur = (
        variant.get("price_currency")
        or variant.get("compare_at_price_currency")
        or currency
        or "USD"
    )

    images = _image_urls(product)
    title = (product.get("title") or "").strip()
    status = "success" if title and source_url else "partial"

    return ProductRecord(
        sourceUrl=source_url,
        actualSourceUrl=actual,
        title=title,
        brand=brand,
        designer=designer,
        price="" if price is None else str(price),
        currency=str(cur).upper(),
        compareAtPrice="" if compare in (None, "", "null") else str(compare),
        availabilityStatus=_availability_status(variant, tags, enrich.get("availabilitySchema") or ""),
        coverUrl=images[0] if images else "",
        imageUrls="|".join(images),
        handle=handle,
        productId=str(product.get("id") or ""),
        productType=(product.get("product_type") or "").strip(),
        sku=str(variant.get("sku") or ""),
        variantCount=str(len(variants)),
        tags=tags if isinstance(tags, str) else "",
        entryType="product",
        crawlStatus=status,
        skipReason="" if status == "success" else "missing_title_or_url",
        extra={"collectionSourcePageUrl": collection_source_page_url},
    )
