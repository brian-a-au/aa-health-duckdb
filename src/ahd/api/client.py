"""Adobe Analytics 2.0 HTTP client. The only module that talks to Adobe."""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from typing import Any

import requests

from ahd.config import Credentials

log = logging.getLogger(__name__)

BASE = "https://analytics.adobe.io/api"
BACKOFF_BASE = 0.5
BACKOFF_CAP = 10.0
TIMEOUT = 120


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
        import aanalytics2

        def sign_in() -> tuple[str, dict]:
            aanalytics2.configure(
                org_id=creds.org_id,
                client_id=creds.client_id,
                secret=creds.secret,
                scopes=creds.scopes,
            )
            companies = aanalytics2.Login().getCompanyId()
            if not companies:
                raise ApiError(
                    0,
                    "No Adobe Analytics company was found for these credentials. "
                    "The credential is probably not added to an Adobe Analytics product profile.",
                )
            cid = company_id or companies[0]["globalCompanyId"]
            return cid, dict(aanalytics2.Analytics(cid).header)

        cid, header = sign_in()
        return cls(requests.Session(), header, cid, lambda: sign_in()[1])

    def get(self, path: str, params: dict | None = None) -> Any:
        return self._request("GET", path, params=params)

    def post_report(self, body: dict) -> dict:
        return self._request("POST", "/reports", json=body)

    def _request(self, method: str, path: str, params=None, json=None) -> Any:
        url = f"{BASE}/{self.company_id}{path}"
        relogged = False
        attempt = 0
        while True:
            resp = self._session.request(
                method, url, headers=self._header, params=params, json=json, timeout=TIMEOUT
            )
            status = resp.status_code
            if status == 401 and not relogged:
                relogged = True
                self._header = dict(self._relogin())
                continue
            if (status == 429 or status >= 500) and attempt < self.max_retries:
                delay = random.uniform(0, min(BACKOFF_CAP, BACKOFF_BASE * 2**attempt))
                retry_after = resp.headers.get("Retry-After")
                try:
                    delay = max(delay, float(retry_after)) if retry_after else delay
                except ValueError:
                    pass
                attempt += 1
                log.debug("HTTP %s from %s %s, retry %d in %.1fs", status, method, path, attempt, delay)
                self._sleep(delay)
                continue
            return self._finish(resp)

    @staticmethod
    def _finish(resp: Any) -> Any:
        status = resp.status_code
        body = _json_or_none(resp)
        err = _error_fields(body)
        if err and (status in (200, 400) or status >= 400):
            raise ApiError(status, err[1] or "Adobe returned an error", err[0])
        if status >= 400:
            raise ApiError(status, f"Adobe returned HTTP {status}")
        return body
