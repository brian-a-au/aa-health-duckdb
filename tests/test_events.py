"""Suite-wide events: the suite_events() macro and the event column of the alerts() macro.

An event is a group of alerts of the same condition that holds at least EVENT_MIN alerts and at
least EVENT_SHARE of the components that collected data in the run. tests/test_report_js.py
checks that the page finds the same events.
"""

from datetime import date, timedelta

import pytest

from ahd.api.reports import Column, DailyValue
from ahd.catalog import Component
from ahd.store import Store
from ahd.summary import ANOMALY_METHODS, EVENT_MIN, EVENT_SHARE, SENSITIVITIES

TODAY = date(2026, 11, 7)
YESTERDAY = date(2026, 11, 6)  # a Friday
START = TODAY - timedelta(days=91)

# The value of yesterday for each kind of component. Every other day has the value 100, so the
# weekday average is 100. "high" and "low" carry an Adobe flag with the range 80 to 120.
YESTERDAY_VALUE = {
    "zero": 0.0,
    "drop": 50.0,
    "spike": 200.0,
    "high": 150.0,
    "low": 60.0,
    "quiet": 100.0,
}


def _days(start=START, end=TODAY):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def _values(cid, kind, since=YESTERDAY):
    """100 on every day, and the value of kind on each day from since to yesterday. "unused"
    is 0 on every day."""
    out = []
    for d in _days():
        if kind == "unused":
            out.append(DailyValue(cid, d, 0.0, None, None, None, None))
        elif since <= d <= YESTERDAY and kind in ("high", "low"):
            out.append(DailyValue(cid, d, YESTERDAY_VALUE[kind], 100.0, 120.0, 80.0, True))
        elif since <= d <= YESTERDAY:
            out.append(DailyValue(cid, d, YESTERDAY_VALUE[kind], None, None, None, None))
        else:
            out.append(DailyValue(cid, d, 100.0, 100.0, 120.0, 80.0, False))
    return out


def _store(tmp_path, groups, name="t"):
    """A finished run with groups of components. groups maps a kind to a count, or to a list of
    days, one for each component, on which its kind starts."""
    store = Store.open(tmp_path / f"{name}.duckdb")
    rid = store.create_run("rs1", "Suite One", None, TODAY, "UTC", "0.1")
    comps, values, n = [], [], 0
    for kind, spec in groups.items():
        starts = [YESTERDAY] * spec if isinstance(spec, int) else spec
        for since in starts:
            n += 1
            cid = f"metrics/event{n}"
            comps.append(
                Component(cid, f"{kind} {n}", "metric", "custom_events", n, Column(cid, cid), True)
            )
            values += _values(cid, kind, since)
    store.add_components(rid, comps)
    store.add_daily_values(rid, values)
    store.finish_run(rid, "ok")
    return store, rid


def _events(store, rid, **options):
    names = "".join(f", {k} := ${k}" for k in options)
    cur = store.con.execute(f"FROM suite_events($run{names})", {"run": rid, **options})
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _alert_events(store, rid, **options):
    """{component: (alert, event)} from the alerts() macro."""
    names = "".join(f", {k} := ${k}" for k in options)
    rows = store.con.execute(
        f"SELECT component_id, alert, event FROM alerts($run{names})", {"run": rid, **options}
    ).fetchall()
    return {cid: (alert, event) for cid, alert, event in rows}


def test_the_limits_are_half_and_10():
    # docs/report.md gives these numbers and their reasons, and views.sql writes them.
    assert (EVENT_SHARE, EVENT_MIN) == (0.5, 10)


@pytest.mark.parametrize("kind", ["zero", "drop", "spike", "high", "low"])
@pytest.mark.parametrize(
    ("alerts", "quiet", "is_event"),
    [
        (EVENT_MIN - 1, EVENT_MIN - 1, False),  # half, but 1 below the minimum
        (EVENT_MIN, EVENT_MIN, True),  # half, and the minimum
        (EVENT_MIN, EVENT_MIN + 1, False),  # the minimum, but just below half
        (EVENT_MIN + 1, EVENT_MIN + 1, True),  # exactly half
        (EVENT_MIN + 1, EVENT_MIN, True),  # more than half
    ],
)
def test_an_event_needs_half_of_the_components_and_at_least_10(
    tmp_path, kind, alerts, quiet, is_event
):
    store, rid = _store(tmp_path, {kind: alerts, "quiet": quiet})
    events = _events(store, rid)
    marked = _alert_events(store, rid)
    assert len(marked) == alerts
    if not is_event:
        assert events == []
        assert {e for _, e in marked.values()} == {None}
        return
    assert events == [
        {
            "run_id": rid,
            "day": YESTERDAY,
            "event": kind,
            "alerts": alerts,
            "components": alerts + quiet,
            "since": YESTERDAY,
            "at_least": False,
            "since_alerts": alerts,
            "new": alerts,
        }
    ]
    assert {e for _, e in marked.values()} == {kind}


def test_components_that_never_collected_data_do_not_count(tmp_path):
    # 10 of the 20 that collected data are 0 yesterday. The 30 that never collected data could
    # not alert, so they do not make the share smaller.
    store, rid = _store(tmp_path, {"zero": 10, "quiet": 10, "unused": 30})
    [event] = _events(store, rid)
    assert (event["event"], event["alerts"], event["components"]) == ("zero", 10, 20)


def test_a_small_suite_has_an_event_from_10_alerts(tmp_path):
    # As Adobe Store on 2026-11-11: 35 components, 15 of them collected data, 10 spikes.
    store, rid = _store(tmp_path, {"spike": 10, "quiet": 5, "unused": 20})
    [event] = _events(store, rid)
    assert (event["event"], event["alerts"], event["components"]) == ("spike", 10, 15)
    store, rid = _store(tmp_path, {"spike": 9, "quiet": 1, "unused": 25}, name="nine")
    assert _events(store, rid) == []


def test_mixed_starts_name_the_most_common_one(tmp_path):
    two, three = YESTERDAY - timedelta(days=1), YESTERDAY - timedelta(days=2)
    starts = [YESTERDAY] * 4 + [two] * 7 + [three] * 2
    store, rid = _store(tmp_path, {"zero": starts, "quiet": 5})
    [event] = _events(store, rid)
    assert event["alerts"] == 13 and event["components"] == 18
    assert (event["since"], event["at_least"], event["since_alerts"]) == (two, False, 7)
    # 4 of them started yesterday, so they are new.
    assert event["new"] == 4


def test_a_tie_of_starts_names_the_earlier_one(tmp_path):
    two = YESTERDAY - timedelta(days=1)
    store, rid = _store(tmp_path, {"zero": [YESTERDAY] * 6 + [two] * 6, "quiet": 2})
    [event] = _events(store, rid)
    assert (event["since"], event["since_alerts"], event["new"]) == (two, 6, 6)


def test_alerts_outside_an_event_keep_no_event(tmp_path):
    store, rid = _store(tmp_path, {"zero": 10, "drop": 1, "high": 1, "quiet": 8})
    marked = _alert_events(store, rid)
    outside = {cid: v for cid, v in marked.items() if v[1] is None}
    assert sorted(alert for alert, _ in outside.values()) == ["large drop", "unusual"]
    assert sum(1 for _, e in marked.values() if e == "zero") == 10


def test_a_zero_event_holds_flagged_zeros_too(tmp_path):
    # A 0 that Adobe flagged is an unusual value, and its condition is still a value of 0.
    store, rid = _store(tmp_path, {"zero": 5, "quiet": 5})
    cids = [f"metrics/flag{n}" for n in range(1, 6)]
    store.add_components(
        rid,
        [
            Component(c, f"flag {c}", "metric", "custom_events", 20 + i, Column(c, c), True)
            for i, c in enumerate(cids)
        ],
    )
    vals = []
    for c in cids:
        vals += [
            DailyValue(c, d, 0.0 if d == YESTERDAY else 100.0, 100.0, 120.0, 80.0, d == YESTERDAY)
            for d in _days()
        ]
    store.add_daily_values(rid, vals)
    marked = _alert_events(store, rid)
    assert (
        sorted(alert for alert, _ in marked.values()) == ["dropped to zero"] * 5 + ["unusual"] * 5
    )
    assert {e for _, e in marked.values()} == {"zero"}
    [event] = _events(store, rid)
    assert (event["alerts"], event["components"], event["new"]) == (10, 15, 10)


def test_two_events_on_one_day_come_by_size_then_by_name(tmp_path):
    store, rid = _store(tmp_path, {"zero": 10, "drop": 10})
    assert [(e["event"], e["alerts"]) for e in _events(store, rid)] == [("drop", 10), ("zero", 10)]


def test_no_event_leaves_every_alert_without_one(tmp_path):
    store, rid = _store(tmp_path, {"zero": 1, "drop": 1, "spike": 1, "high": 1, "quiet": 2})
    assert _events(store, rid) == []
    assert {e for _, e in _alert_events(store, rid).values()} == {None}


def test_the_drop_threshold_makes_and_ends_a_drop_event(tmp_path):
    # 6 components are 50 percent below and 6 are 20 percent below their weekday average.
    store, rid = _store(tmp_path, {"drop": 6, "quiet": 0})
    cids = [f"metrics/small{n}" for n in range(1, 7)]
    store.add_components(
        rid,
        [
            Component(c, c, "metric", "custom_events", 30 + i, Column(c, c), True)
            for i, c in enumerate(cids)
        ],
    )
    vals = []
    for c in cids:
        vals += [
            DailyValue(c, d, 80.0 if d == YESTERDAY else 100.0, None, None, None, None)
            for d in _days()
        ]
    store.add_daily_values(rid, vals)
    assert _events(store, rid) == []
    [event] = _events(store, rid, threshold=0.10)
    assert (event["event"], event["alerts"], event["components"]) == ("drop", 12, 12)
    assert _events(store, rid, threshold=0.50) == []


def test_the_spike_threshold_makes_and_ends_a_spike_event(tmp_path):
    store, rid = _store(tmp_path, {"spike": 10, "quiet": 10})
    assert [e["event"] for e in _events(store, rid)] == ["spike"]
    # A spike of exactly the threshold is not "more than" it.
    assert _events(store, rid, spike_threshold=1.0) == []


def test_an_event_on_an_earlier_day(tmp_path):
    day = YESTERDAY - timedelta(days=3)
    store, rid = _store(tmp_path, {"zero": [day] * 10, "quiet": 10})
    [event] = _events(store, rid, for_day=day)
    assert (event["day"], event["since"], event["new"]) == (day, day, 10)
    # On later days the same components are still 0, so the event goes on and is not new.
    [later] = _events(store, rid)
    assert (later["day"], later["since"], later["new"]) == (YESTERDAY, day, 0)


# Values that vary around 100 on the 12 Fridays before yesterday, and 100 on the other days.
SPREAD = [100.0, 110.0, 90.0, 100.0, 120.0, 80.0, 100.0, 105.0, 95.0, 100.0, 130.0, 70.0]


def _fallen_store(tmp_path):
    """12 components with SPREAD on the earlier Fridays and 60 yesterday, and 8 at 100."""
    store = Store.open(tmp_path / "fallen.duckdb")
    rid = store.create_run("rs1", "Suite One", None, TODAY, "UTC", "0.1")
    comps, values = [], []
    for n in range(1, 21):
        cid = f"metrics/event{n}"
        comps.append(Component(cid, f"E{n}", "metric", "custom_events", n, Column(cid, cid), True))
        fridays = iter(SPREAD)
        for d in _days():
            if n <= 12 and d == YESTERDAY:
                v = 60.0
            elif d.weekday() == 4 and d < YESTERDAY:
                v = next(fridays)
            else:
                v = 100.0
            values.append(DailyValue(cid, d, v, None, None, None, None))
    store.add_components(rid, comps)
    store.add_daily_values(rid, values)
    store.finish_run(rid, "ok")
    return store, rid


# The event of _fallen_store for each method and sensitivity. A value 40 percent down is inside
# the wider range of weekday-median and weekday-trend with low sensitivity, so it is a large drop
# there. Adobe's method has no flags in this run.
FALLEN_EVENT = {
    ("adobe", "normal"): "drop",
    ("weekday-median", "low"): "drop",
    ("weekday-median", "normal"): "low",
    ("weekday-median", "high"): "low",
    ("weekday-iqr", "low"): "low",
    ("weekday-iqr", "normal"): "low",
    ("weekday-iqr", "high"): "low",
    ("weekday-trend", "low"): "drop",
    ("weekday-trend", "normal"): "low",
    ("weekday-trend", "high"): "low",
}


def test_every_method_is_covered():
    assert {m for m, _ in FALLEN_EVENT} == set(ANOMALY_METHODS)
    assert {s for _, s in FALLEN_EVENT} == set(SENSITIVITIES)


@pytest.mark.parametrize(("method", "sensitivity"), FALLEN_EVENT)
def test_events_follow_the_anomaly_method(tmp_path, method, sensitivity):
    store, rid = _fallen_store(tmp_path)
    options = {"method": method, "sensitivity": sensitivity}
    marked = _alert_events(store, rid, **options)
    [event] = _events(store, rid, **options)
    want = FALLEN_EVENT[method, sensitivity]
    assert (event["event"], event["alerts"], event["components"]) == (want, 12, 20)
    alert = "unusual" if want == "low" else "large drop"
    assert set(marked.values()) == {(alert, want)} and len(marked) == 12


def test_the_columns_of_the_macros(tmp_path):
    store, rid = _store(tmp_path, {"zero": 10, "quiet": 10})
    cols = [d[0] for d in store.con.execute("FROM alerts(?)", [rid]).description]
    assert cols[-5:] == ["since", "days", "at_least", "is_new", "event"]
    cols = [d[0] for d in store.con.execute("FROM suite_events(?)", [rid]).description]
    assert cols == [
        "run_id",
        "day",
        "event",
        "alerts",
        "components",
        "since",
        "at_least",
        "since_alerts",
        "new",
    ]
