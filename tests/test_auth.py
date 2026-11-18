import pytest

from ahd.api.auth import (
    DISCOVERY_URL,
    IMS_TOKEN_URL,
    NO_COMPANY,
    NOT_FROM_ADOBE,
    RETRY_AFTER_CAP,
    SCOPE_REJECTED,
    SIGN_IN_TRIES,
    WRONG_CLIENT_ID,
    WRONG_ORG,
    WRONG_SECRET,
    SignInError,
    get_token,
    sign_in,
)
from ahd.config import Credentials

FULL_SCOPES = "openid, AdobeID, read_organizations, additional_info.projectedProductContext"
CREDS = Credentials("ORG1@AdobeOrg", "client-abc", "secret-xyz", FULL_SCOPES)


class Resp:
    def __init__(self, status, body, headers=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}

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


@pytest.mark.parametrize("body", [None, {"nope": 1}, ["TOKEN-1"], {"access_token": ""}])
def test_token_200_without_token_says_it_is_not_from_adobe(body):
    text = _fails(Session((200, body)))
    assert text == NOT_FROM_ADOBE
    assert "did not come from Adobe's sign-in service" in text and "proxy" in text
    assert "Check the credential" not in text


def test_a_sign_in_page_of_the_network_is_not_from_adobe():
    """A proxy or a network sign-in page answers HTTP 200 with an HTML page."""

    class Html(Resp):
        def json(self):
            raise ValueError("Expecting value: line 1 column 1 (char 0)")

    class HtmlSession(Session):
        def post(self, url, data=None, timeout=None):
            return Html(200, "<html><body>Please log in to the Wi-Fi</body></html>")

    assert _fails(HtmlSession(GOOD_TOKEN)) == NOT_FROM_ADOBE


def test_a_dropped_connection_in_sign_in_is_tried_again(waits):
    import requests

    s = ScriptedSession(requests.exceptions.ChunkedEncodingError("dropped"), GOOD_TOKEN)
    assert sign_in(s, CREDS).header["Authorization"] == "Bearer TOKEN-1"
    assert len(_posts(s)) == 2 and len(waits) == 1


class HeaderSession(Session):
    """Answers the token requests in turn, each as (status, body, headers)."""

    def __init__(self, *answers):
        super().__init__(GOOD_TOKEN, orgs(org()))
        self.answers = list(answers)

    def post(self, url, data=None, timeout=None):
        self.calls.append(("POST", url, data))
        return Resp(*self.answers.pop(0))


def test_a_rate_limit_waits_for_its_retry_after(waits):
    s = HeaderSession(
        (429, None, {"Retry-After": "7"}), (429, None, {"Retry-After": "11"}), GOOD_TOKEN
    )
    assert sign_in(s, CREDS).company_id == "co1"
    assert waits == [7.0, 11.0]


def test_a_short_retry_after_keeps_the_own_wait(waits):
    s = HeaderSession((429, None, {"Retry-After": "0"}), (429, None, {}), GOOD_TOKEN)
    assert sign_in(s, CREDS).company_id == "co1"
    assert waits == [1.0, 3.0]


def test_a_long_retry_after_in_sign_in_is_capped(waits):
    s = HeaderSession((429, None, {"Retry-After": "3600"}), GOOD_TOKEN)
    assert sign_in(s, CREDS).company_id == "co1"
    assert waits == [RETRY_AFTER_CAP]


def test_a_retry_after_date_keeps_the_own_wait(waits):
    date = "Wed, 18 Nov 2026 07:28:00 GMT"
    s = HeaderSession((429, None, {"Retry-After": date}), GOOD_TOKEN)
    assert sign_in(s, CREDS).company_id == "co1"
    assert waits == [1.0]


def test_the_wait_of_the_caller_gets_the_retry_after(waits):
    s = HeaderSession((429, None, {"Retry-After": "5"}), GOOD_TOKEN)
    seen = []
    assert get_token(s, CREDS, wait=lambda seconds: seen.append(seconds)) == "TOKEN-1"
    assert seen == [5.0] and waits == []


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


class ScriptedDiscovery(Session):
    """Answers the discovery calls in turn. An exception in the script is raised."""

    def __init__(self, *answers):
        super().__init__(GOOD_TOKEN)
        self.answers = list(answers)

    def get(self, url, headers=None, timeout=None):
        self.calls.append(("GET", url, headers))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return Resp(*answer)


def _gets(session):
    return [c for c in session.calls if c[0] == "GET"]


@pytest.mark.parametrize("status", [429, 500, 503])
def test_a_temporary_discovery_error_is_tried_again(waits, status):
    s = ScriptedDiscovery((status, None), (status, {"error": "x"}), orgs(org()))
    assert sign_in(s, CREDS).company_id == "co1"
    assert len(_gets(s)) == 3 and waits == [1.0, 3.0]


def test_a_network_error_in_discovery_is_tried_again(waits):
    import requests

    s = ScriptedDiscovery(requests.ConnectionError("https://analytics/x"), orgs(org()))
    assert sign_in(s, CREDS).company_id == "co1"
    assert len(_gets(s)) == 2 and len(waits) == 1


def test_a_lasting_network_error_in_discovery_is_raised(waits):
    import requests

    s = ScriptedDiscovery(*[requests.Timeout("t")] * SIGN_IN_TRIES)
    with pytest.raises(requests.Timeout):
        sign_in(s, CREDS)
    assert len(_gets(s)) == SIGN_IN_TRIES


def test_a_lasting_discovery_server_error_says_it_is_temporary(waits):
    from ahd.api.auth import SignInUnavailable

    s = ScriptedDiscovery(*[(503, None)] * SIGN_IN_TRIES)
    with pytest.raises(SignInUnavailable) as e:
        sign_in(s, CREDS)
    text = str(e.value)
    assert "discovery call answered HTTP 503 3 times" in text
    assert "This is a problem at Adobe, not with the credential" in text
    assert "secret-xyz" not in text and "TOKEN-1" not in text and "https" not in text
    assert len(_gets(s)) == SIGN_IN_TRIES and len(waits) == SIGN_IN_TRIES - 1


def test_a_discovery_error_that_is_not_temporary_is_not_tried_again(waits):
    s = ScriptedDiscovery((403, {"error": "x"}))
    assert "discovery call answered HTTP 403" in _fails(s)
    assert len(_gets(s)) == 1 and waits == []


def test_a_wait_that_is_cut_short_ends_the_tries(waits):
    """The client passes the wait of its stop signal, so Ctrl-C ends the tries at once."""
    from ahd.api.auth import SignInUnavailable, get_token

    cut = []
    s = ScriptedSession(*[(503, None)] * SIGN_IN_TRIES)
    with pytest.raises(SignInUnavailable):
        get_token(s, CREDS, wait=lambda seconds: cut.append(seconds) or True)
    assert len(_posts(s)) == 1 and cut == [1.0] and waits == []


def test_a_wait_that_is_cut_short_after_a_network_error_raises_it(waits):
    import requests

    from ahd.api.auth import get_token

    s = ScriptedSession(requests.ConnectionError("c"), GOOD_TOKEN)
    with pytest.raises(requests.ConnectionError):
        get_token(s, CREDS, wait=lambda seconds: True)
    assert len(_posts(s)) == 1


def test_a_wait_that_runs_its_full_time_goes_on(waits):
    from ahd.api.auth import get_token

    s = ScriptedSession((503, None), GOOD_TOKEN)
    assert get_token(s, CREDS, wait=lambda seconds: False) == "TOKEN-1"
    assert len(_posts(s)) == 2


@pytest.mark.parametrize("companies", [5, "text", {"globalCompanyId": "co1"}])
def test_companies_in_another_form_are_no_company(companies):
    entry = {"imsOrgId": "ORG1@AdobeOrg", "companies": companies}
    assert _fails(Session(GOOD_TOKEN, orgs(entry))) == NO_COMPANY


@pytest.mark.parametrize("found", [5, "text", {"imsOrgId": "ORG1@AdobeOrg"}])
def test_orgs_in_another_form_are_no_org(found):
    assert _fails(Session(GOOD_TOKEN, (200, {"imsOrgs": found}))) == NO_COMPANY
