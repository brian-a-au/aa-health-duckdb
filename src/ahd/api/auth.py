"""OAuth Server-to-Server sign-in. Turns each way it can fail into a plain fix.

Two requests: the IMS token request, then the Analytics discovery call that names the
login company. Adobe accepts unknown scopes and ignores the org ID header in discovery, so
those two problems are found by reading the discovery answer. No value of the credential,
token or header ever goes into a message.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ahd.config import Credentials

IMS_TOKEN_URL = "https://ims-na1.adobelogin.com/ims/token/v3"
DISCOVERY_URL = "https://analytics.adobe.io/discovery/me"
TIMEOUT = 60

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


@dataclass(frozen=True)
class SignIn:
    company_id: str
    header: dict


def _json(resp: Any) -> Any:
    try:
        return resp.json()
    except ValueError:
        return None


def _missing_scopes(scopes: str) -> list[str]:
    have = {s.strip() for s in scopes.replace(" ", ",").split(",") if s.strip()}
    return [s for s in REQUIRED_SCOPES if s not in have]


def get_token(session: Any, creds: Credentials) -> str:
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


def find_company(session: Any, token: str, creds: Credentials, company_id: str | None) -> str:
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
    ids = [
        str(c["globalCompanyId"])
        for c in org.get("companies") or []
        if isinstance(c, dict) and c.get("globalCompanyId")
    ]
    if not ids:
        raise SignInError(NO_COMPANY)
    if company_id is None:
        return ids[0]
    if company_id not in ids:
        raise SignInError(
            f"This credential cannot see the login company {company_id!r}. "
            f"It can see {len(ids)} login companies."
        )
    return company_id


def sign_in(session: Any, creds: Credentials, company_id: str | None = None) -> SignIn:
    token = get_token(session, creds)
    cid = find_company(session, token, creds, company_id)
    return SignIn(cid, header_for(token, creds, cid))
