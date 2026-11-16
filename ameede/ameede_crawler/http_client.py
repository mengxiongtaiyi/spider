"""HTTP helpers via curl subprocess (hard timeouts) + polite rate limiting."""

from __future__ import annotations

import logging
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

    def _curl(self, url: str, dest: Path | None = None) -> tuple[int, str]:
        """Run curl with hard max-time. Returns (status_code, body_or_empty)."""
        cmd = [
            "curl",
            "-sS",
            "-L",
            "--compressed",
            "--max-time",
            str(int(self.timeout)),
            "--connect-timeout",
            "10",
            "-A",
            DEFAULT_UA,
            "-H",
            "Accept-Language: en-US,en;q=0.9",
            "-w",
            "\n__HTTP_STATUS__:%{http_code}",
        ]
        if dest is not None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(dest.suffix + ".part")
            cmd.extend(["-o", str(tmp)])
        else:
            cmd.extend(["-o", "-"])
        cmd.append(url)

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True if dest is None else False,
                timeout=self.timeout + 15,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise FetchError(f"curl timeout for {url}") from exc

        if dest is None:
            out = proc.stdout or ""
            err = proc.stderr or ""
            if "__HTTP_STATUS__:" in out:
                body, _, status_part = out.rpartition("__HTTP_STATUS__:")
                status = int(status_part.strip().splitlines()[0] or "0")
            else:
                body, status = out, 0
            if proc.returncode not in (0,) or status == 0:
                raise FetchError(f"curl rc={proc.returncode} status={status} err={err[:200]} url={url}")
            if status in {429, 500, 502, 503, 504} or status >= 400:
                raise FetchError(f"HTTP {status} for {url}")
            return status, body

        # Binary download path: status trailer is in stderr when -o file? With -w to stdout
        # but stdout captured as bytes when text=False. Re-run status via -I is wasteful;
        # use -w to stderr instead.
        # Rebuild with -o tmp and -w on stdout as text meta only via separate approach:
        return self._curl_download(url, dest)

    def _curl_download(self, url: str, dest: Path) -> tuple[int, str]:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        with tempfile.NamedTemporaryFile(prefix="ameede_curl_", suffix=".hdr", delete=False) as hdr:
            hdr_path = Path(hdr.name)
        try:
            cmd = [
                "curl",
                "-sS",
                "-L",
                "--compressed",
                "--max-time",
                str(int(self.timeout)),
                "--connect-timeout",
                "10",
                "-A",
                DEFAULT_UA,
                "-o",
                str(tmp),
                "-w",
                "%{http_code}",
                "-D",
                str(hdr_path),
                url,
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout + 15, check=False)
            status_txt = (proc.stdout or "").strip()
            try:
                status = int(status_txt)
            except ValueError:
                status = 0
            if proc.returncode != 0 or status == 0:
                raise FetchError(
                    f"curl download rc={proc.returncode} status={status_txt} err={(proc.stderr or '')[:200]} url={url}"
                )
            if status in {429, 500, 502, 503, 504} or status >= 400:
                raise FetchError(f"HTTP {status} downloading {url}")
            tmp.replace(dest)
            return status, ""
        finally:
            hdr_path.unlink(missing_ok=True)
            if tmp.exists() and (not dest.exists() or dest.stat().st_size == 0):
                tmp.unlink(missing_ok=True)

    @retry(
        retry=retry_if_exception_type(FetchError),
        wait=wait_exponential_jitter(initial=1, max=15),
        stop=stop_after_attempt(5),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def get_text(self, url: str) -> tuple[str, str]:
        self._throttle()
        # Use curl writing body to temp file + status on stdout for reliability
        with tempfile.NamedTemporaryFile(prefix="ameede_body_", suffix=".html", delete=False) as fh:
            body_path = Path(fh.name)
        try:
            cmd = [
                "curl",
                "-sS",
                "-L",
                "--compressed",
                "--max-time",
                str(int(self.timeout)),
                "--connect-timeout",
                "10",
                "-A",
                DEFAULT_UA,
                "-H",
                "Accept-Language: en-US,en;q=0.9",
                "-o",
                str(body_path),
                "-w",
                "%{http_code} %{url_effective}",
                url,
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout + 15, check=False)
            meta = (proc.stdout or "").strip().split(" ", 1)
            status = int(meta[0]) if meta and meta[0].isdigit() else 0
            final_url = meta[1] if len(meta) > 1 else url
            if proc.returncode != 0 or status == 0:
                raise FetchError(
                    f"curl rc={proc.returncode} status={status} err={(proc.stderr or '')[:200]} url={url}"
                )
            if status in {429, 500, 502, 503, 504} or status >= 400:
                raise FetchError(f"HTTP {status} for {url}")
            text = body_path.read_text(encoding="utf-8", errors="ignore")
            logger.info("HTTP Request: GET %s \"%s\"", final_url, f"HTTP {status}")
            return final_url, text
        except subprocess.TimeoutExpired as exc:
            raise FetchError(f"curl timeout for {url}") from exc
        finally:
            body_path.unlink(missing_ok=True)

    @retry(
        retry=retry_if_exception_type(FetchError),
        wait=wait_exponential_jitter(initial=1, max=15),
        stop=stop_after_attempt(5),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def download_file(self, url: str, dest: Path) -> None:
        self._throttle()
        try:
            self._curl_download(url, dest)
            logger.info("HTTP Request: GET %s \"HTTP download OK\"", url)
        except subprocess.TimeoutExpired as exc:
            raise FetchError(f"curl timeout downloading {url}") from exc
