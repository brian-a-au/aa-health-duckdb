"""Adobe Analytics 2.0 HTTP client. The only module that talks to Adobe."""

from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import requests

from ahd.api.auth import get_token, header_for, sign_in
from ahd.config import Credentials

log = logging.getLogger(__name__)

BASE = "https://analytics.adobe.io/api"


@dataclass(frozen=True)
class Waits:
    """How many more tries to make after one kind of error, and the longest single wait."""

    retries: int
    cap: float

    def delay(self, retry: int) -> float:
        """The wait before retry number `retry`, counted from 0. The step doubles from 1 second
        up to the cap. The wait is half the step plus a random part up to the other half. The
        random part spreads the workers out, and the fixed half sets the shortest total wait."""
        step = min(self.cap, 2.0**retry)
        return step / 2 + random.uniform(0, step / 2)

    def total(self) -> tuple[float, float]:
        """The shortest and the longest wait in all, before the last try."""
        steps = [min(self.cap, 2.0**n) for n in range(self.retries)]
        return sum(steps) / 2, float(sum(steps))


# A rate limit (HTTP 429) or Adobe's temporary error 403025 means that Adobe is busy. Waits of
# about 1, 2, 4, 8, 16 and 30 seconds, 30.5 to 61 seconds in all, outlast a short rate limit.
BUSY = Waits(retries=6, cap=30.0)
# A server error (HTTP 5xx) or a network error. Waits of about 1, 2 and 4 seconds, 3.5 to 7
# seconds in all, so that an Adobe outage or a missing network shows within seconds.
FAILED = Waits(retries=3, cap=4.0)
# The longest wait that a Retry-After header can ask for. A longer one waits this long.
RETRY_AFTER_CAP = 120.0
# Seconds to wait for a connection, and then for the answer. The slowest report in the live
# timing runs took 34 seconds. A computer with no network fails the connection within seconds.
TIMEOUT = (10, 120)
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
        company_name: str = "",
        companies: int = 1,
    ):
        self._session = session
        self._header = dict(header)
        # Guards _header. The generation counts sign-ins, so that when several threads see an
        # expired token at once, only the first signs in again and the rest reuse its header.
        self._header_lock = threading.Lock()
        self._header_gen = 0
        self.company_id = company_id
        self.company_name = company_name
        self.companies = companies
        self._relogin = relogin
        self._sleep = sleep

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

        return cls(
            session,
            first.header,
            first.company_id,
            relogin,
            company_name=first.company_name,
            companies=first.companies,
        )

    def get(self, path: str, params: dict | None = None) -> Any:
        return self._request("GET", path, params=params)

    def post_report(self, body: dict) -> dict:
        return self._request("POST", "/reports", json=body)

    def _request(self, method: str, path: str, params=None, json=None) -> Any:
        url = f"{BASE}/{self.company_id}{path}"
        relogged = False
        # Retries so far for each kind of error. Each kind has its own limit.
        tries = {BUSY: 0, FAILED: 0}
        while True:
            with self._header_lock:
                header, gen = self._header, self._header_gen
            try:
                resp = self._session.request(
                    method, url, headers=header, params=params, json=json, timeout=TIMEOUT
                )
            except RETRIED_ERRORS as e:
                if tries[FAILED] >= FAILED.retries:
                    raise
                delay = FAILED.delay(tries[FAILED])
                tries[FAILED] += 1
                self._wait(type(e).__name__, method, path, tries[FAILED], delay)
                continue
            status = resp.status_code
            if status == 401 and not relogged:
                relogged = True
                self._renew(gen)
                continue
            kind = self._temporary(resp)
            if kind is not None and tries[kind] < kind.retries:
                delay = kind.delay(tries[kind])
                retry_after = resp.headers.get("Retry-After")
                try:
                    if retry_after:
                        delay = min(RETRY_AFTER_CAP, max(delay, float(retry_after)))
                except ValueError:
                    pass
                tries[kind] += 1
                self._wait(f"HTTP {status}", method, path, tries[kind], delay)
                continue
            return self._finish(resp)

    def _wait(self, what: str, method: str, path: str, retry: int, delay: float) -> None:
        log.debug("%s from %s %s, retry %d in %.1fs", what, method, path, retry, delay)
        self._sleep(delay)

    def _renew(self, seen_gen: int) -> None:
        """Sign in again, unless another thread already did since `seen_gen`."""
        with self._header_lock:
            if self._header_gen == seen_gen:
                self._header = dict(self._relogin())
                self._header_gen += 1

    @staticmethod
    def _temporary(resp: Any) -> Waits | None:
        """The kind of temporary error in this answer, or None when another try will not help."""
        status = resp.status_code
        if status == 429:
            return BUSY
        if status >= 500:
            return FAILED
        if status != 403:
            return None
        err = _error_fields(_json_or_none(resp))
        return BUSY if err is not None and err[0] == TEMPORARY_403 else None

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
