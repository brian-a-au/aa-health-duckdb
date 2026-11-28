"""OAuth Server-to-Server sign-in. Turns each way it can fail into a plain fix.

Two requests: the IMS token request, then the Analytics discovery call that names the
login company. Adobe accepts unknown scopes and ignores the org ID header in discovery, so
those two problems are found by reading the discovery answer. No value of the credential,
token or header ever goes into a message.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import requests

from ahd.config import MASK, Credentials, mask_client_id
from ahd.text import inert

IMS_TOKEN_URL = "https://ims-na1.adobelogin.com/ims/token/v3"
DISCOVERY_URL = "https://analytics.adobe.io/discovery/me"
# Seconds to wait for a connection, and then for the answer.
TIMEOUT = (10, 60)
# Tries of the token request and of the discovery call when Adobe is busy (HTTP 429 or 5xx) or
# the network fails, and the waits in seconds between them. A wrong credential is never tried
# again.
SIGN_IN_TRIES = 3
SIGN_IN_WAITS = (1.0, 3.0)
# Network errors that are worth another try. ChunkedEncodingError means the connection
# dropped while the answer was being read. The client tries the same errors again.
TEMPORARY_ERRORS = (
    requests.ConnectionError,
    requests.Timeout,
    requests.exceptions.ChunkedEncodingError,
)
# The longest wait that a Retry-After header can ask for. A longer one waits this long. The
# client uses the same limit for its own requests.
RETRY_AFTER_CAP = 120.0
_sleep = time.sleep
# Waits a number of seconds. A true result means that the wait ended early and that no other
# try should follow, as threading.Event.wait gives after the event is set.
Wait = Callable[[float], object]

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
    "Console, add the credential's technical account to an Adobe Analytics product profile, "
    "under API credentials. Changes can take a while to reach Analytics."
)
NOT_FROM_ADOBE = (
    "Sign-in failed: the answer to the sign-in request did not come from Adobe's sign-in "
    "service. It may come from a proxy or from the sign-in page of a network, e.g., the Wi-Fi of "
    "a hotel. Sign in to the network or check the proxy settings, then try again."
)
NO_REPORT_SUITES = (
    "This credential signs in but sees no report suites. In the Adobe Admin Console, open the "
    "Adobe Analytics product profile that holds the credential's technical account, and add the "
    "report suites you want to check. Changes can take a while to reach Analytics."
)


# The two steps of a sign-in, as the live test of the Adobe credentials standard names them:
# the token request, and the one call to Analytics that finds the login company.
SIGN_IN = "sign_in"
PRODUCT_ACCESS = "product_access"
# The note on a network error of the second step. Without it, the error is of the first step.
AFTER_TOKEN = "The token request worked. The Analytics discovery call failed."
# The longest text from Adobe that an error keeps.
ADOBE_TEXT_LIMIT = 200


class SignInError(Exception):
    """Sign-in failed. The message says what to fix and never holds a credential value.

    code is the name of the failure in the Adobe credentials standard, e.g., bad_secret, and
    step is the step that failed. adobe_text is the error description of the answer for a
    failure that the tool cannot name. It is cut short and holds no secret. Only the JSON of
    ahd check shows it.
    """

    def __init__(
        self,
        message: str,
        code: str = "sign_in_failed",
        step: str = SIGN_IN,
        adobe_text: str = "",
    ):
        super().__init__(message)
        self.code = code
        self.step = step
        self.adobe_text = adobe_text


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


def _list(value: Any) -> list:
    """The value when it is a list, else an empty list."""
    return value if isinstance(value, list) else []


def _missing_scopes(scopes: str) -> list[str]:
    have = {s.strip() for s in scopes.replace(" ", ",").split(",") if s.strip()}
    return [s for s in REQUIRED_SCOPES if s not in have]


def _adobe_text(description: str, creds: Credentials) -> str:
    """The error description of an answer, safe to show: no secret, the client ID only as
    mask_client_id() shows it, no character that changes the terminal, and no longer than
    ADOBE_TEXT_LIMIT."""
    text = description
    if creds.secret:
        text = text.replace(creds.secret, MASK)
    if creds.client_id:
        text = text.replace(creds.client_id, mask_client_id(creds.client_id))
    return inert(text)[:ADOBE_TEXT_LIMIT]


def failed_step(error: BaseException) -> str:
    """The step of the sign-in that a SignInError or a network error stopped."""
    if isinstance(error, SignInError):
        return error.step
    return PRODUCT_ACCESS if AFTER_TOKEN in getattr(error, "__notes__", ()) else SIGN_IN


def _is_temporary(status: int) -> bool:
    return status == 429 or status >= 500


def retry_after(delay: float, headers: Any) -> float:
    """The wait before the next try: delay, or longer when the Retry-After header asks for it.

    The wait is never longer than RETRY_AFTER_CAP, unless delay itself is. Retry-After may
    also be a date. Then the delay stays as it is.
    """
    value = headers.get("Retry-After") if headers else None
    try:
        if value:
            return min(RETRY_AFTER_CAP, max(delay, float(value)))
    except ValueError:
        pass  # Retry-After holds a date, which the delay already covers.
    return delay


def _cut_short(wait: Wait | None, attempt: int, resp: Any = None) -> bool:
    """Wait before the next try. True when the wait ended early, e.g., after Ctrl-C.

    resp is the temporary answer, if any. Its Retry-After header can make the wait longer.
    """
    delay = SIGN_IN_WAITS[min(attempt, len(SIGN_IN_WAITS)) - 1]
    if resp is not None:
        delay = retry_after(delay, getattr(resp, "headers", None))
    return bool((wait or _sleep)(delay))


def _tried(send: Callable[[], Any], wait: Wait | None = None) -> Any:
    """The answer of send(). Tries again while the answer or the network error is temporary.

    wait(seconds) waits between the tries. When it returns True, e.g., the wait of a stop
    signal that was set, there is no other try. The last answer is returned, or the last
    network error is raised.
    """
    attempt = 1
    while True:
        try:
            resp = send()
        except TEMPORARY_ERRORS:
            if attempt >= SIGN_IN_TRIES or _cut_short(wait, attempt):
                raise
        else:
            if (
                attempt >= SIGN_IN_TRIES
                or not _is_temporary(resp.status_code)
                or _cut_short(wait, attempt, resp)
            ):
                return resp
        attempt += 1


def get_token(session: Any, creds: Credentials, wait: Wait | None = None) -> str:
    """A new access token. wait(seconds) waits between the tries, see _tried."""
    resp = _tried(
        lambda: session.post(
            IMS_TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": creds.client_id,
                "client_secret": creds.secret,
                "scope": creds.scopes,
            },
            timeout=TIMEOUT,
        ),
        wait,
    )
    body = _json(resp)
    body = body if isinstance(body, dict) else {}
    token = body.get("access_token")
    if resp.status_code == 200 and isinstance(token, str) and token:
        return token
    if resp.status_code == 200:
        # Adobe's sign-in service answers 200 only with a token. Another answer with 200 comes
        # from something between, e.g., a proxy or the sign-in page of a network.
        raise SignInError(NOT_FROM_ADOBE, "unreachable")
    error = str(body.get("error") or "")
    desc = str(body.get("error_description") or "")
    if error == "invalid_client" and "secret" in desc:
        raise SignInError(WRONG_SECRET, "bad_secret")
    if error == "invalid_client":
        raise SignInError(WRONG_CLIENT_ID, "bad_client_id")
    if error == "invalid_scope":
        raise SignInError(SCOPE_REJECTED, "scopes_rejected")
    # The error code comes from the answer, which may not be from Adobe, e.g., from a proxy.
    code = f", error {inert(error)}" if error else ""
    adobe_text = _adobe_text(desc, creds)
    if _is_temporary(resp.status_code):
        raise SignInUnavailable(
            f"Sign-in failed: Adobe's sign-in service answered HTTP {resp.status_code}{code} "
            f"{SIGN_IN_TRIES} times. This is a problem at Adobe, not with the credential. Wait a "
            "little and try again.",
            adobe_text=adobe_text,
        )
    raise SignInError(
        f"Sign-in failed: Adobe answered HTTP {resp.status_code}{code}. Check the credential in "
        "the Adobe Developer Console, then try again.",
        adobe_text=adobe_text,
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
    resp = _tried(
        lambda: session.get(
            DISCOVERY_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "x-api-key": creds.client_id,
                "x-gw-ims-org-id": creds.org_id,
            },
            timeout=TIMEOUT,
        )
    )
    body = _json(resp)
    if _is_temporary(resp.status_code):
        raise SignInUnavailable(
            f"Sign-in worked, but the Analytics discovery call answered HTTP {resp.status_code} "
            f"{SIGN_IN_TRIES} times. This is a problem at Adobe, not with the credential. Wait a "
            "little and try again.",
            "unreachable",
            PRODUCT_ACCESS,
        )
    if resp.status_code != 200 or not isinstance(body, dict):
        raise SignInError(
            f"Sign-in worked, but the Analytics discovery call answered HTTP {resp.status_code}. "
            "Wait a little and try again.",
            "unreachable",
            PRODUCT_ACCESS,
        )
    orgs = [o for o in _list(body.get("imsOrgs")) if isinstance(o, dict)]
    if not orgs:
        missing = _missing_scopes(creds.scopes)
        if missing:
            raise SignInError(
                SCOPE_PROBLEM.format(missing=", ".join(missing)),
                "no_product_access",
                PRODUCT_ACCESS,
            )
        raise SignInError(NO_COMPANY, "no_product_access", PRODUCT_ACCESS)
    org = next(
        (o for o in orgs if str(o.get("imsOrgId", "")).lower() == creds.org_id.lower()), None
    )
    if org is None:
        raise SignInError(WRONG_ORG, "wrong_org")
    companies = [
        (str(c["globalCompanyId"]), str(c.get("companyName") or ""))
        for c in _list(org.get("companies"))
        if isinstance(c, dict) and c.get("globalCompanyId")
    ]
    if not companies:
        raise SignInError(NO_COMPANY, "no_product_access", PRODUCT_ACCESS)
    if company_id is None:
        return companies[0][0], companies[0][1], len(companies)
    # The login company may be given by its global company ID or by its name.
    wanted = company_id.strip().lower()
    for cid, name in companies:
        if wanted in (cid.lower(), name.lower()):
            return cid, name, len(companies)
    names = ", ".join(f"{inert(name) or '(no name)'} ({inert(cid)})" for cid, name in companies)
    raise SignInError(
        f"This credential cannot see the login company {company_id!r}. It can see: {names}.",
        "no_product_access",
        PRODUCT_ACCESS,
    )


def sign_in(session: Any, creds: Credentials, company_id: str | None = None) -> SignIn:
    token = get_token(session, creds)
    try:
        cid, name, count = find_company(session, token, creds, company_id)
    except requests.RequestException as e:
        # The error keeps its type and its text. The note only says which step failed.
        e.add_note(AFTER_TOKEN)
        raise
    return SignIn(cid, header_for(token, creds, cid), name, count)
