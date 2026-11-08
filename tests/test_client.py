import pytest
import requests

from ahd.api.client import BUSY, FAILED, RETRY_AFTER_CAP, AdobeClient, ApiError

# Every try, the first one included, for each kind of temporary error.
BUSY_TRIES = BUSY.retries + 1
FAILED_TRIES = FAILED.retries + 1

HEADER = {"Authorization": "Bearer SECRET-TOKEN", "x-api-key": "KEY123"}


class FakeResponse:
    def __init__(self, status, body, headers=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.text = "" if body is None else str(body)

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeSession:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def request(self, method, url, headers=None, params=None, json=None, timeout=None):
        self.calls.append((method, url, dict(headers or {}), params, json))
        self.timeout = timeout
        status, body, hdrs = self.script.pop(0)
        return FakeResponse(status, body, hdrs)


def make(script, relogin=None):
    sleeps = []
    session = FakeSession(script)
    c = AdobeClient._from_parts(
        session, dict(HEADER), "co1", relogin or (lambda: dict(HEADER)), sleep=sleeps.append
    )
    return c, session, sleeps


def test_get_url_and_headers():
    c, s, _ = make([(200, {"a": 1}, {})])
    assert c.get("/dimensions", {"x": 1}) == {"a": 1}
    method, url, hdrs, params, _ = s.calls[0]
    assert method == "GET"
    assert url == "https://analytics.adobe.io/api/co1/dimensions"
    assert hdrs["x-api-key"] == "KEY123" and params == {"x": 1}


def test_post_report_goes_to_reports():
    c, s, _ = make([(200, {"rows": []}, {})])
    assert c.post_report({"q": 1}) == {"rows": []}
    method, url, _, _, body = s.calls[0]
    assert method == "POST" and url.endswith("/api/co1/reports") and body == {"q": 1}


def test_retries_429_then_succeeds():
    c, s, sleeps = make([(429, None, {"Retry-After": "3"}), (429, None, {}), (200, {"ok": 1}, {})])
    assert c.post_report({}) == {"ok": 1}
    assert len(s.calls) == 3 and len(sleeps) == 2
    assert sleeps[0] >= 3


def test_gives_up_after_max_retries():
    c, s, sleeps = make([(503, None, {})] * FAILED_TRIES)
    with pytest.raises(ApiError) as e:
        c.get("/dimensions")
    assert e.value.status == 503
    assert len(s.calls) == FAILED_TRIES and len(sleeps) == FAILED.retries


def test_401_relogs_once():
    n = []

    def relogin():
        n.append(1)
        return {"Authorization": "Bearer NEW", "x-api-key": "KEY123"}

    c, s, _ = make([(401, None, {}), (200, {"ok": 1}, {})], relogin)
    assert c.get("/dimensions") == {"ok": 1}
    assert len(n) == 1
    assert s.calls[1][2]["Authorization"] == "Bearer NEW"


def test_401_twice_raises():
    c, s, _ = make([(401, None, {}), (401, None, {})])
    with pytest.raises(ApiError) as e:
        c.get("/x")
    assert e.value.status == 401 and len(s.calls) == 2


def test_206_returned_not_raised():
    body = {"columnErrors": {"errors": []}, "rows": []}
    c, _, _ = make([(206, body, {})])
    assert c.post_report({}) == body


@pytest.mark.parametrize("status", [200, 400])
@pytest.mark.parametrize(
    "body",
    [
        {"errorCode": "invalid_access", "errorDescription": "No Predictive", "errorId": "z"},
        {"error_code": "invalid_access", "message": "No Predictive"},
    ],
)
def test_error_code_raised_without_secrets(status, body):
    c, _, _ = make([(status, body, {})])
    with pytest.raises(ApiError) as e:
        c.post_report({})
    assert e.value.error_code == "invalid_access"
    assert e.value.status == status
    assert "Predictive" in e.value.message
    text = str(e.value) + repr(e.value)
    assert "Bearer" not in text and "SECRET-TOKEN" not in text and "KEY123" not in text


def test_other_4xx_raises():
    c, _, _ = make([(404, None, {})])
    with pytest.raises(ApiError) as e:
        c.get("/nope")
    assert e.value.status == 404 and e.value.error_code is None


def test_repr_has_no_secrets():
    c, _, _ = make([])
    assert "SECRET-TOKEN" not in repr(c) and "KEY123" not in repr(c)


class RaisingSession(FakeSession):
    """A script entry that is an exception instance is raised instead of answered."""

    def request(self, method, url, headers=None, params=None, json=None, timeout=None):
        self.calls.append((method, url, dict(headers or {}), params, json))
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        status, body, hdrs = item
        return FakeResponse(status, body, hdrs)


def make_raising(script):
    sleeps = []
    session = RaisingSession(script)
    c = AdobeClient._from_parts(
        session, dict(HEADER), "co1", lambda: dict(HEADER), sleep=sleeps.append
    )
    return c, session, sleeps


@pytest.mark.parametrize(
    "exc",
    [
        requests.ConnectionError("reset"),
        requests.Timeout("slow"),
        requests.exceptions.ChunkedEncodingError("Connection broken: IncompleteRead"),
    ],
)
def test_network_errors_retried_then_succeed(exc):
    c, s, sleeps = make_raising([exc, exc, (200, {"ok": 1}, {})])
    assert c.post_report({}) == {"ok": 1}
    assert len(s.calls) == 3 and len(sleeps) == 2
    assert 0.5 <= sleeps[0] <= 1.0 and 1.0 <= sleeps[1] <= 2.0


def test_network_error_raised_after_max_retries():
    errors = [requests.Timeout(f"slow {i}") for i in range(FAILED_TRIES)]
    c, s, sleeps = make_raising(list(errors))
    with pytest.raises(requests.Timeout) as e:
        c.get("/dimensions")
    assert e.value is errors[-1]
    assert len(s.calls) == FAILED_TRIES and len(sleeps) == FAILED.retries


TEMP_403 = {"errorCode": "403025", "errorDescription": "temporarily unavailable"}


def test_403025_is_retried_with_backoff():
    c, s, sleeps = make([(403, TEMP_403, {}), (403, TEMP_403, {}), (200, {"ok": 1}, {})])
    assert c.post_report({}) == {"ok": 1}
    assert len(s.calls) == 3 and len(sleeps) == 2


def test_403025_raises_after_last_try():
    c, s, sleeps = make([(403, TEMP_403, {})] * BUSY_TRIES)
    with pytest.raises(ApiError) as e:
        c.post_report({})
    assert e.value.status == 403 and e.value.error_code == "403025"
    assert len(s.calls) == BUSY_TRIES and len(sleeps) == BUSY.retries
    assert 30.5 <= sum(sleeps) <= 61


def test_other_403_is_not_retried():
    c, s, sleeps = make([(403, {"errorCode": "invalid_access", "errorDescription": "no"}, {})])
    with pytest.raises(ApiError):
        c.post_report({})
    assert len(s.calls) == 1 and sleeps == []


def test_403_without_body_is_not_retried():
    c, s, sleeps = make([(403, None, {})])
    with pytest.raises(ApiError):
        c.get("/x")
    assert len(s.calls) == 1 and sleeps == []


class ExpiringSession:
    """Answers 401 to the old token until every worker has seen it, then 200 to the new one."""

    def __init__(self, workers):
        import threading

        self.barrier = threading.Barrier(workers, timeout=5)
        self.lock = threading.Lock()
        self.tokens = []

    def request(self, method, url, headers=None, params=None, json=None, timeout=None):
        token = headers["Authorization"]
        with self.lock:
            self.tokens.append(token)
        if token == "Bearer OLD":
            self.barrier.wait()  # every worker holds the old token before any renewal
            return FakeResponse(401, None)
        return FakeResponse(200, {"token": token})


def test_parallel_401s_sign_in_once():
    import threading
    from concurrent.futures import ThreadPoolExecutor

    workers = 6
    session = ExpiringSession(workers)
    logins = []
    lock = threading.Lock()

    def relogin():
        with lock:
            logins.append(1)
            n = len(logins)
        return {"Authorization": f"Bearer NEW{n}"}

    c = AdobeClient._from_parts(
        session, {"Authorization": "Bearer OLD"}, "co1", relogin, sleep=lambda d: None
    )
    with ThreadPoolExecutor(workers) as pool:
        results = list(pool.map(lambda _: c.post_report({}), range(workers)))
    assert len(logins) == 1
    assert results == [{"token": "Bearer NEW1"}] * workers
    assert session.tokens.count("Bearer OLD") == workers


def test_connect_signs_in_and_relogin_gets_a_new_token(monkeypatch):
    from ahd.api import client as client_mod
    from ahd.api.auth import SignIn
    from ahd.config import Credentials

    creds = Credentials("ORG@AdobeOrg", "cid", "sec", "openid")
    tokens = iter(["T1", "T2"])
    monkeypatch.setattr(
        client_mod,
        "sign_in",
        lambda session, c, company_id=None: SignIn("co7", client_mod.header_for("T0", c, "co7")),
    )
    monkeypatch.setattr(client_mod, "get_token", lambda session, c: next(tokens))
    c = AdobeClient.connect(creds)
    assert c.company_id == "co7"
    assert c._header["Authorization"] == "Bearer T0"
    assert c._relogin()["Authorization"] == "Bearer T1"
    assert c._relogin()["x-proxy-global-company-id"] == "co7"


@pytest.mark.parametrize("body", [None, "a string", 3])
def test_a_2xx_answer_that_is_not_json_is_an_api_error(body):
    from ahd.api.client import NOT_JSON

    c, _session, _sleeps = make([(200, body, {})])
    with pytest.raises(ApiError) as e:
        c.post_report({})
    assert (e.value.error_code, e.value.message) == ("invalid_response", NOT_JSON)


def test_retries_wait_long_enough_to_outlast_a_short_rate_limit():
    c, s, sleeps = make([(429, None, {})] * BUSY_TRIES)
    with pytest.raises(ApiError):
        c.post_report({})
    assert len(s.calls) == BUSY_TRIES
    assert 30.5 <= sum(sleeps) <= 61


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_a_server_error_is_reported_within_seconds(status):
    c, s, sleeps = make([(status, None, {})] * FAILED_TRIES)
    with pytest.raises(ApiError):
        c.get("/reportsuites/collections/suites")
    assert len(s.calls) == FAILED_TRIES
    assert 3.5 <= sum(sleeps) <= 7


def test_no_network_is_reported_within_seconds():
    c, s, sleeps = make_raising([requests.ConnectionError("no route")] * FAILED_TRIES)
    with pytest.raises(requests.ConnectionError):
        c.get("/reportsuites/collections/suites")
    assert len(s.calls) == FAILED_TRIES
    assert 3.5 <= sum(sleeps) <= 7


def test_each_kind_of_error_has_its_own_limit():
    # Three server errors use up their retries, but a rate limit after them is still retried.
    script = [(503, None, {})] * FAILED.retries + [(429, None, {}), (200, {"ok": 1}, {})]
    c, s, sleeps = make(script)
    assert c.post_report({}) == {"ok": 1}
    assert len(s.calls) == len(script) and len(sleeps) == len(script) - 1


def test_a_long_retry_after_is_capped():
    c, _session, sleeps = make([(429, None, {"Retry-After": "3600"}), (200, {"ok": 1}, {})])
    assert c.post_report({}) == {"ok": 1}
    assert sleeps == [RETRY_AFTER_CAP]


def test_a_server_error_honors_retry_after():
    c, _session, sleeps = make([(503, None, {"Retry-After": "9"}), (200, {"ok": 1}, {})])
    assert c.post_report({}) == {"ok": 1}
    assert sleeps == [9.0]


def test_wait_totals_match_the_troubleshooting_page():
    from pathlib import Path

    assert BUSY.total() == (30.5, 61.0)
    assert FAILED.total() == (3.5, 7.0)
    page = (Path(__file__).parents[1] / "docs" / "troubleshooting.md").read_text(encoding="utf-8")
    assert "up to 6 more times and waits 30 to 60 seconds in all" in page
    assert "up to 3 more times and waits 3 to 7 seconds in all" in page


def test_connection_wait_is_short_and_answer_wait_is_long():
    c, s, _sleeps = make([(200, {"ok": 1}, {})])
    c.get("/x")
    assert s.timeout == (10, 120)
