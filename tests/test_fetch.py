from datetime import UTC, date, datetime

import pytest
from fakes import (
    FakeClient,
    breakdown_dimension,
    date_range,
    exists_dims,
    fake_for,
    is_anomaly_trend,
    is_trend,
    metric_ids,
    restricted_dimensions,
    restricted_metrics,
    trend_response,
)

from ahd.api.client import ApiError
from ahd.api.reports import Column
from ahd.dates import RunWindow
from ahd.fetch import RunOptions, fetch_catalog, probe_anomaly, run_fetch
from ahd.store import Store

TODAY = date(2026, 11, 7)
W = RunWindow.for_today(TODAY)
NO_CUSTOM_MSG = (
    "The API returned no custom eVars, props, or events. "
    "The credential's product profile probably hides them."
)


def events(n):
    return [{"id": f"metrics/event{i}", "name": f"Event {i}"} for i in range(1, n + 1)]


def custom_dims():
    return [{"id": "variables/prop1", "name": "Prop 1", "allowedForReporting": True},
            {"id": "variables/page", "name": "Page", "allowedForReporting": True}]


@pytest.fixture
def store(tmp_path):
    return Store.open(tmp_path / "t.duckdb")


def opts(**kw):
    kw.setdefault("today", TODAY)
    return RunOptions(rsid="rs1", **kw)


def issues(store, run_id):
    return store.con.execute(
        "select scope, component_id, code, message from issues where run_id = ?"
        " order by scope, component_id", [run_id]).fetchall()


def issue_codes(store, run_id):
    return {r[2] for r in issues(store, run_id)}


def values_for(store, run_id, cid):
    return store.con.execute(
        "select count(*) from daily_values where run_id = ? and component_id = ?",
        [run_id, cid]).fetchone()[0]


# ---- batching and ranges -----------------------------------------------------

def test_batches_respect_batch_size(store):
    fake = fake_for("rs1", events(44), custom_dims()[1:])  # 44 events + page = 45 columns
    run_fetch(fake, store, opts(metric="metrics/visits"))
    sizes = sorted(len(metric_ids(b)) for b in fake.trend_bodies())
    # probe (1), individual (1), batches 20 + 20 + 5
    assert sizes == [1, 1, 5, 20, 20]
    # the probe is always the first call; every event appears exactly once after it
    later = [i for b in fake.trend_bodies()[1:] for i in metric_ids(b)]
    assert sorted(i for i in later if i.startswith("metrics/event")) == sorted(
        f"metrics/event{n}" for n in range(1, 45))


def test_trend_bodies_use_full_window(store):
    fake = fake_for("rs1", events(3), custom_dims())
    run_fetch(fake, store, opts())
    trends = fake.trend_bodies()
    assert len(trends) >= 3
    assert all(date_range(b) == W.trend_range() for b in trends)
    assert W.trend_range() == "2026-08-08T00:00:00.000/2026-11-08T00:00:00.000"


def test_values_stored_and_status_ok(store):
    fake = fake_for("rs1", events(2), custom_dims())
    rid = run_fetch(fake, store, opts())
    days = 92  # today-91 .. today, today fetched too
    for cid in ["metrics/event1", "metrics/event2", "variables/prop1", "variables/page",
                "individual"]:
        assert values_for(store, rid, cid) == days
    assert issues(store, rid) == []
    assert store.get_run(rid)["status"] == "ok"
    comp_ids = {r[0] for r in store.con.execute(
        "select component_id from components where run_id = ?", [rid]).fetchall()}
    assert "individual" not in comp_ids
    assert {"metrics/event1", "variables/prop1", "variables/page"} <= comp_ids


# ---- anomaly probe (Review Focus 3) ------------------------------------------

def test_anomaly_not_allowed_continues(store):
    denied = ApiError(403, "anomaly detection not allowed", "invalid_access")
    fake = fake_for("rs1", events(3), custom_dims(), posts=[(is_anomaly_trend, denied)])
    run_id = run_fetch(fake, store, opts())
    assert issue_codes(store, run_id) == {"anomaly_not_allowed"}
    (scope, _cid, _code, msg), = issues(store, run_id)
    assert scope == "permissions" and msg
    trends = fake.trend_bodies()
    assert sum(1 for b in trends if is_anomaly_trend(b)) == 1  # never retried
    later_trend_bodies = trends[1:]
    assert later_trend_bodies
    assert all(b["settings"]["includeAnomalyDetection"] is False for b in later_trend_bodies)
    assert values_for(store, run_id, "metrics/event1") == 92
    assert store.get_run(run_id)["status"] == "partial"


def test_anomaly_allowed_keeps_anomaly_on(store):
    fake = fake_for("rs1", events(3), custom_dims())
    run_fetch(fake, store, opts())
    assert all(b["settings"]["includeAnomalyDetection"] is True for b in fake.trend_bodies())


def test_probe_anomaly_reraises_other_errors():
    boom = ApiError(500, "server", "server_error")
    fake = FakeClient(posts=[(is_trend, boom)])
    with pytest.raises(ApiError):
        probe_anomaly(fake, "rs1", W, Column("metrics/a", "metrics/a"))
    deny = FakeClient(posts=[(is_trend, ApiError(403, "no", "invalid_access"))])
    assert probe_anomaly(deny, "rs1", W, Column("metrics/a", "metrics/a"), "seg") is False
    body = deny.posted()[0]
    assert body["settings"]["includeAnomalyDetection"] is True
    assert body["globalFilters"][-1] == {"type": "segment", "segmentId": "seg"}
    assert probe_anomaly(FakeClient(), "rs1", W, Column("metrics/a", "metrics/a")) is True


# ---- permission gaps (Review Focus 1) ----------------------------------------

def test_column_errors_become_issues_not_zeros(store):
    def respond(body):
        ids = metric_ids(body)
        errs = {i: "unauthorized_metric" for i, m in enumerate(ids) if m == "metrics/event2"}
        return trend_response(body, value=7.0, column_errors=errs)

    fake = fake_for("rs1", events(3), custom_dims(), posts=[(is_trend, respond)])
    rid = run_fetch(fake, store, opts())
    assert values_for(store, rid, "metrics/event2") == 0
    zeros = store.con.execute(
        "select count(*) from daily_values where run_id = ? and value = 0", [rid]).fetchone()[0]
    assert zeros == 0
    assert values_for(store, rid, "metrics/event1") == 92
    rows = [r for r in issues(store, rid) if r[0] == "column"]
    assert [(r[1], r[2]) for r in rows] == [("metrics/event2", "unauthorized_metric")]
    assert store.get_run(rid)["status"] == "partial"


def after_probe(pred):
    """Wrap a predicate so it never matches the first trend body (the anomaly probe)."""
    seen = []

    def wrapped(body):
        if is_trend(body) and not seen:
            seen.append(body)
            return False
        return pred(body)
    return wrapped


def test_batch_api_error_one_issue_per_column(store):
    @after_probe
    def failing_batch(body):
        return is_trend(body) and "metrics/event2" in metric_ids(body)

    fake = fake_for("rs1", events(3), custom_dims(),
                    posts=[(failing_batch, ApiError(400, "bad request", "invalid_request"))])
    rid = run_fetch(fake, store, opts(batch_size=2))
    col_issues = [(r[1], r[2]) for r in issues(store, rid) if r[0] == "column"]
    # catalog order: event1, event2, event3, prop1, page -> batch [event1, event2] fails
    assert col_issues == [("metrics/event1", "invalid_request"),
                          ("metrics/event2", "invalid_request")]
    assert values_for(store, rid, "metrics/event3") == 92
    assert values_for(store, rid, "individual") == 92
    assert store.get_run(rid)["status"] == "partial"


def test_failed_when_no_values(store):
    fake = fake_for("rs1", events(2), custom_dims(),
                    posts=[(after_probe(is_trend), ApiError(400, "nope", "invalid_request"))])
    rid = run_fetch(fake, store, opts())
    assert store.get_run(rid)["status"] == "failed"


def test_no_custom_components_flagged(store):
    fake = fake_for("rs1", restricted_metrics(), restricted_dimensions())
    assert len(restricted_dimensions()) == 87 and len(restricted_metrics()) == 15
    rid = run_fetch(fake, store, opts())
    rows = [r for r in issues(store, rid) if r[2] == "no_custom_components"]
    assert rows == [("permissions", None, "no_custom_components", NO_CUSTOM_MSG)]
    assert store.get_run(rid)["status"] == "partial"


# ---- individual component ----------------------------------------------------

def test_individual_requests(store):
    fake = fake_for("rs1", events(1), custom_dims())
    rid = run_fetch(fake, store, opts(dimension="variables/page", metric="metrics/occurrences"))
    ranked = [b for b in fake.posted() if not is_trend(b)]
    top = [b for b in ranked if breakdown_dimension(b) is None]
    assert sorted(date_range(b) for b in top) == sorted([W.yesterday_range(),
                                                         W.historical_range()])
    assert all(b["dimension"] == "variables/page" and b["settings"]["limit"] == 100
               for b in top)
    bd = {breakdown_dimension(b): b for b in ranked if breakdown_dimension(b)}
    ids = {k: [f["itemId"] for f in b["metricContainer"]["metricFilters"]] for k, b in bd.items()}
    assert ids == {
        "variables/browsertype": ["8", "2", "6", "7", "1"],
        "variables/mobiledevicetype": ["0", "2163986270", "1728229488", "3464263970",
                                       "2355485956"],
    }
    assert all(date_range(b) == W.yesterday_range() for b in bd.values())
    # catalog is 3 columns in one batch, so the only later 1-column trend is the individual
    ind, = [b for b in fake.trend_bodies()[1:] if len(metric_ids(b)) == 1]
    assert metric_ids(ind) == ["metrics/occurrences"]
    assert exists_dims(ind) == ["variables/page"]

    con = store.con
    periods = con.execute("select period, count(*) from top_items where run_id = ?"
                          " group by period order by period", [rid]).fetchall()
    assert periods == [("current", 2), ("historical", 2)]
    labels = con.execute("select kind, label from breakdown_totals where run_id = ?"
                         " order by kind, col_index", [rid]).fetchall()
    assert labels == [("browser", "Google"), ("browser", "Microsoft"), ("browser", "Apple"),
                      ("browser", "Mozilla"), ("browser", "Other"),
                      ("device", "Other"), ("device", "Mobile Phone"), ("device", "Tablet"),
                      ("device", "Media Player"), ("device", "Television")]


def test_individual_failure_is_one_issue_and_continues(store):
    def is_device(body):
        return breakdown_dimension(body) == "variables/mobiledevicetype"

    fake = fake_for("rs1", events(1), custom_dims(),
                    posts=[(is_device, ApiError(400, "denied", "unauthorized_dimension"))])
    rid = run_fetch(fake, store, opts())
    ind = [r for r in issues(store, rid) if r[0] == "individual"]
    assert len(ind) == 1 and ind[0][2] == "unauthorized_dimension"
    assert values_for(store, rid, "individual") == 92
    assert store.con.execute("select count(*) from breakdown_totals where run_id = ?"
                             " and kind = 'browser'", [rid]).fetchone()[0] == 5


# ---- read-only and today -----------------------------------------------------

def test_only_reports_endpoint_posted(store):
    fake = fake_for("rs1", events(3), custom_dims())
    run_fetch(fake, store, opts())
    methods = {m for m, _, _ in fake.calls}
    assert methods == {"GET", "POST"}
    assert all(p.endswith("/reports") for m, p, _ in fake.calls if m == "POST")


def test_today_uses_suite_zone_via_now(store):
    fake = fake_for("rs1", events(1), custom_dims(), tz="US/Pacific")
    now = datetime(2026, 11, 8, 3, 0, tzinfo=UTC)  # 19:00 on Nov 7 in Pacific
    rid = run_fetch(fake, store, RunOptions(rsid="rs1"), now=lambda: now)
    run = store.get_run(rid)
    assert run["today"] == TODAY
    assert run["timezone"] == "US/Pacific" and run["rsid_name"] == "Test Suite"
    assert all(date_range(b) == W.trend_range() for b in fake.trend_bodies())


def test_fetch_catalog_calls():
    fake = fake_for("rs1", events(1), custom_dims())
    catalog, suite = fetch_catalog(fake, "rs1")
    assert suite["timezoneZoneinfo"] == "US/Pacific"
    assert {c.id for c in catalog} == {"metrics/event1", "variables/prop1", "variables/page"}
    assert fake.calls == [
        ("GET", "/reportsuites/collections/suites/rs1", {"expansion": "name,timezoneZoneinfo"}),
        ("GET", "/metrics", {"rsid": "rs1", "expansion": "allowedForReporting"}),
        ("GET", "/dimensions", {"rsid": "rs1", "expansion": "allowedForReporting"}),
    ]


def test_segment_passed_everywhere(store):
    fake = fake_for("rs1", events(1), custom_dims())
    run_fetch(fake, store, opts(segment_id="s300"))
    assert all({"type": "segment", "segmentId": "s300"} in b["globalFilters"]
               for b in fake.posted())


def _probe_failure_run(store, responder):
    fake = fake_for("rs1", events(3), custom_dims(), posts=[(is_anomaly_trend, responder)])
    rid = run_fetch(fake, store, opts())
    return fake, rid


def _assert_probe_failed_and_continued(store, fake, rid):
    rows = [r for r in issues(store, rid) if r[2] == "anomaly_probe_failed"]
    assert len(rows) == 1
    scope, cid, _code, msg = rows[0]
    assert scope == "permissions" and cid is None
    assert "anomaly detection" in msg.lower() and "http" not in msg.lower()
    assert issue_codes(store, rid) == {"anomaly_probe_failed"}
    later = fake.trend_bodies()[1:]
    assert later and all(b["settings"]["includeAnomalyDetection"] is False for b in later)
    assert values_for(store, rid, "metrics/event1") == 92
    assert store.get_run(rid)["status"] == "partial"


def test_probe_server_error_is_issue_and_run_continues(store):
    fake, rid = _probe_failure_run(store, ApiError(500, "down", "server_error"))
    _assert_probe_failed_and_continued(store, fake, rid)


def test_probe_timeout_is_issue_and_run_continues(store):
    import requests

    def raise_timeout(body):
        raise requests.Timeout("read timed out")

    fake, rid = _probe_failure_run(store, raise_timeout)
    _assert_probe_failed_and_continued(store, fake, rid)
    msg, = [r[3] for r in issues(store, rid)]
    assert "Timeout" in msg


# ---- empty Individual results (spec section 7) -------------------------------

@pytest.mark.parametrize("kind, pred, phrase", [
    ("current", lambda b: not is_trend(b) and breakdown_dimension(b) is None
     and date_range(b) == W.yesterday_range(), "Top 100 values for yesterday"),
    ("historical", lambda b: not is_trend(b) and breakdown_dimension(b) is None
     and date_range(b) == W.historical_range(), "Top 100 values for the previous 90 days"),
    ("browser", lambda b: breakdown_dimension(b) == "variables/browsertype",
     "Browser table"),
    ("device", lambda b: breakdown_dimension(b) == "variables/mobiledevicetype",
     "Device table"),
])
def test_empty_individual_result_is_no_rows_issue(store, kind, pred, phrase):
    empty = {"rows": [], "totalElements": 0, "summaryData": {"totals": [0.0] * 5}}
    fake = fake_for("rs1", events(1), custom_dims(), posts=[(pred, empty)])
    rid = run_fetch(fake, store, opts())
    ind = [r for r in issues(store, rid) if r[0] == "individual"]
    assert [(r[1], r[2]) for r in ind] == [("individual", "no_rows")]
    msg = ind[0][3]
    assert msg.startswith(phrase + ": no rows came back.")
    assert "product profile may hide this dimension" in msg
    assert store.get_run(rid)["status"] == "partial"


def test_empty_individual_trend_is_no_rows_issue(store):
    @after_probe
    def is_individual_trend(body):
        return is_trend(body) and exists_dims(body) == ["variables/page"] \
            and metric_ids(body) == ["metrics/occurrences"]

    empty = {"columns": {"columnIds": ["0"], "columnErrors": []}, "rows": [],
             "totalElements": 0}
    # the catalog batch has 3 columns, so only the individual trend has 1 column here
    fake = fake_for("rs1", events(1), custom_dims(), posts=[(is_individual_trend, empty)])
    rid = run_fetch(fake, store, opts())
    ind = [r for r in issues(store, rid) if r[0] == "individual"]
    assert [(r[2], r[3].split(":")[0]) for r in ind] == [("no_rows", "Daily trend")]
    assert values_for(store, rid, "individual") == 0


def test_individual_with_rows_has_no_issue(store):
    fake = fake_for("rs1", events(1), custom_dims())
    rid = run_fetch(fake, store, opts())
    assert [r for r in issues(store, rid) if r[0] == "individual"] == []


def test_network_error_in_one_batch_continues(store):
    import requests

    @after_probe
    def timed_out_batch(body):
        return is_trend(body) and "metrics/event2" in metric_ids(body)

    def raise_timeout(body):
        raise requests.Timeout("read timed out")

    fake = fake_for("rs1", events(3), custom_dims(), posts=[(timed_out_batch, raise_timeout)])
    rid = run_fetch(fake, store, opts(batch_size=2))
    col_issues = [(r[1], r[2]) for r in issues(store, rid) if r[0] == "column"]
    assert col_issues == [("metrics/event1", "network_error"),
                          ("metrics/event2", "network_error")]
    for cid in ["metrics/event3", "variables/prop1", "variables/page", "individual"]:
        assert values_for(store, rid, cid) == 92
    assert store.get_run(rid)["status"] == "partial"


def test_network_error_in_individual_request_is_one_issue(store):
    import requests

    def is_browser(body):
        return breakdown_dimension(body) == "variables/browsertype"

    def raise_conn(body):
        raise requests.ConnectionError("connection reset")

    fake = fake_for("rs1", events(1), custom_dims(), posts=[(is_browser, raise_conn)])
    rid = run_fetch(fake, store, opts())
    ind = [r for r in issues(store, rid) if r[0] == "individual"]
    assert [(r[1], r[2]) for r in ind] == [("individual", "network_error")]
    assert values_for(store, rid, "individual") == 92
    assert store.get_run(rid)["status"] == "partial"
