import pytest

from ahd.api.auth import (
    DISCOVERY_URL,
    IMS_TOKEN_URL,
    NO_COMPANY,
    SCOPE_REJECTED,
    WRONG_CLIENT_ID,
    WRONG_ORG,
    WRONG_SECRET,
    SignInError,
    sign_in,
)
from ahd.config import Credentials

FULL_SCOPES = "openid, AdobeID, read_organizations, additional_info.projectedProductContext"
CREDS = Credentials("ORG1@AdobeOrg", "client-abc", "secret-xyz", FULL_SCOPES)


class Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class Session:
    def __init__(self, token, discovery=(200, None)):
        self.token = token
        self.discovery = discovery
        self.calls = []

    def post(self, url, data=None, timeout=None):
        self.calls.append(("POST", url, data))
        return Resp(*self.token)

    def get(self, url, headers=None, timeout=None):
        self.calls.append(("GET", url, headers))
        return Resp(*self.discovery)


GOOD_TOKEN = (200, {"access_token": "TOKEN-1", "expires_in": 86399})


def orgs(*entries):
    return (200, {"imsOrgs": list(entries)})


def org(org_id="ORG1@AdobeOrg", companies=("co1",)):
    return {
        "imsOrgId": org_id,
        "companies": [{"globalCompanyId": c, "companyName": f"Name {c}"} for c in companies],
    }


def _fails(session, creds=CREDS, company_id=None):
    with pytest.raises(SignInError) as e:
        sign_in(session, creds, company_id)
    text = str(e.value)
    assert creds.secret not in text and "TOKEN-1" not in text
    return text


def test_sign_in_builds_header():
    s = Session(GOOD_TOKEN, orgs(org(companies=("co1", "co2"))))
    result = sign_in(s, CREDS)
    assert result.company_id == "co1"
    assert (result.company_name, result.companies) == ("Name co1", 2)
    assert result.header["Authorization"] == "Bearer TOKEN-1"
    assert result.header["x-api-key"] == "client-abc"
    assert result.header["x-proxy-global-company-id"] == "co1"
    assert s.calls[0][:2] == ("POST", IMS_TOKEN_URL)
    assert s.calls[0][2]["client_secret"] == "secret-xyz"
    assert s.calls[1][:2] == ("GET", DISCOVERY_URL)
    assert s.calls[1][2]["x-gw-ims-org-id"] == "ORG1@AdobeOrg"


def test_org_match_ignores_case_and_picks_the_right_org():
    s = Session(GOOD_TOKEN, orgs(org("OTHER@AdobeOrg", ("x",)), org("org1@adobeorg", ("co9",))))
    assert sign_in(s, CREDS).company_id == "co9"


def test_chosen_company():
    s = Session(GOOD_TOKEN, orgs(org(companies=("co1", "co2"))))
    assert sign_in(s, CREDS, "co2").company_id == "co2"
    assert sign_in(s, CREDS, "name CO2").company_id == "co2"  # by name, any case
    text = _fails(s, company_id="co3")
    assert "cannot see the login company 'co3'" in text
    assert "Name co1 (co1), Name co2 (co2)" in text


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        (
            (400, {"error": "invalid_client", "error_description": "invalid client_id parameter"}),
            WRONG_CLIENT_ID,
        ),
        (
            (
                400,
                {"error": "invalid_client", "error_description": "invalid client_secret parameter"},
            ),
            WRONG_SECRET,
        ),
        ((400, {"error": "invalid_scope", "error_description": "bad"}), SCOPE_REJECTED),
    ],
)
def test_token_errors(token, expected):
    assert _fails(Session(token)) == expected


def test_token_other_error_names_status_and_code():
    text = _fails(Session((503, {"error": "server_error"})))
    assert "HTTP 503" in text and "server_error" in text


def test_token_not_json():
    text = _fails(Session((502, None)))
    assert "HTTP 502" in text and "error" not in text.split("HTTP 502")[1].split(".")[0]


def test_token_200_without_token():
    assert "HTTP 200" in _fails(Session((200, {"nope": 1})))


def test_wrong_org_id():
    assert _fails(Session(GOOD_TOKEN, orgs(org("OTHER@AdobeOrg")))) == WRONG_ORG


def test_no_orgs_with_missing_scopes_names_them():
    creds = Credentials("ORG1@AdobeOrg", "client-abc", "secret-xyz", "openid AdobeID")
    text = _fails(Session(GOOD_TOKEN, orgs()), creds)
    assert "scopes are wrong" in text and "additional_info.projectedProductContext" in text
    assert "openid," not in text


def test_no_orgs_with_full_scopes_is_product_profile():
    assert _fails(Session(GOOD_TOKEN, orgs())) == NO_COMPANY


def test_org_without_companies_is_product_profile():
    assert _fails(Session(GOOD_TOKEN, orgs(org(companies=())))) == NO_COMPANY


@pytest.mark.parametrize("discovery", [(403, {"error": "x"}), (200, None), (200, [1])])
def test_discovery_failure(discovery):
    assert "discovery call answered" in _fails(Session(GOOD_TOKEN, discovery))
