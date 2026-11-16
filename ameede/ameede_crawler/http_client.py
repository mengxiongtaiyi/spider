"""HTTP helpers with retries and polite rate limiting."""

from __future__ import annotations

import logging
import random
import socket
import time
from pathlib import Path
from typing import Any

import httpx
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

logger = logging.getLogger(__name__)

# Hard ceiling for any blocking socket op (httpx timeouts alone have hung on this host).
socket.setdefaulttimeout(25)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; AmeedeResearchCrawler/0.1; "
        "+https://github.com/mengxiongtaiyi/spider)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _timeout(seconds: float) -> httpx.Timeout:
    connect = min(10.0, max(5.0, seconds / 3))
    return httpx.Timeout(timeout=seconds, connect=connect, read=seconds, write=seconds, pool=seconds)


class PoliteClient:
    def __init__(
        self,
        *,
        min_delay: float = 0.8,
        max_delay: float = 1.6,
        timeout: float = 20.0,
    ) -> None:
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.timeout = timeout
        self._last_request_at = 0.0
        self.client = self._new_client()

    def _new_client(self) -> httpx.Client:
        return httpx.Client(
            headers=DEFAULT_HEADERS,
            follow_redirects=True,
            timeout=_timeout(self.timeout),
            limits=httpx.Limits(max_keepalive_connections=0, max_connections=10, keepalive_expiry=0),
            http2=False,
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

    def _reset_client(self) -> None:
        try:
            self.client.close()
        except Exception:  # noqa: BLE001
            pass
        self.client = self._new_client()

    @retry(
        retry=retry_if_exception_type(
            (httpx.TransportError, httpx.TimeoutException, httpx.HTTPStatusError, TimeoutError, socket.timeout)
        ),
        wait=wait_exponential_jitter(initial=1, max=15),
        stop=stop_after_attempt(5),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def get_text(self, url: str) -> tuple[str, str]:
        self._throttle()
        try:
            resp = self.client.get(url)
        except (httpx.TransportError, httpx.TimeoutException, TimeoutError, socket.timeout):
            self._reset_client()
            raise
        if resp.status_code in {429, 500, 502, 503, 504}:
            resp.raise_for_status()
        resp.raise_for_status()
        return str(resp.url), resp.text

    @retry(
        retry=retry_if_exception_type(
            (httpx.TransportError, httpx.TimeoutException, httpx.HTTPStatusError, TimeoutError, socket.timeout)
        ),
        wait=wait_exponential_jitter(initial=1, max=15),
        stop=stop_after_attempt(5),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def download_file(self, url: str, dest: Path) -> None:
        self._throttle()
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.client.stream("GET", url) as resp:
                if resp.status_code in {429, 500, 502, 503, 504}:
                    resp.raise_for_status()
                resp.raise_for_status()
                tmp = dest.with_suffix(dest.suffix + ".part")
                with tmp.open("wb") as f:
                    for chunk in resp.iter_bytes():
                        f.write(chunk)
                tmp.replace(dest)
        except (httpx.TransportError, httpx.TimeoutException, TimeoutError, socket.timeout):
            self._reset_client()
            raise
