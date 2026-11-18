import pytest
import requests

from ahd.api.auth import RETRY_AFTER_CAP
from ahd.api.client import BUSY, FAILED, AdobeClient, ApiError

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


def test_parallel_401s_with_a_failed_sign_in_sign_in_once():
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from ahd.api.auth import SignInError

    workers = 6
    session = ExpiringSession(workers)
    logins = []
    lock = threading.Lock()

    def relogin():
        with lock:
            logins.append(1)
        raise SignInError("Sign-in failed: the client secret is wrong.")

    c = AdobeClient._from_parts(
        session, {"Authorization": "Bearer OLD"}, "co1", relogin, sleep=lambda d: None
    )

    def call(_):
        try:
            c.post_report({})
        except SignInError as e:
            return str(e)
        return "no error"

    with ThreadPoolExecutor(workers) as pool:
        results = list(pool.map(call, range(workers)))
    # The threads that waited for the failed sign-in get its error and do not try again.
    assert len(logins) == 1
    assert results == ["Sign-in failed: the client secret is wrong."] * workers


def test_a_request_after_a_failed_sign_in_tries_again():
    from ahd.api.auth import SignInError

    answers = iter([SignInError("busy"), {"Authorization": "Bearer NEW"}])

    def relogin():
        a = next(answers)
        if isinstance(a, Exception):
            raise a
        return a

    c, s, _ = make([(401, None, {}), (401, None, {}), (200, {"ok": 1}, {})], relogin)
    with pytest.raises(SignInError):
        c.get("/x")
    assert c.get("/x") == {"ok": 1}
    assert s.calls[-1][2]["Authorization"] == "Bearer NEW"


def test_connect_signs_in_and_relogin_gets_a_new_token(monkeypatch):
    from ahd.api import client as client_mod
    from ahd.api.auth import SignIn
    from ahd.config import Credentials

    creds = Credentials("ORG@AdobeOrg", "cid", "sec", "openid")
    tokens = iter(["T1", "T2"])
    waits = []
    monkeypatch.setattr(
        client_mod,
        "sign_in",
        lambda session, c, company_id=None: SignIn("co7", client_mod.header_for("T0", c, "co7")),
    )

    def get_token(session, c, wait=None):
        waits.append(wait)
        return next(tokens)

    monkeypatch.setattr(client_mod, "get_token", get_token)
    c = AdobeClient.connect(creds)
    assert (c._pacer.limit, c._pacer.window) == (12, 6.0)
    assert c.company_id == "co7"
    assert c._header["Authorization"] == "Bearer T0"
    assert c._relogin()["Authorization"] == "Bearer T1"
    assert c._relogin()["x-proxy-global-company-id"] == "co7"
    # The waits between the sign-in tries end when the client is stopped, e.g., on Ctrl-C.
    assert waits == [c._stop.wait] * 2


def _connected(monkeypatch):
    from ahd.api import client as client_mod
    from ahd.api.auth import SignIn
    from ahd.config import Credentials

    monkeypatch.setattr(
        client_mod, "sign_in", lambda session, c, company_id=None: SignIn("co7", {})
    )
    return AdobeClient.connect(Credentials("ORG@AdobeOrg", "cid", "sec", "openid"))


def test_the_connection_pool_holds_a_connection_for_each_worker(monkeypatch):
    from ahd.api.auth import IMS_TOKEN_URL
    from ahd.api.client import BASE, MAX_CONNECTIONS

    c = _connected(monkeypatch)
    for url in (BASE, IMS_TOKEN_URL):
        adapter = c._session.get_adapter(url)
        assert adapter._pool_maxsize >= MAX_CONNECTIONS
        # The tool tries again itself, so the connection pool must not.
        assert adapter.max_retries.total == 0


def test_the_connection_pool_is_as_large_as_the_most_workers_the_command_allows():
    import argparse

    from ahd import cli
    from ahd.api.client import MAX_CONNECTIONS

    sub = next(a for a in cli._parser()._actions if isinstance(a, argparse._SubParsersAction))
    workers = [
        action.type
        for parser in sub.choices.values()
        for action in parser._actions
        if action.dest == "workers"
    ]
    assert workers
    for parse in workers:
        assert parse(str(MAX_CONNECTIONS)) == MAX_CONNECTIONS
        for too_many in range(MAX_CONNECTIONS + 1, MAX_CONNECTIONS + 100):
            with pytest.raises(argparse.ArgumentTypeError):
                parse(str(too_many))


class WatchedLock:
    """A lock that sets an event when the thread `watched` starts to wait for it."""

    def __init__(self):
        import threading

        self._lock = threading.Lock()
        self.watched = None
        self.waiting = threading.Event()

    def __enter__(self):
        import threading

        if threading.current_thread() is self.watched:
            self.waiting.set()
        self._lock.acquire()

    def __exit__(self, *exc):
        self._lock.release()


def test_a_request_that_waited_for_a_sign_in_is_not_sent_after_stop():
    """A thread that waits for the header while another thread signs in again checks the stop
    signal after the wait, so it sends nothing after Ctrl-C."""
    import threading

    from ahd.api.client import Stopped

    signing_in = threading.Event()
    go_on = threading.Event()

    def relogin():
        signing_in.set()
        assert go_on.wait(5)
        return {"Authorization": "Bearer NEW"}

    session = FakeSession([(401, None, {}), (200, {"a": 1}, {}), (200, {"b": 2}, {})])
    c = AdobeClient._from_parts(session, dict(HEADER), "co1", relogin, sleep=lambda d: None)
    lock = WatchedLock()
    c._header_lock = lock
    results = {}

    def call(name):
        try:
            results[name] = c.get("/x")
        except Stopped as e:
            results[name] = e

    first = threading.Thread(target=call, args=("first",))
    first.start()
    assert signing_in.wait(5)  # the first thread holds the header lock while it signs in
    second = threading.Thread(target=call, args=("second",))
    lock.watched = second
    second.start()
    assert lock.waiting.wait(5)
    c.stop()
    go_on.set()
    first.join(5)
    second.join(5)
    assert isinstance(results["first"], Stopped) and isinstance(results["second"], Stopped)
    assert len(session.calls) == 1


def test_a_stopped_client_does_not_sign_in_again():
    from ahd.api.client import Stopped

    logins = []
    session = FakeSession([(401, None, {})])
    c = AdobeClient._from_parts(
        session, dict(HEADER), "co1", lambda: logins.append(1) or dict(HEADER)
    )
    answer = session.request

    def answer_then_stop(*a, **kw):
        c.stop()
        return answer(*a, **kw)

    session.request = answer_then_stop
    with pytest.raises(Stopped):
        c.get("/x")
    assert logins == [] and len(session.calls) == 1


def test_a_sign_in_cut_short_by_stop_raises_stopped():
    """A sign-in whose waits ended because of stop() fails. The request then says it was
    stopped, and the error is not kept for the next sign-in."""
    from ahd.api.auth import SignInUnavailable
    from ahd.api.client import Stopped

    c = None

    def relogin():
        c.stop()
        raise SignInUnavailable("busy")

    c, session, _ = make([(401, None, {})], relogin)
    with pytest.raises(Stopped):
        c.get("/x")
    assert c._sign_in_error is None and len(session.calls) == 1


def test_ctrl_c_during_a_sign_in_is_not_turned_into_stopped():
    c = None

    def relogin():
        c.stop()
        raise KeyboardInterrupt

    c, _session, _ = make([(401, None, {})], relogin)
    with pytest.raises(KeyboardInterrupt):
        c.get("/x")


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
    assert "up to 6 more times and waits about 30 seconds to 1 minute in all" in page
    assert "up to 3 more times and waits about 3 to 7 seconds in all" in page


def test_connection_wait_is_short_and_answer_wait_is_long():
    c, s, _sleeps = make([(200, {"ok": 1}, {})])
    c.get("/x")
    assert s.timeout == (10, 120)


class FakeClock:
    """A clock that moves only when someone sleeps, so the pacer tests do not wait."""

    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def _pacer(clock):
    from ahd.api.client import Pacer

    return Pacer(clock=clock, sleep=clock.sleep)


def test_pacer_starts_12_requests_then_waits_for_the_window():
    clock = FakeClock()
    pacer = _pacer(clock)
    for _ in range(12):
        pacer.wait()
    assert clock.sleeps == []
    pacer.wait()
    assert clock.sleeps == [6.0]


def test_pacer_does_not_wait_when_requests_are_spread_out():
    clock = FakeClock()
    pacer = _pacer(clock)
    for _ in range(30):
        pacer.wait()
        clock.now += 0.5  # 12 requests take 6 seconds
    assert clock.sleeps == []


def test_pacer_waits_only_until_the_oldest_start_leaves_the_window():
    clock = FakeClock()
    pacer = _pacer(clock)
    for _ in range(12):
        pacer.wait()
        clock.now += 0.25
    pacer.wait()  # at 3.0 s; the first start leaves the window at 6.0 s
    assert clock.sleeps == [3.0]


def test_pacer_keeps_the_limit_across_threads():
    import threading
    from collections import deque

    clock = FakeClock()
    pacer = _pacer(clock)
    starts = []

    class Recorded(deque):
        # The pacer appends each start while it holds its lock, so the times are exact.
        def append(self, now):
            starts.append(now)
            super().append(now)

    pacer._starts = Recorded()
    ready = threading.Barrier(8, timeout=5)

    def worker():
        ready.wait()  # every thread starts together
        for _ in range(10):
            pacer.wait()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(starts) == 80 and starts == sorted(starts)
    # Any 13 starts in a row span at least 6 seconds.
    assert all(starts[i + 12] - starts[i] >= 6.0 for i in range(len(starts) - 12))


def test_client_waits_for_the_pacer_before_each_try():
    clock = FakeClock()
    script = [(200, {"ok": 1}, {})] * 12 + [(429, None, {}), (200, {"ok": 1}, {})]
    sleeps = []
    session = FakeSession(script)
    c = AdobeClient._from_parts(
        session, dict(HEADER), "co1", lambda: dict(HEADER), sleep=sleeps.append, pacer=_pacer(clock)
    )
    for _ in range(13):
        c.get("/x")
    # The 13th request waited for the pacer. Its retry after the 429 counts as a request too.
    assert len(session.calls) == 14
    assert clock.sleeps[0] == 6.0 and len(sleeps) == 1


def test_a_stopped_client_sends_no_more_requests():
    from ahd.api.client import Stopped

    c, s, _ = make([(200, {"a": 1}, {})])
    c.stop()
    with pytest.raises(Stopped):
        c.get("/metrics")
    assert s.calls == []


def test_stop_ends_the_wait_before_another_try():
    """A request that Adobe answered with 503 waits and tries again. After stop(), the wait
    ends at once and the next try is not sent."""
    from ahd.api.client import Stopped

    session = FakeSession([(503, None, {}), (200, {"a": 1}, {})])
    # No sleep is given, so the client waits on its own stop signal.
    c = AdobeClient._from_parts(session, dict(HEADER), "co1", lambda: dict(HEADER))
    answer = session.request

    def answer_then_stop(*a, **kw):
        c.stop()
        return answer(*a, **kw)

    session.request = answer_then_stop
    with pytest.raises(Stopped):
        c.get("/metrics")
    assert len(session.calls) == 1


def test_a_stopped_pacer_does_not_wait():
    import threading

    from ahd.api.client import Pacer

    clock = FakeClock()
    stop = threading.Event()
    pacer = Pacer(clock=clock, sleep=clock.sleep, stop=stop)
    for _ in range(12):
        pacer.wait()
    stop.set()
    pacer.wait()
    assert clock.sleeps == []
