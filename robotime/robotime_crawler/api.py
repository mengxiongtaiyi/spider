"""Shopify JSON helpers for Robotime Online."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from bs4 import BeautifulSoup

from .http_client import PoliteClient

SITE = "https://www.robotimeonline.com"
COLLECTION_PRODUCTS = f"{SITE}/collections/all/products.json"
META_JSON = f"{SITE}/meta.json"


@dataclass
class CollectionPage:
    products: list[dict[str, Any]]
    page: int
    source_page_url: str


class RobotimeApi:
    def __init__(self, client: PoliteClient) -> None:
        self.client = client
        self.currency = "USD"

    def refresh_shop_currency(self) -> str:
        try:
            data = self.client.get(META_JSON).json()
            self.currency = (data.get("currency") or self.currency).upper()
        except Exception:
            pass
        return self.currency

    def list_products(self, *, page: int = 1, limit: int = 250) -> CollectionPage:
        resp = self.client.get(
            COLLECTION_PRODUCTS,
            params={"limit": min(limit, 250), "page": page},
        )
        products = resp.json().get("products") or []
        source = f"{SITE}/collections/all"
        if page > 1:
            # Shopify collection pagination commonly uses ?page=
            source = f"{SITE}/collections/all?page={page}"
        return CollectionPage(products=products, page=page, source_page_url=source)

    def product_url(self, handle: str) -> str:
        return f"{SITE}/products/{handle}"

    def enrich_from_html(self, handle: str) -> dict[str, Any]:
        """Optional HTML enrich: JSON-LD brand/availability, no invented designer."""
        url = self.product_url(handle)
        resp = self.client.get(url, headers={"Accept": "text/html"})
        html = resp.text
        out: dict[str, Any] = {
            "finalUrl": str(resp.url),
            "brand": "",
            "designer": "",
            "availabilitySchema": "",
        }
        soup = BeautifulSoup(html, "lxml")
        for script in soup.select('script[type="application/ld+json"]'):
            raw = script.string or ""
            try:
                data = json.loads(raw)
            except Exception:
                continue
            nodes = data if isinstance(data, list) else [data]
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                if node.get("@type") == "Product":
                    brand = node.get("brand")
                    if isinstance(brand, dict):
                        out["brand"] = (brand.get("name") or "").strip()
                    elif isinstance(brand, str):
                        out["brand"] = brand.strip()
                    offers = node.get("offers")
                    if isinstance(offers, dict):
                        avail = offers.get("availability") or ""
                        out["availabilitySchema"] = str(avail)
                    elif isinstance(offers, list) and offers:
                        avail = (offers[0] or {}).get("availability") or ""
                        out["availabilitySchema"] = str(avail)
                    # Only set designer if an explicit designer property exists.
                    for prop in node.get("additionalProperty") or []:
                        if not isinstance(prop, dict):
                            continue
                        name = (prop.get("name") or "").strip().lower()
                        if name == "designer":
                            out["designer"] = str(prop.get("value") or "").strip()
        # Explicit "Designer:" label in visible text (rare).
        m = re.search(r"Designer\s*[:：]\s*([^\n<]{2,80})", html, re.I)
        if m and not out["designer"]:
            out["designer"] = m.group(1).strip()
        return out
