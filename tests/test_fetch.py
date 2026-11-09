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
from ahd.config import ConfigError
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
    return [
        {"id": "variables/prop1", "name": "Prop 1", "allowedForReporting": True},
        {"id": "variables/page", "name": "Page", "allowedForReporting": True},
    ]


@pytest.fixture
def store(tmp_path):
    return Store.open(tmp_path / "t.duckdb")


def opts(**kw):
    kw.setdefault("today", TODAY)
    return RunOptions(rsid="rs1", **kw)


def issues(store, run_id):
    return store.con.execute(
        "select scope, component_id, code, message from issues where run_id = ?"
        " order by scope, component_id",
        [run_id],
    ).fetchall()


def issue_codes(store, run_id):
    return {r[2] for r in issues(store, run_id)}


def values_for(store, run_id, cid):
    return store.con.execute(
        "select count(*) from daily_values where run_id = ? and component_id = ?", [run_id, cid]
    ).fetchone()[0]


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
        f"metrics/event{n}" for n in range(1, 45)
    )


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
    for cid in [
        "metrics/event1",
        "metrics/event2",
        "variables/prop1",
        "variables/page",
        "individual/1",
    ]:
        assert values_for(store, rid, cid) == days
    assert issues(store, rid) == []
    assert store.get_run(rid)["status"] == "ok"
    comp_ids = {
        r[0]
        for r in store.con.execute(
            "select component_id from components where run_id = ?", [rid]
        ).fetchall()
    }
    assert "individual" not in comp_ids
    assert {"metrics/event1", "variables/prop1", "variables/page"} <= comp_ids


# ---- anomaly probe (Review Focus 3) ------------------------------------------


def test_anomaly_not_allowed_continues(store):
    denied = ApiError(403, "anomaly detection not allowed", "invalid_access")
    fake = fake_for("rs1", events(3), custom_dims(), posts=[(is_anomaly_trend, denied)])
    run_id = run_fetch(fake, store, opts())
    assert issue_codes(store, run_id) == {"anomaly_not_allowed"}
    ((scope, _cid, _code, msg),) = issues(store, run_id)
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
        "select count(*) from daily_values where run_id = ? and value = 0", [rid]
    ).fetchone()[0]
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

    fake = fake_for(
        "rs1",
        events(3),
        custom_dims(),
        posts=[(failing_batch, ApiError(400, "bad request", "invalid_request"))],
    )
    rid = run_fetch(fake, store, opts(batch_size=2))
    col_issues = [(r[1], r[2]) for r in issues(store, rid) if r[0] == "column"]
    # catalog order: event1, event2, event3, prop1, page -> batch [event1, event2] fails
    assert col_issues == [
        ("metrics/event1", "invalid_request"),
        ("metrics/event2", "invalid_request"),
    ]
    assert values_for(store, rid, "metrics/event3") == 92
    assert values_for(store, rid, "individual/1") == 92
    assert store.get_run(rid)["status"] == "partial"


def test_failed_when_no_values(store):
    fake = fake_for(
        "rs1",
        events(2),
        custom_dims(),
        posts=[(after_probe(is_trend), ApiError(400, "nope", "invalid_request"))],
    )
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
    assert sorted(date_range(b) for b in top) == sorted([W.yesterday_range(), W.historical_range()])
    assert all(b["dimension"] == "variables/page" and b["settings"]["limit"] == 100 for b in top)
    bd = {breakdown_dimension(b): b for b in ranked if breakdown_dimension(b)}
    ids = {k: [f["itemId"] for f in b["metricContainer"]["metricFilters"]] for k, b in bd.items()}
    assert ids == {
        "variables/browsertype": ["8", "2", "6", "7", "1"],
        "variables/mobiledevicetype": ["0", "2163986270", "1728229488", "3464263970", "2355485956"],
    }
    assert all(date_range(b) == W.yesterday_range() for b in bd.values())
    # catalog is 3 columns in one batch, so the only later 1-column trend is the individual
    (ind,) = [b for b in fake.trend_bodies()[1:] if len(metric_ids(b)) == 1]
    assert metric_ids(ind) == ["metrics/occurrences"]
    assert exists_dims(ind) == ["variables/page"]

    con = store.con
    periods = con.execute(
        "select period, count(*) from top_items where run_id = ? group by period order by period",
        [rid],
    ).fetchall()
    assert periods == [("current", 2), ("historical", 2)]
    labels = con.execute(
        "select kind, label from breakdown_totals where run_id = ? order by kind, col_index", [rid]
    ).fetchall()
    assert labels == [
        ("browser", "Google"),
        ("browser", "Microsoft"),
        ("browser", "Apple"),
        ("browser", "Mozilla"),
        ("browser", "Other"),
        ("device", "Other"),
        ("device", "Mobile Phone"),
        ("device", "Tablet"),
        ("device", "Media Player"),
        ("device", "Television"),
    ]


def test_individual_failure_is_one_issue_and_continues(store):
    def is_device(body):
        return breakdown_dimension(body) == "variables/mobiledevicetype"

    fake = fake_for(
        "rs1",
        events(1),
        custom_dims(),
        posts=[(is_device, ApiError(400, "denied", "unauthorized_dimension"))],
    )
    rid = run_fetch(fake, store, opts())
    ind = [r for r in issues(store, rid) if r[0] == "individual"]
    assert len(ind) == 1 and ind[0][2] == "unauthorized_dimension"
    assert values_for(store, rid, "individual/1") == 92
    assert (
        store.con.execute(
            "select count(*) from breakdown_totals where run_id = ? and kind = 'browser'", [rid]
        ).fetchone()[0]
        == 5
    )


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
    [pair] = store.list_pairs(rid)
    assert (pair["pair"], pair["dimension"], pair["metric"]) == (
        1,
        "variables/page",
        "metrics/occurrences",
    )
    assert (pair["dimension_name"], pair["metric_name"]) == ("Page", "Occurrences")
    assert run["today"] == TODAY
    assert run["timezone"] == "US/Pacific" and run["rsid_name"] == "Test Suite"
    assert all(date_range(b) == W.trend_range() for b in fake.trend_bodies())


def test_a_date_after_the_suites_today_is_refused(store):
    # 19:00 on Nov 7 in Pacific. Nov 7 has not ended, and Nov 8 has not begun.
    now = datetime(2026, 11, 8, 3, 0, tzinfo=UTC)
    fake = fake_for("rs1", events(1), custom_dims(), tz="US/Pacific")
    with pytest.raises(ConfigError) as e:
        run_fetch(fake, store, opts(today=date(2026, 11, 8)), now=lambda: now)
    assert str(e.value) == (
        "The date 2026-11-08 is after today in the report suite's time zone (US/Pacific), "
        "which is 2026-11-07. Give a date up to 2026-11-07."
    )
    assert store.list_runs() == [] and fake.posted() == []
    # Today in the suite's time zone is allowed. Its last full day is yesterday.
    rid = run_fetch(fake, store, opts(today=date(2026, 11, 7)), now=lambda: now)
    assert store.get_run(rid)["today"] == date(2026, 11, 7)


def test_fetch_catalog_calls():
    fake = fake_for("rs1", events(1), custom_dims())
    catalog, suite = fetch_catalog(fake, "rs1")
    assert suite["timezoneZoneinfo"] == "US/Pacific"
    assert {c.id for c in catalog} == {"metrics/event1", "variables/prop1", "variables/page"}
    assert fake.calls == [
        (
            "GET",
            "/reportsuites/collections/suites/rs1",
            {"expansion": "name,timezoneZoneinfo,currency"},
        ),
        ("GET", "/metrics", {"rsid": "rs1", "expansion": "allowedForReporting"}),
        ("GET", "/dimensions", {"rsid": "rs1", "expansion": "allowedForReporting"}),
    ]


def test_segment_passed_everywhere(store):
    fake = fake_for("rs1", events(1), custom_dims())
    run_fetch(fake, store, opts(segment_id="s300"))
    assert all(
        {"type": "segment", "segmentId": "s300"} in b["globalFilters"] for b in fake.posted()
    )


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
    (msg,) = [r[3] for r in issues(store, rid)]
    assert "Timeout" in msg


# ---- empty Individual results (spec section 7) -------------------------------


@pytest.mark.parametrize(
    "kind, pred, phrase",
    [
        (
            "current",
            lambda b: (
                not is_trend(b)
                and breakdown_dimension(b) is None
                and date_range(b) == W.yesterday_range()
            ),
            "Top 100 values for yesterday",
        ),
        (
            "historical",
            lambda b: (
                not is_trend(b)
                and breakdown_dimension(b) is None
                and date_range(b) == W.historical_range()
            ),
            "Top 100 values for the previous 90 days",
        ),
        ("browser", lambda b: breakdown_dimension(b) == "variables/browsertype", "Browser table"),
        (
            "device",
            lambda b: breakdown_dimension(b) == "variables/mobiledevicetype",
            "Device table",
        ),
    ],
)
def test_empty_individual_result_is_no_rows_issue(store, kind, pred, phrase):
    empty = {"rows": [], "totalElements": 0, "summaryData": {"totals": [0.0] * 5}}
    fake = fake_for("rs1", events(1), custom_dims(), posts=[(pred, empty)])
    rid = run_fetch(fake, store, opts())
    ind = [r for r in issues(store, rid) if r[0] == "individual"]
    assert [(r[1], r[2]) for r in ind] == [("individual/1", "no_rows")]
    msg = ind[0][3]
    assert msg.startswith(phrase + " (Page by Occurrences): no rows came back.")
    assert "product profile may hide this dimension" in msg
    assert store.get_run(rid)["status"] == "partial"


def test_empty_individual_trend_is_no_rows_issue(store):
    @after_probe
    def is_individual_trend(body):
        return (
            is_trend(body)
            and exists_dims(body) == ["variables/page"]
            and metric_ids(body) == ["metrics/occurrences"]
        )

    empty = {"columns": {"columnIds": ["0"], "columnErrors": []}, "rows": [], "totalElements": 0}
    # the catalog batch has 3 columns, so only the individual trend has 1 column here
    fake = fake_for("rs1", events(1), custom_dims(), posts=[(is_individual_trend, empty)])
    rid = run_fetch(fake, store, opts())
    ind = [r for r in issues(store, rid) if r[0] == "individual"]
    assert [(r[2], r[3].split(":")[0]) for r in ind] == [
        ("no_rows", "Daily trend (Page by Occurrences)")
    ]
    assert values_for(store, rid, "individual/1") == 0


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
    assert col_issues == [("metrics/event1", "network_error"), ("metrics/event2", "network_error")]
    for cid in ["metrics/event3", "variables/prop1", "variables/page", "individual/1"]:
        assert values_for(store, rid, cid) == 92
    assert store.get_run(rid)["status"] == "partial"


def test_a_temporary_sign_in_problem_in_one_batch_continues(store):
    from ahd.api.auth import SignInUnavailable

    @after_probe
    def renewal_fails(body):
        return is_trend(body) and "metrics/event2" in metric_ids(body)

    def raise_unavailable(body):
        raise SignInUnavailable("Adobe's sign-in service is busy.")

    fake = fake_for("rs1", events(3), custom_dims(), posts=[(renewal_fails, raise_unavailable)])
    rid = run_fetch(fake, store, opts(batch_size=2))
    col_issues = [(r[1], r[2]) for r in issues(store, rid) if r[0] == "column"]
    assert col_issues == [
        ("metrics/event1", "sign_in_unavailable"),
        ("metrics/event2", "sign_in_unavailable"),
    ]
    for cid in ["metrics/event3", "variables/prop1", "variables/page", "individual/1"]:
        assert values_for(store, rid, cid) == 92
    assert store.get_run(rid)["status"] == "partial"


def test_a_temporary_sign_in_problem_in_an_individual_request_is_one_issue(store):
    from ahd.api.auth import SignInUnavailable

    def is_browser(body):
        return breakdown_dimension(body) == "variables/browsertype"

    def raise_unavailable(body):
        raise SignInUnavailable("Adobe's sign-in service is busy.")

    fake = fake_for("rs1", events(1), custom_dims(), posts=[(is_browser, raise_unavailable)])
    rid = run_fetch(fake, store, opts())
    ind = [r for r in issues(store, rid) if r[0] == "individual"]
    assert [(r[1], r[2]) for r in ind] == [("individual/1", "sign_in_unavailable")]
    assert "Browser table" in ind[0][3]
    assert store.get_run(rid)["status"] == "partial"


def test_a_temporary_sign_in_problem_in_the_probe_is_an_issue(store):
    from ahd.api.auth import SignInUnavailable

    def raise_unavailable(body):
        raise SignInUnavailable("busy")

    fake, rid = _probe_failure_run(store, raise_unavailable)
    _assert_probe_failed_and_continued(store, fake, rid)


def test_network_error_in_individual_request_is_one_issue(store):
    import requests

    def is_browser(body):
        return breakdown_dimension(body) == "variables/browsertype"

    def raise_conn(body):
        raise requests.ConnectionError("connection reset")

    fake = fake_for("rs1", events(1), custom_dims(), posts=[(is_browser, raise_conn)])
    rid = run_fetch(fake, store, opts())
    ind = [r for r in issues(store, rid) if r[0] == "individual"]
    assert [(r[1], r[2]) for r in ind] == [("individual/1", "network_error")]
    assert values_for(store, rid, "individual/1") == 92
    assert store.get_run(rid)["status"] == "partial"


@pytest.mark.parametrize(("currency", "stored"), [("EUR", "EUR"), (None, None), (5, None)])
def test_run_stores_suite_currency_and_metric_types(tmp_path, currency, stored):
    from fakes import suite as fake_suite

    metrics = [{"id": "metrics/event1", "name": "Rev", "type": "currency"}]
    fake = fake_for("rs1", metrics, custom_dims())
    fake.gets["/reportsuites/collections/suites/rs1"] = {**fake_suite(), "currency": currency}
    store = Store.open(tmp_path / "c.duckdb")
    rid = run_fetch(fake, store, RunOptions(rsid="rs1", today=date(2026, 11, 7)))
    assert store.get_run(rid)["currency"] == stored
    row = store.con.execute(
        "select metric_type from components where component_id = 'metrics/event1'"
    ).fetchone()
    assert row == ("currency",)


def test_add_pair_fetches_five_requests_for_the_same_run(store):
    from ahd.fetch import add_pair

    fake = fake_for("rs1", events(2), custom_dims())
    rid = run_fetch(fake, store, opts(segment_id="seg9"))
    before = len(fake.posted())
    pair, problems = add_pair(
        fake, store, rid, "variables/evar17", "metrics/cartadditions", "Search", "Carts", "int"
    )
    assert (pair, problems) == (2, 0)
    new = fake.posted()[before:]
    assert len(new) == 5
    assert all("variables/evar17" in str(b) and "metrics/cartadditions" in str(b) for b in new)
    # The same segment and dates as the run.
    assert all({"type": "segment", "segmentId": "seg9"} in b["globalFilters"] for b in new)
    trend = [b for b in new if is_trend(b)]
    assert len(trend) == 1 and date_range(trend[0]) == W.trend_range()
    assert is_anomaly_trend(trend[0])
    assert values_for(store, rid, "individual/2") == 92
    assert [p["pair"] for p in store.list_pairs(rid)] == [1, 2]
    assert (
        store.con.execute(
            "select count(*) from top_items where run_id = ? and pair = 2", [rid]
        ).fetchone()[0]
        > 0
    )


def test_add_pair_without_anomaly_access_skips_anomaly(store):
    from ahd.fetch import add_pair

    fake = fake_for(
        "rs1",
        events(2),
        custom_dims(),
        posts=[(is_anomaly_trend, ApiError(403, "no", "invalid_access"))],
    )
    rid = run_fetch(fake, store, opts())
    fake.posts = []
    before = len(fake.posted())
    add_pair(fake, store, rid, "variables/evar1", "metrics/orders")
    trend = [b for b in fake.posted()[before:] if is_trend(b)]
    assert len(trend) == 1 and not is_anomaly_trend(trend[0])


def test_add_pair_problems_make_an_ok_run_partial(store):
    from ahd.fetch import add_pair

    fake = fake_for("rs1", events(2), custom_dims())
    rid = run_fetch(fake, store, opts())
    assert store.get_run(rid)["status"] == "ok"
    fake.posts = [
        (lambda b: "variables/evar5" in str(b), ApiError(400, "bad", "invalid_dimension"))
    ]
    _pair, problems = add_pair(
        fake, store, rid, "variables/evar5", "metrics/orders", "Five", "Orders"
    )
    assert problems == 5 and store.get_run(rid)["status"] == "partial"
    ind = [r for r in issues(store, rid) if r[1] == "individual/2"]
    assert len(ind) == 5 and all("(Five by Orders)" in r[3] for r in ind)


def test_add_pair_with_data_makes_a_failed_run_partial(store):
    from ahd.fetch import add_pair

    rid = run_fetch(
        fake_for("rs1", events(1), custom_dims(), default_post=ApiError(500, "down")),
        store,
        opts(),
    )
    assert store.get_run(rid)["status"] == "failed"
    assert add_pair(
        fake_for("rs1", events(1), custom_dims()), store, rid, "variables/evar1", "metrics/orders"
    ) == (2, 0)
    assert store.get_run(rid)["status"] == "partial"


def test_add_pair_with_only_column_errors_keeps_a_failed_run_failed(store):
    from fakes import trend_response

    from ahd.fetch import add_pair

    down = fake_for("rs1", events(1), custom_dims(), default_post=ApiError(500, "down"))
    rid = run_fetch(down, store, opts())
    # Only the trend answers, and its one column has an error.
    down.posts = [(is_trend, lambda b: trend_response(b, column_errors={0: "unauthorized"}))]
    _pair, problems = add_pair(down, store, rid, "variables/evar1", "metrics/orders")
    assert problems == 5 and store.get_run(rid)["status"] == "failed"


def test_add_pair_without_data_keeps_a_failed_run_failed(store):
    from ahd.fetch import add_pair

    down = fake_for("rs1", events(1), custom_dims(), default_post=ApiError(500, "down"))
    rid = run_fetch(down, store, opts())
    _pair, problems = add_pair(down, store, rid, "variables/evar1", "metrics/orders")
    assert problems == 5 and store.get_run(rid)["status"] == "failed"


def test_add_pair_can_try_again_a_pair_that_stored_nothing(store):
    # A run stores its first pair before any data comes back. When the run fails, the same
    # dimension and metric can be tried again, and the empty pair makes way for the new one.
    from ahd.fetch import add_pair

    down = fake_for("rs1", events(1), custom_dims(), default_post=ApiError(500, "down"))
    rid = run_fetch(down, store, opts())
    assert store.get_run(rid)["status"] == "failed"
    assert any(r[1] == "individual/1" for r in issues(store, rid))
    fake = fake_for("rs1", events(1), custom_dims())
    assert add_pair(
        fake, store, rid, "variables/page", "metrics/occurrences", "Page", "Occurrences"
    ) == (2, 0)
    pairs = [(p["pair"], p["dimension"], p["metric"]) for p in store.list_pairs(rid)]
    assert pairs == [(2, "variables/page", "metrics/occurrences")]
    assert not any(r[1] == "individual/1" for r in issues(store, rid))
    assert values_for(store, rid, "individual/2") == 92
    assert store.get_run(rid)["status"] == "partial"
    # Now the pair holds data, so it is refused like any other pair that is there.
    with pytest.raises(ConfigError, match="already holds Page by Occurrences"):
        add_pair(fake, store, rid, "variables/page", "metrics/occurrences")


def test_add_pair_keeps_the_empty_pair_when_the_new_try_stops(store):
    from ahd.api.auth import SignInError
    from ahd.fetch import add_pair

    down = fake_for("rs1", events(1), custom_dims(), default_post=ApiError(500, "down"))
    rid = run_fetch(down, store, opts())

    def stop(body):
        raise SignInError("Sign-in failed: the client secret is wrong.")

    with pytest.raises(SignInError):
        add_pair(
            fake_for("rs1", events(1), custom_dims(), default_post=stop),
            store,
            rid,
            "variables/page",
            "metrics/occurrences",
        )
    assert [p["pair"] for p in store.list_pairs(rid)] == [1]
    assert any(r[1] == "individual/1" for r in issues(store, rid))


def test_add_pair_refuses_duplicates_and_running_runs(store):
    from ahd.config import ConfigError
    from ahd.fetch import add_pair

    fake = fake_for("rs1", events(1), custom_dims())
    rid = run_fetch(fake, store, opts())
    with pytest.raises(ConfigError, match="already holds Page by Occurrences"):
        add_pair(fake, store, rid, "variables/page", "metrics/occurrences")
    store.set_status(rid, "running")
    with pytest.raises(ConfigError, match="still marked"):
        add_pair(fake, store, rid, "variables/evar1", "metrics/orders")


def test_add_pair_that_stops_part_way_can_be_tried_again(store):
    from ahd.api.auth import SignInError
    from ahd.fetch import add_pair

    fake = fake_for("rs1", events(1), custom_dims())
    rid = run_fetch(fake, store, opts())

    def fail_after_some(body):
        # The browser table fails after the top 100 tables were stored.
        if breakdown_dimension(body):
            raise SignInError("sign-in renewal failed")
        return trend_response(body) if is_trend(body) else {"rows": [{"value": "x", "data": [1]}]}

    broken = fake_for("rs1", events(1), custom_dims(), default_post=fail_after_some)
    with pytest.raises(SignInError):
        add_pair(broken, store, rid, "variables/evar1", "metrics/orders")
    assert [p["pair"] for p in store.list_pairs(rid)] == [1]
    for table in ("top_items", "breakdowns", "breakdown_totals"):
        count = store.con.execute(f"select count(*) from {table} where pair = 2").fetchone()[0]
        assert count == 0, table
    assert values_for(store, rid, "individual/2") == 0
    assert add_pair(fake, store, rid, "variables/evar1", "metrics/orders") == (2, 0)


def test_ctrl_c_drops_the_requests_that_have_not_started(store):
    import threading

    from fakes import auto_response

    # The probe and the first trend batch answer at once. Every later request waits until the
    # test lets it go, so at most one request is in flight when Ctrl-C comes.
    gate = threading.Event()
    answered = []

    def held(body):
        answered.append(1)
        if len(answered) > 2:
            gate.wait(10)
        return auto_response(body)

    fake = fake_for("rs1", events(200), custom_dims(), default_post=held)

    def stop(message):
        if message.startswith("Daily trends"):
            raise KeyboardInterrupt

    try:
        with pytest.raises(KeyboardInterrupt):
            run_fetch(fake, store, opts(workers=1, batch_size=20), progress=stop)
    finally:
        gate.set()
    # 1 probe, 11 trend batches and 5 pair requests were queued. Only the probe, the first
    # batch and at most the one request that the worker had already taken were sent.
    assert 2 <= len(fake.posted()) <= 3
    assert store.list_runs()[0]["status"] == "failed"


def test_a_batch_that_is_not_json_becomes_issues(store):
    from ahd.api.client import NOT_JSON

    fake = fake_for(
        "rs1",
        events(3),
        custom_dims(),
        posts=[(after_probe(is_trend), ApiError(200, NOT_JSON, "invalid_response"))],
    )
    rid = run_fetch(fake, store, opts())
    assert "invalid_response" in issue_codes(store, rid)


def test_a_run_records_whether_date_was_given(store):
    fake = fake_for("rs1", events(1), custom_dims())
    plain = run_fetch(fake, store, RunOptions(rsid="rs1"))
    given = run_fetch(fake, store, RunOptions(rsid="rs1", today=date(2026, 9, 16)))
    assert store.get_run(plain)["date_given"] is False
    assert store.get_run(given)["date_given"] is True
