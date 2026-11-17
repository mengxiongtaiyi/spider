"""HTTP helpers via `timeout`-wrapped curl (hard kills) + polite rate limiting."""

from __future__ import annotations

import logging
import os
import random
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

logger = logging.getLogger(__name__)

DEFAULT_UA = (
    "Mozilla/5.0 (compatible; AmeedeResearchCrawler/0.1; "
    "+https://github.com/mengxiongtaiyi/spider)"
)


class FetchError(Exception):
    pass


def _run(cmd: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
    """Run command in its own process group; always kill the group on expiry."""
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            start_new_session=True,
        )
    except subprocess.TimeoutExpired as exc:
        if exc.process is not None:  # type: ignore[attr-defined]
            try:
                os.killpg(exc.process.pid, 9)  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
        raise FetchError(f"command timeout after {timeout}s: {' '.join(cmd[:6])}...") from exc


class PoliteClient:
    def __init__(
        self,
        *,
        min_delay: float = 0.8,
        max_delay: float = 1.6,
        timeout: float = 25.0,
    ) -> None:
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.timeout = timeout
        self._last_request_at = 0.0

    def close(self) -> None:
        return None

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

    def _curl_cmd(self, *extra: str) -> list[str]:
        # GNU timeout sends KILL if curl ignores soft signals.
        return [
            "timeout",
            "-s",
            "KILL",
            str(int(self.timeout)),
            "curl",
            "-sS",
            "-L",
            "--compressed",
            "--max-time",
            str(int(self.timeout) - 1),
            "--connect-timeout",
            "8",
            "--retry",
            "0",
            "-A",
            DEFAULT_UA,
            *extra,
        ]

    @retry(
        retry=retry_if_exception_type(FetchError),
        wait=wait_exponential_jitter(initial=1, max=12),
        stop=stop_after_attempt(4),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def get_text(self, url: str) -> tuple[str, str]:
        self._throttle()
        with tempfile.NamedTemporaryFile(prefix="ameede_body_", suffix=".html", delete=False) as fh:
            body_path = Path(fh.name)
        try:
            cmd = self._curl_cmd(
                "-H",
                "Accept-Language: en-US,en;q=0.9",
                "-o",
                str(body_path),
                "-w",
                "%{http_code} %{url_effective}",
                url,
            )
            proc = _run(cmd, timeout=self.timeout + 10)
            meta = (proc.stdout or "").strip().split(" ", 1)
            status = int(meta[0]) if meta and meta[0].isdigit() else 0
            final_url = meta[1] if len(meta) > 1 else url
            # timeout(1) exit 124/137 means killed by timeout
            if proc.returncode in {124, 137}:
                raise FetchError(f"curl killed by timeout for {url}")
            if proc.returncode != 0 or status == 0:
                raise FetchError(
                    f"curl rc={proc.returncode} status={status} err={(proc.stderr or '')[:200]} url={url}"
                )
            if status >= 400:
                raise FetchError(f"HTTP {status} for {url}")
            text = body_path.read_text(encoding="utf-8", errors="ignore")
            logger.info("HTTP Request: GET %s \"HTTP %s\"", final_url, status)
            return final_url, text
        finally:
            body_path.unlink(missing_ok=True)

    @retry(
        retry=retry_if_exception_type(FetchError),
        wait=wait_exponential_jitter(initial=1, max=12),
        stop=stop_after_attempt(4),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def download_file(self, url: str, dest: Path) -> None:
        self._throttle()
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        cmd = self._curl_cmd("-o", str(tmp), "-w", "%{http_code}", url)
        proc = _run(cmd, timeout=self.timeout + 10)
        status_txt = (proc.stdout or "").strip()
        status = int(status_txt) if status_txt.isdigit() else 0
        if proc.returncode in {124, 137}:
            tmp.unlink(missing_ok=True)
            raise FetchError(f"curl killed by timeout downloading {url}")
        if proc.returncode != 0 or status == 0:
            tmp.unlink(missing_ok=True)
            raise FetchError(
                f"curl download rc={proc.returncode} status={status_txt} err={(proc.stderr or '')[:200]} url={url}"
            )
        if status >= 400:
            tmp.unlink(missing_ok=True)
            raise FetchError(f"HTTP {status} downloading {url}")
        tmp.replace(dest)
        logger.info("HTTP Request: GET %s \"HTTP download %s\"", url, status)
