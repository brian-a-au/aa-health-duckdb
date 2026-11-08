import pytest
import requests

from ahd.api.client import AdobeClient, ApiError

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
    c, s, sleeps = make([(503, None, {})] * 5)
    with pytest.raises(ApiError) as e:
        c.get("/dimensions")
    assert e.value.status == 503
    assert len(s.calls) == 5 and len(sleeps) == 4


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


@pytest.mark.parametrize("exc", [requests.ConnectionError("reset"), requests.Timeout("slow")])
def test_network_errors_retried_then_succeed(exc):
    c, s, sleeps = make_raising([exc, exc, (200, {"ok": 1}, {})])
    assert c.post_report({}) == {"ok": 1}
    assert len(s.calls) == 3 and len(sleeps) == 2
    assert all(0 <= d <= 10.0 for d in sleeps)


def test_network_error_raised_after_max_retries():
    errors = [requests.Timeout(f"slow {i}") for i in range(5)]
    c, s, sleeps = make_raising(list(errors))
    with pytest.raises(requests.Timeout) as e:
        c.get("/dimensions")
    assert e.value is errors[-1]
    assert len(s.calls) == 5 and len(sleeps) == 4
