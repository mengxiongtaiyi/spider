"""Classify Ameede links: detail pages vs download entries vs ads."""

from __future__ import annotations

from urllib.parse import urlparse

AMEEDE_HOSTS = {"ameede.com", "www.ameede.com"}

AD_HOST_FRAGMENTS = (
    "doubleclick",
    "googlesyndication",
    "googleadservices",
    "adservice",
    "amazon-adsystem",
    "adsystem",
    "clickbank",
    "bit.ly",
    "amzn.to",
    "facebook.com/tr",
    "outbrain",
    "taboola",
)

AD_PATH_FRAGMENTS = (
    "/ads/",
    "/ad/",
    "/banner/",
    "/sponsor/",
    "/affiliate/",
    "/goto/",
    "/out/",
    "/redirect/",
)


def classify_url(url: str | None) -> str:
    """Return one of: detail | download | ad | unknown."""
    if not url:
        return "unknown"
    raw = url.strip()
    if not raw or raw.startswith("#") or raw.lower().startswith("javascript:"):
        return "unknown"

    parsed = urlparse(raw)
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").lower()

    if host and host not in AMEEDE_HOSTS:
        if any(frag in host for frag in AD_HOST_FRAGMENTS):
            return "ad"
        # Off-site destination is not an Ameede source record.
        return "ad"

    if path.startswith("/download/") or "/download/" in path:
        return "download"

    if any(frag in path for frag in AD_PATH_FRAGMENTS):
        return "ad"

    # Category / tag / pagination / home are not project detail records.
    if path.startswith("/amee/") or path.startswith("/house/") or path in {"", "/"}:
        return "unknown"

    # Typical project detail slug pages live at site root.
    if host in AMEEDE_HOSTS and path.count("/") >= 1 and path not in {"/", ""}:
        return "detail"

    return "unknown"
