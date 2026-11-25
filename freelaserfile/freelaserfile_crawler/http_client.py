"""HTTP helpers with retries and polite rate limiting."""

from __future__ import annotations

import random
import time
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

# Railway upload CDN 403s non-browser User-Agents; use a normal browser UA.
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
        min_delay: float = 0.4,
        max_delay: float = 1.0,
        timeout: float = 45.0,
    ) -> None:
        self.min_delay = min_delay
        self.max_delay = max_delay
        self._last_request_at = 0.0
        self.client = httpx.Client(
            headers=DEFAULT_HEADERS,
            follow_redirects=True,
            timeout=timeout,
        )

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
        wait=wait_exponential_jitter(initial=1, max=30),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        self._throttle()
        resp = self.client.get(url, **kwargs)
        if resp.status_code in {429, 500, 502, 503, 504}:
            resp.raise_for_status()
        resp.raise_for_status()
        return resp

    def download_file(self, url: str, dest: Path) -> None:
        """Download once with limited retries on transient errors only (not 403/404)."""

        @retry(
            retry=retry_if_exception_type((httpx.TransportError, httpx.TimeoutException)),
            wait=wait_exponential_jitter(initial=1, max=20),
            stop=stop_after_attempt(4),
            reraise=True,
        )
        def _once() -> None:
            self._throttle()
            dest.parent.mkdir(parents=True, exist_ok=True)
            headers = {
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                "Referer": "https://freelaserfile.com/",
            }
            with self.client.stream("GET", url, headers=headers) as resp:
                if resp.status_code in {429, 500, 502, 503, 504}:
                    # Raise as transport-ish retry via HTTPStatusError only for transient.
                    resp.raise_for_status()
                if resp.status_code >= 400:
                    resp.raise_for_status()
                tmp = dest.with_suffix(dest.suffix + ".part")
                with tmp.open("wb") as f:
                    for chunk in resp.iter_bytes():
                        f.write(chunk)
                tmp.replace(dest)

        # Manually retry transient HTTP statuses a few times.
        last_exc: Exception | None = None
        for attempt in range(4):
            try:
                _once()
                return
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                code = exc.response.status_code if exc.response is not None else 0
                if code in {429, 500, 502, 503, 504} and attempt < 3:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_exc = exc
                raise
        if last_exc:
            raise last_exc
