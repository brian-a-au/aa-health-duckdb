"""OAuth Server-to-Server sign-in. Turns each way it can fail into a plain fix.

Two requests: the IMS token request, then the Analytics discovery call that names the
login company. Adobe accepts unknown scopes and ignores the org ID header in discovery, so
those two problems are found by reading the discovery answer. No value of the credential,
token or header ever goes into a message.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import requests

from ahd.config import Credentials

IMS_TOKEN_URL = "https://ims-na1.adobelogin.com/ims/token/v3"
DISCOVERY_URL = "https://analytics.adobe.io/discovery/me"
# Seconds to wait for a connection, and then for the answer.
TIMEOUT = (10, 60)
# Tries of the token request when Adobe's sign-in service is busy (HTTP 429 or 5xx) or the
# network fails, and the waits in seconds between them. A wrong credential is never tried again.
SIGN_IN_TRIES = 3
SIGN_IN_WAITS = (1.0, 3.0)
TEMPORARY_ERRORS = (requests.ConnectionError, requests.Timeout)
_sleep = time.sleep

# Without these, the token works but discovery returns no Analytics company.
REQUIRED_SCOPES = ("openid", "AdobeID", "additional_info.projectedProductContext")

DEV_CONSOLE = (
    "In the Adobe Developer Console, open the project and its OAuth Server-to-Server credential."
)

WRONG_CLIENT_ID = (
    "Sign-in failed: Adobe does not know this client ID. " + DEV_CONSOLE + " Copy its client ID "
    "into the client_id value. Check that it has no extra spaces."
)
WRONG_SECRET = (
    "Sign-in failed: the client secret is wrong. " + DEV_CONSOLE + " Copy the client secret "
    'into the secret value. If you rotated the secret, use the new one. Click "Retrieve client '
    'secret" to see it.'
)
SCOPE_PROBLEM = (
    "Sign-in worked, but the token cannot see Adobe Analytics because the scopes are wrong. "
    + DEV_CONSOLE
    + " Copy the full scopes list into the scopes value. It must include {missing}."
)
SCOPE_REJECTED = (
    "Sign-in failed: Adobe rejected the scopes. " + DEV_CONSOLE + " Copy the full scopes list "
    "into the scopes value, exactly as it is shown there."
)
WRONG_ORG = (
    "Sign-in worked, but the org ID does not belong to this credential. "
    + DEV_CONSOLE
    + " Copy its organization ID into the org_id value. It ends with @AdobeOrg."
)
NO_COMPANY = (
    "Sign-in worked, but this credential sees no Adobe Analytics company. In the Adobe Admin "
    "Console, open Adobe Analytics, choose a product profile, and add the credential's technical "
    "account to it under API credentials. Changes can take a while to reach Analytics."
)
NO_REPORT_SUITES = (
    "This credential signs in but sees no report suites. In the Adobe Admin Console, open the "
    "Adobe Analytics product profile that holds the credential's technical account, and add the "
    "report suites you want to check. Changes can take a while to reach Analytics."
)


class SignInError(Exception):
    """Sign-in failed. The message says what to fix and never holds a credential value."""


class SignInUnavailable(SignInError):
    """Adobe's sign-in service kept answering with a temporary error. The credential may be fine."""


@dataclass(frozen=True)
class SignIn:
    company_id: str
    header: dict
    company_name: str = ""
    # How many login companies the credential can see. With more than one, the caller can
    # say which one was used.
    companies: int = 1


def _json(resp: Any) -> Any:
    try:
        return resp.json()
    except ValueError:
        return None


def _missing_scopes(scopes: str) -> list[str]:
    have = {s.strip() for s in scopes.replace(" ", ",").split(",") if s.strip()}
    return [s for s in REQUIRED_SCOPES if s not in have]


def _is_temporary(status: int) -> bool:
    return status == 429 or status >= 500


def _post_token(session: Any, creds: Credentials) -> Any:
    """The token answer. Tries again while the answer or the network error is temporary."""
    attempt = 1
    while True:
        try:
            resp = session.post(
                IMS_TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": creds.client_id,
                    "client_secret": creds.secret,
                    "scope": creds.scopes,
                },
                timeout=TIMEOUT,
            )
        except TEMPORARY_ERRORS:
            if attempt >= SIGN_IN_TRIES:
                raise
        else:
            if attempt >= SIGN_IN_TRIES or not _is_temporary(resp.status_code):
                return resp
        _sleep(SIGN_IN_WAITS[min(attempt, len(SIGN_IN_WAITS)) - 1])
        attempt += 1


def get_token(session: Any, creds: Credentials) -> str:
    resp = _post_token(session, creds)
    body = _json(resp)
    body = body if isinstance(body, dict) else {}
    token = body.get("access_token")
    if resp.status_code == 200 and isinstance(token, str) and token:
        return token
    error = str(body.get("error") or "")
    desc = str(body.get("error_description") or "")
    if error == "invalid_client" and "secret" in desc:
        raise SignInError(WRONG_SECRET)
    if error == "invalid_client":
        raise SignInError(WRONG_CLIENT_ID)
    if error == "invalid_scope":
        raise SignInError(SCOPE_REJECTED)
    code = f", error {error}" if error else ""
    if _is_temporary(resp.status_code):
        raise SignInUnavailable(
            f"Sign-in failed: Adobe's sign-in service answered HTTP {resp.status_code}{code} "
            f"{SIGN_IN_TRIES} times. This is a problem at Adobe, not with the credential. Wait a "
            "little and try again."
        )
    raise SignInError(
        f"Sign-in failed: Adobe answered HTTP {resp.status_code}{code}. Check the credential in "
        "the Adobe Developer Console, then try again."
    )


def header_for(token: str, creds: Credentials, company_id: str) -> dict:
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "x-api-key": creds.client_id,
        "x-proxy-global-company-id": company_id,
    }


def find_company(
    session: Any, token: str, creds: Credentials, company_id: str | None
) -> tuple[str, str, int]:
    """The chosen login company as (global company ID, name, how many the credential sees)."""
    resp = session.get(
        DISCOVERY_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "x-api-key": creds.client_id,
            "x-gw-ims-org-id": creds.org_id,
        },
        timeout=TIMEOUT,
    )
    body = _json(resp)
    if resp.status_code != 200 or not isinstance(body, dict):
        raise SignInError(
            f"Sign-in worked, but the Analytics discovery call answered HTTP {resp.status_code}. "
            "Wait a little and try again."
        )
    orgs = [o for o in body.get("imsOrgs") or [] if isinstance(o, dict)]
    if not orgs:
        missing = _missing_scopes(creds.scopes)
        if missing:
            raise SignInError(SCOPE_PROBLEM.format(missing=", ".join(missing)))
        raise SignInError(NO_COMPANY)
    org = next(
        (o for o in orgs if str(o.get("imsOrgId", "")).lower() == creds.org_id.lower()), None
    )
    if org is None:
        raise SignInError(WRONG_ORG)
    companies = [
        (str(c["globalCompanyId"]), str(c.get("companyName") or ""))
        for c in org.get("companies") or []
        if isinstance(c, dict) and c.get("globalCompanyId")
    ]
    if not companies:
        raise SignInError(NO_COMPANY)
    if company_id is None:
        return companies[0][0], companies[0][1], len(companies)
    # The login company may be given by its global company ID or by its name.
    wanted = company_id.strip().lower()
    for cid, name in companies:
        if wanted in (cid.lower(), name.lower()):
            return cid, name, len(companies)
    names = ", ".join(f"{name or '(no name)'} ({cid})" for cid, name in companies)
    raise SignInError(
        f"This credential cannot see the login company {company_id!r}. It can see: {names}."
    )


def sign_in(session: Any, creds: Credentials, company_id: str | None = None) -> SignIn:
    token = get_token(session, creds)
    cid, name, count = find_company(session, token, creds, company_id)
    return SignIn(cid, header_for(token, creds, cid), name, count)
