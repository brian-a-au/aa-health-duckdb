import pytest

from ahd.api.auth import (
    DISCOVERY_URL,
    IMS_TOKEN_URL,
    NO_COMPANY,
    SCOPE_REJECTED,
    SIGN_IN_TRIES,
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
    text = _fails(Session((418, {"error": "teapot"})))
    assert "HTTP 418" in text and "teapot" in text


class ScriptedSession(Session):
    """Answers the token requests in turn. An exception in the script is raised."""

    def __init__(self, *answers):
        super().__init__(GOOD_TOKEN, orgs(org()))
        self.answers = list(answers)

    def post(self, url, data=None, timeout=None):
        self.calls.append(("POST", url, data))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return Resp(*answer)


@pytest.fixture
def waits(monkeypatch):
    from ahd.api import auth

    seen = []
    monkeypatch.setattr(auth, "_sleep", seen.append)
    return seen


def _posts(session):
    return [c for c in session.calls if c[0] == "POST"]


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_a_temporary_sign_in_error_is_tried_again(waits, status):
    s = ScriptedSession((status, None), (status, {"error": "server_error"}), GOOD_TOKEN)
    assert sign_in(s, CREDS).header["Authorization"] == "Bearer TOKEN-1"
    assert len(_posts(s)) == 3 and len(waits) == 2


def test_a_network_error_in_sign_in_is_tried_again(waits):
    import requests

    s = ScriptedSession(requests.ConnectionError("https://ims/x"), GOOD_TOKEN)
    assert sign_in(s, CREDS).company_id == "co1"
    assert len(_posts(s)) == 2 and len(waits) == 1


def test_a_lasting_network_error_in_sign_in_is_raised(waits):
    import requests

    s = ScriptedSession(*[requests.Timeout("t")] * SIGN_IN_TRIES)
    with pytest.raises(requests.Timeout):
        sign_in(s, CREDS)
    assert len(_posts(s)) == SIGN_IN_TRIES


def test_a_lasting_server_error_says_it_is_temporary(waits):
    from ahd.api.auth import SignInUnavailable

    s = ScriptedSession(*[(503, {"error": "server_error"})] * SIGN_IN_TRIES)
    with pytest.raises(SignInUnavailable) as e:
        sign_in(s, CREDS)
    text = str(e.value)
    assert "HTTP 503" in text and "Wait a little and try again." in text
    assert "Check the credential" not in text and "secret-xyz" not in text
    assert len(_posts(s)) == SIGN_IN_TRIES and len(waits) == SIGN_IN_TRIES - 1


def test_a_wrong_credential_is_not_tried_again(waits):
    bad = (400, {"error": "invalid_client", "error_description": "invalid client_secret"})
    s = ScriptedSession(bad)
    assert _fails(s) == WRONG_SECRET
    assert len(_posts(s)) == 1 and waits == []


def test_token_not_json():
    text = _fails(Session((404, None)))
    assert "HTTP 404" in text and "error" not in text.split("HTTP 404")[1].split(".")[0]


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


@pytest.mark.parametrize("companies", [5, "text", {"globalCompanyId": "co1"}])
def test_companies_in_another_form_are_no_company(companies):
    entry = {"imsOrgId": "ORG1@AdobeOrg", "companies": companies}
    assert _fails(Session(GOOD_TOKEN, orgs(entry))) == NO_COMPANY


@pytest.mark.parametrize("found", [5, "text", {"imsOrgId": "ORG1@AdobeOrg"}])
def test_orgs_in_another_form_are_no_org(found):
    assert _fails(Session(GOOD_TOKEN, (200, {"imsOrgs": found}))) == NO_COMPANY
