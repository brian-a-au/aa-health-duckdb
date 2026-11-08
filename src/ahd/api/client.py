"""Adobe Analytics 2.0 HTTP client. The only module that talks to Adobe."""

from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Callable
from typing import Any

import requests

from ahd.api.auth import get_token, header_for, sign_in
from ahd.config import Credentials

log = logging.getLogger(__name__)

BASE = "https://analytics.adobe.io/api"
BACKOFF_BASE = 0.5
BACKOFF_CAP = 10.0
TIMEOUT = 120
# Adobe's "temporarily unavailable" code. It comes with HTTP 403 but clears on a retry.
TEMPORARY_403 = "403025"
NOT_JSON = "Adobe sent an answer that could not be read as JSON."
# Network errors that are worth another try. ChunkedEncodingError means the connection
# dropped while the answer was being read.
RETRIED_ERRORS = (
    requests.ConnectionError,
    requests.Timeout,
    requests.exceptions.ChunkedEncodingError,
)


class ApiError(Exception):
    """An Adobe API failure. Never carries header values."""

    def __init__(self, status: int, message: str, error_code: str | None = None):
        super().__init__(message)
        self.status = status
        self.error_code = error_code
        self.message = message

    def __str__(self) -> str:
        code = f" [{self.error_code}]" if self.error_code else ""
        return f"Adobe API error {self.status}{code}: {self.message}"


def _json_or_none(resp: Any) -> Any:
    try:
        return resp.json()
    except ValueError:
        return None


def _error_fields(body: Any) -> tuple[str, str] | None:
    if not isinstance(body, dict):
        return None
    code = body.get("errorCode") or body.get("error_code")
    if not code:
        return None
    msg = body.get("errorDescription") or body.get("message") or ""
    return str(code), str(msg)


class AdobeClient:
    def __init__(
        self,
        session: Any,
        header: dict,
        company_id: str,
        relogin: Callable[[], dict],
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = 4,
    ):
        self._session = session
        self._header = dict(header)
        # Guards _header. The generation counts sign-ins, so that when several threads see an
        # expired token at once, only the first signs in again and the rest reuse its header.
        self._header_lock = threading.Lock()
        self._header_gen = 0
        self.company_id = company_id
        self._relogin = relogin
        self._sleep = sleep
        self.max_retries = max_retries

    def __repr__(self) -> str:
        return f"AdobeClient(company_id={self.company_id!r})"

    @classmethod
    def _from_parts(cls, session, header, company_id, relogin, sleep=time.sleep) -> AdobeClient:
        return cls(session, header, company_id, relogin, sleep=sleep)

    @classmethod
    def connect(cls, creds: Credentials, company_id: str | None = None) -> AdobeClient:
        session = requests.Session()
        first = sign_in(session, creds, company_id)

        def relogin() -> dict:
            return header_for(get_token(session, creds), creds, first.company_id)

        return cls(session, first.header, first.company_id, relogin)

    def get(self, path: str, params: dict | None = None) -> Any:
        return self._request("GET", path, params=params)

    def post_report(self, body: dict) -> dict:
        return self._request("POST", "/reports", json=body)

    def _request(self, method: str, path: str, params=None, json=None) -> Any:
        url = f"{BASE}/{self.company_id}{path}"
        relogged = False
        attempt = 0
        while True:
            with self._header_lock:
                header, gen = self._header, self._header_gen
            try:
                resp = self._session.request(
                    method, url, headers=header, params=params, json=json, timeout=TIMEOUT
                )
            except RETRIED_ERRORS as e:
                if attempt >= self.max_retries:
                    raise
                attempt += 1
                delay = self._backoff(attempt - 1)
                log.debug(
                    "%s from %s %s, retry %d in %.1fs",
                    type(e).__name__,
                    method,
                    path,
                    attempt,
                    delay,
                )
                self._sleep(delay)
                continue
            status = resp.status_code
            if status == 401 and not relogged:
                relogged = True
                self._renew(gen)
                continue
            if self._temporary(resp) and attempt < self.max_retries:
                delay = self._backoff(attempt)
                retry_after = resp.headers.get("Retry-After")
                try:
                    delay = max(delay, float(retry_after)) if retry_after else delay
                except ValueError:
                    pass
                attempt += 1
                log.debug(
                    "HTTP %s from %s %s, retry %d in %.1fs", status, method, path, attempt, delay
                )
                self._sleep(delay)
                continue
            return self._finish(resp)

    def _renew(self, seen_gen: int) -> None:
        """Sign in again, unless another thread already did since `seen_gen`."""
        with self._header_lock:
            if self._header_gen == seen_gen:
                self._header = dict(self._relogin())
                self._header_gen += 1

    @staticmethod
    def _temporary(resp: Any) -> bool:
        status = resp.status_code
        if status == 429 or status >= 500:
            return True
        if status != 403:
            return False
        err = _error_fields(_json_or_none(resp))
        return err is not None and err[0] == TEMPORARY_403

    @staticmethod
    def _backoff(attempt: int) -> float:
        return random.uniform(0, min(BACKOFF_CAP, BACKOFF_BASE * 2**attempt))

    @staticmethod
    def _finish(resp: Any) -> Any:
        status = resp.status_code
        body = _json_or_none(resp)
        err = _error_fields(body)
        if err and (status in (200, 400) or status >= 400):
            raise ApiError(status, err[1] or "Adobe returned an error", err[0])
        if status >= 400:
            raise ApiError(status, f"Adobe returned HTTP {status}")
        if not isinstance(body, (dict, list)):
            # e.g. an HTML page from a proxy, or a body cut off in the middle.
            raise ApiError(status, NOT_JSON, "invalid_response")
        return body
