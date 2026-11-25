"""HTTP helpers with retries and polite rate limiting."""

from __future__ import annotations

import random
import time
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "en-US,en;q=0.9",
}


class PoliteClient:
    def __init__(
        self,
        *,
        min_delay: float = 0.35,
        max_delay: float = 0.9,
        timeout: float = 45.0,
    ) -> None:
        self.min_delay = min_delay
        self.max_delay = max_delay
        self._last_request_at = 0.0
        self._consecutive_429 = 0
        self.client = httpx.Client(
            headers=DEFAULT_HEADERS,
            follow_redirects=True,
            timeout=timeout,
        )

    def warm_session(self, url: str = "https://www.robotimeonline.com/") -> None:
        """Hit the storefront once so Cloudflare/Shopify cookies settle before JSON APIs."""
        try:
            self._throttle()
            resp = self.client.get(
                url,
                headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
            )
            # Do not raise on warm failures; listing may still succeed.
            if resp.status_code >= 500:
                time.sleep(2.0)
        except Exception:
            pass

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "PoliteClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _throttle(self) -> None:
        now = time.monotonic()
        wait_for = self._last_request_at + random.uniform(self.min_delay, self.max_delay) - now
        if wait_for > 0:
            time.sleep(wait_for)
        self._last_request_at = time.monotonic()

    @retry(
        retry=retry_if_exception_type((httpx.TransportError, httpx.TimeoutException, httpx.HTTPStatusError)),
        wait=wait_exponential_jitter(initial=5, max=180),
        stop=stop_after_attempt(12),
        reraise=True,
    )
    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        self._throttle()
        resp = self.client.get(url, **kwargs)
        if resp.status_code in {429, 500, 502, 503, 504}:
            # Honor Retry-After when present (Shopify/Cloudflare 429).
            if resp.status_code == 429:
                self._consecutive_429 += 1
                ra = resp.headers.get("retry-after")
                try:
                    base = float(ra) if ra else 60.0
                except ValueError:
                    base = 60.0
                # Escalate beyond Retry-After on repeated 429s from the same client.
                sleep_s = base * (1.0 + 0.5 * (self._consecutive_429 - 1)) + random.uniform(2.0, 8.0)
                sleep_s = min(max(sleep_s, 15.0), 300.0)
                time.sleep(sleep_s)
            resp.raise_for_status()
        self._consecutive_429 = 0
        resp.raise_for_status()
        return resp

    def resolve_final_url(self, url: str) -> str:
        """Follow redirects and return the final URL (actual source site)."""
        self._throttle()
        resp = self.client.get(url, headers={"Accept": "text/html,application/xhtml+xml"})
        return str(resp.url)

    def download_file(self, url: str, dest: Path) -> None:
        self._throttle()
        dest.parent.mkdir(parents=True, exist_ok=True)
        headers = {
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Referer": "https://www.robotimeonline.com/",
        }
        last_exc: Exception | None = None
        for attempt in range(4):
            try:
                with self.client.stream("GET", url, headers=headers) as resp:
                    if resp.status_code in {429, 500, 502, 503, 504} and attempt < 3:
                        time.sleep(1.2 * (attempt + 1))
                        continue
                    resp.raise_for_status()
                    tmp = dest.with_suffix(dest.suffix + ".part")
                    with tmp.open("wb") as f:
                        for chunk in resp.iter_bytes():
                            f.write(chunk)
                    tmp.replace(dest)
                    return
            except (httpx.TransportError, httpx.TimeoutException, httpx.HTTPStatusError) as exc:
                last_exc = exc
                if isinstance(exc, httpx.HTTPStatusError):
                    code = exc.response.status_code if exc.response is not None else 0
                    if code in {429, 500, 502, 503, 504} and attempt < 3:
                        time.sleep(1.2 * (attempt + 1))
                        continue
                    raise
                raise
        if last_exc:
            raise last_exc
