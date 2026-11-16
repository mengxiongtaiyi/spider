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
        "Mozilla/5.0 (compatible; AmeedeResearchCrawler/0.1; "
        "+https://github.com/mengxiongtaiyi/spider)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class PoliteClient:
    def __init__(
        self,
        *,
        min_delay: float = 0.8,
        max_delay: float = 1.6,
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
    def get_text(self, url: str) -> tuple[str, str]:
        self._throttle()
        resp = self.client.get(url)
        if resp.status_code in {429, 500, 502, 503, 504}:
            resp.raise_for_status()
        resp.raise_for_status()
        return str(resp.url), resp.text

    @retry(
        retry=retry_if_exception_type((httpx.TransportError, httpx.TimeoutException, httpx.HTTPStatusError)),
        wait=wait_exponential_jitter(initial=1, max=30),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def download_file(self, url: str, dest: Path) -> None:
        self._throttle()
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self.client.stream("GET", url) as resp:
            if resp.status_code in {429, 500, 502, 503, 504}:
                resp.raise_for_status()
            resp.raise_for_status()
            tmp = dest.with_suffix(dest.suffix + ".part")
            with tmp.open("wb") as f:
                for chunk in resp.iter_bytes():
                    f.write(chunk)
            tmp.replace(dest)
