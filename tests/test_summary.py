import json
import math
from datetime import date, timedelta

import pytest

from ahd.api.reports import Column, DailyValue
from ahd.catalog import Component
from ahd.store import Store
from ahd.summary import build_view

TODAY = date(2026, 11, 7)
YESTERDAY = date(2026, 11, 6)  # a Friday
START = date(2026, 8, 8)  # today - 91


def _days(start, end):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def _comp(cid, name, group="custom_events", number=None):
    return Component(cid, name, "metric", group, number, Column(cid, cid, None), True)


def _dv(cid, day, value, expected=None, upper=None, lower=None, anomaly=None):
    return DailyValue(cid, day, value, expected, upper, lower, anomaly)


def _build(tmp_path, components, values, top=None, breakdowns=None, issues=None):
    store = Store.open(tmp_path / "t.duckdb")
    rid = store.create_run(
        "rs1",
        "Suite One",
        "seg1",
        TODAY,
        "US/Pacific",
        "0.1.0",
        dimension="variables/page",
        metric="metrics/event1",
    )
    store.add_components(rid, components)
    store.add_daily_values(rid, values)
    for period, rows in (top or {}).items():
        store.add_top_items(rid, period, rows)
    for kind, (labels, rows, totals) in (breakdowns or {}).items():
        store.add_breakdown(rid, kind, labels, rows, totals)
    for issue in issues or []:
        store.add_issue(rid, *issue)
    store.finish_run(rid, "partial")
    return store, rid


def _friday_series(cid):
    """Value 10 on every Friday, 1 on other days; yesterday (Friday) = 13; a today row too."""
    out = []
    for d in _days(START, TODAY):
        v = 10.0 if d.weekday() == 4 else 1.0
        if d == YESTERDAY:
            v = 13.0
        out.append(_dv(cid, d, v))
    return out


def _row(rows, cid):
    matches = [r for r in rows if r["component_id"] == cid]
    assert len(matches) == 1, (cid, rows)
    return matches[0]


@pytest.fixture
def friday(tmp_path):
    store, rid = _build(
        tmp_path, [_comp("metrics/event1", "Event 1", number=1)], _friday_series("metrics/event1")
    )
    return build_view(store, rid)


def test_same_weekday_baseline(friday):
    row = friday["views"]["day:2026-11-06"][0]
    assert row["component_id"] == "metrics/event1"
    assert row["value"] == 13
    assert row["baseline"] == 10 and row["pct"] == pytest.approx(0.3) and row["arrow"] == "up"
    assert row["zero"] is False and row["anomaly"] is None
    # a non-Friday selectable day: baseline is the average of that weekday = 1
    thu = friday["views"]["day:2026-11-05"][0]
    assert thu["value"] == 1 and thu["baseline"] == 1 and thu["pct"] == 0 and thu["arrow"] is None


def test_view_keys_are_seven_days_and_week(friday):
    expected = {f"day:{(YESTERDAY - timedelta(days=n)).isoformat()}" for n in range(7)}
    assert set(friday["views"]) == expected | {"week"}


def test_week_sum_and_baseline(friday):
    row = friday["views"]["week"][0]
    # 2026-10-31..2026-11-06: six days of 1 plus 13
    assert row["value"] == 19
    # baseline 2026-08-08..2026-10-30: 12 Fridays of 10 plus 72 days of 1 = 192, / 12
    assert row["baseline"] == 16
    # 19 / 16 - 1 = 0.1875 is under the 20 percent threshold, so no arrow
    assert row["pct"] == pytest.approx(0.1875) and row["arrow"] is None
    assert row["anomaly"] is None


def test_week_excludes_overlap(tmp_path):
    cid = "metrics/event2"
    store, rid = _build(
        tmp_path,
        [_comp(cid, "Event 2", number=2)],
        [
            _dv(cid, date(2026, 10, 30), 5.0),  # last baseline day
            _dv(cid, date(2026, 10, 31), 7.0),  # first week day
        ],
    )
    row = build_view(store, rid)["views"]["week"][0]
    assert row["value"] == 7
    assert row["baseline"] == pytest.approx(5 / 12)


def test_zero_baseline_no_pct(tmp_path):  # Review Focus 4
    cid = "metrics/event3"
    store, rid = _build(tmp_path, [_comp(cid, "New Event", number=3)], [_dv(cid, YESTERDAY, 4.0)])
    view = build_view(store, rid)
    for key in ("day:2026-11-06", "week"):
        row = view["views"][key][0]
        assert row["value"] == 4
        assert row["baseline"] == 0
        assert row["pct"] is None and row["arrow"] is None
    # a selectable day with no stored value at all
    empty = view["views"]["day:2026-11-04"][0]
    assert empty["value"] is None and empty["pct"] is None and empty["arrow"] is None
    assert empty["zero"] is False


def test_down_arrow_and_zero(tmp_path):
    cid = "metrics/event4"
    vals = [_dv(cid, d, 10.0) for d in _days(START, date(2026, 10, 30))]
    vals.append(_dv(cid, YESTERDAY, 0.0))
    store, rid = _build(tmp_path, [_comp(cid, "Dropped", number=4)], vals)
    row = build_view(store, rid)["views"]["day:2026-11-06"][0]
    assert row["value"] == 0 and row["zero"] is True
    assert row["baseline"] == 10 and row["pct"] == -1 and row["arrow"] == "down"


def test_anomaly_high_low(tmp_path):
    cid = "metrics/event5"
    store, rid = _build(
        tmp_path,
        [_comp(cid, "Event 5", number=5)],
        [
            _dv(cid, YESTERDAY, 20.0, 10.0, 15.0, 5.0, True),
            _dv(cid, date(2026, 11, 5), 1.0, 10.0, 15.0, 5.0, True),
            _dv(cid, date(2026, 11, 4), 30.0, 10.0, 15.0, 5.0, False),
        ],
    )
    view = build_view(store, rid)
    assert view["views"]["day:2026-11-06"][0]["anomaly"] == "high"
    assert view["views"]["day:2026-11-05"][0]["anomaly"] == "low"
    assert view["views"]["day:2026-11-04"][0]["anomaly"] is None
    assert view["views"]["week"][0]["anomaly"] is None


def test_alerts_zero_and_anomaly_only(tmp_path):
    comps = [
        _comp("metrics/event1", "Normal", number=1),
        _comp("metrics/event2", "Spike", number=2),
        _comp("metrics/event3", "Zero", number=3),
    ]
    vals = [
        _dv("metrics/event1", YESTERDAY, 50.0, 50.0, 60.0, 40.0, False),
        _dv("metrics/event2", YESTERDAY, 30.0, 20.0, 25.0, 15.0, True),
        _dv("metrics/event3", YESTERDAY, 0.0),
        _dv("metrics/event1", date(2026, 11, 5), 0.0),  # zero, but not yesterday
        _dv("individual", YESTERDAY, 0.0),  # never an alert
    ]
    store, rid = _build(tmp_path, comps, vals)
    alerts = build_view(store, rid)["alerts"]
    assert [a["component_id"] for a in alerts] == ["metrics/event2", "metrics/event3"]
    spike, zero = alerts
    assert spike == {
        "name": "Spike",
        "component_id": "metrics/event2",
        "expected": 20,
        "actual": 30,
        "pct_vs_expected": pytest.approx(0.5),
        "arrow": "up",
        "anomaly": "high",
        "zero": False,
    }
    assert zero["actual"] == 0 and zero["expected"] is None
    assert zero["anomaly"] is None and zero["zero"] is True
    assert zero["pct_vs_expected"] is None and zero["arrow"] is None


def test_alert_zero_expected_has_no_pct(tmp_path):
    cid = "metrics/event1"
    store, rid = _build(
        tmp_path, [_comp(cid, "E", number=1)], [_dv(cid, YESTERDAY, 0.0, 0.0, 0.0, 0.0, False)]
    )
    alert = build_view(store, rid)["alerts"][0]
    assert alert["pct_vs_expected"] is None and alert["arrow"] is None


def test_movement_index(tmp_path):
    store, rid = _build(
        tmp_path,
        [],
        [],
        top={
            "current": [(1, "home", 100.0), (2, "about", 50.0), (3, "brand new", 10.0)],
            "historical": [(2, "about", 900.0), (3, "home", 800.0), (1, "gone", 999.0)],
        },
    )
    top = build_view(store, rid)["top100"]
    assert top == [
        {"movement": 2, "rank": 1, "item": "home", "value": 100},
        {"movement": "-", "rank": 2, "item": "about", "value": 50},
        {"movement": "*", "rank": 3, "item": "brand new", "value": 10},
    ]


def test_today_row_excluded_from_series(tmp_path):
    vals = [v for v in _friday_series("metrics/event1") if v.day != YESTERDAY]
    vals += [
        _dv("metrics/event1", YESTERDAY, 13.0, 8.0, 12.0, 4.0, True),
        _dv("individual", YESTERDAY, 2.0),
        _dv("individual", TODAY, 9.0),
    ]
    store, rid = _build(tmp_path, [_comp("metrics/event1", "Event 1", number=1)], vals)
    view = build_view(store, rid)
    s = view["series"]["metrics/event1"]
    assert len(s) == 91
    assert s[0][0] == "2026-08-08" and s[-1][0] == "2026-11-06"
    assert s[-1] == ["2026-11-06", 13, 8, 12, 4, "high"]
    assert s[0] == ["2026-08-08", 1, None, None, None, None]
    assert view["series"]["individual"] == [["2026-11-06", 2, None, None, None, None]]


def test_individual_in_series_not_views_or_alerts(tmp_path):
    store, rid = _build(
        tmp_path,
        [_comp("metrics/event1", "E", number=1)],
        [_dv("metrics/event1", YESTERDAY, 1.0), _dv("individual", YESTERDAY, 0.0)],
    )
    view = build_view(store, rid)
    assert "individual" in view["series"]
    for rows in view["views"].values():
        assert [r["component_id"] for r in rows] == ["metrics/event1"]
    assert view["alerts"] == []


def test_components_without_values_left_out_and_issue_listed(tmp_path):
    comps = [
        _comp("metrics/event1", "Has data", number=1),
        _comp("metrics/event2", "No access", number=2),
    ]
    store, rid = _build(
        tmp_path,
        comps,
        [_dv("metrics/event1", YESTERDAY, 1.0)],
        issues=[
            ("column", "metrics/event2", "unauthorized_metric", "no access"),
            ("run", None, "anomaly_not_allowed", "no anomaly data"),
        ],
    )
    view = build_view(store, rid)
    for rows in view["views"].values():
        assert [r["component_id"] for r in rows] == ["metrics/event1"]
    assert view["issues"] == [
        {
            "scope": "column",
            "component_id": "metrics/event2",
            "code": "unauthorized_metric",
            "message": "no access",
        },
        {
            "scope": "run",
            "component_id": None,
            "code": "anomaly_not_allowed",
            "message": "no anomaly data",
        },
    ]


def test_row_order_group_then_number_nulls_last_then_id(tmp_path):
    comps = [
        _comp("variables/prop2", "Prop 2", "custom_props", 2),
        _comp("metrics/zzz", "Z", "custom_events", None),
        _comp("metrics/event10", "Event 10", "custom_events", 10),
        _comp("metrics/aaa", "A", "custom_events", None),
        _comp("metrics/event2", "Event 2", "custom_events", 2),
        _comp("variables/evar1", "eVar 1", "custom_evars", 1),
        _comp("metrics/pageviews", "Page Views", "Reserved Traffic Events", None),
    ]
    vals = [_dv(c.id, YESTERDAY, 0.0) for c in comps]
    store, rid = _build(tmp_path, comps, vals)
    view = build_view(store, rid)
    order = [
        "metrics/event2",
        "metrics/event10",
        "metrics/aaa",
        "metrics/zzz",
        "variables/evar1",
        "variables/prop2",
        "metrics/pageviews",
    ]
    assert [r["component_id"] for r in view["views"]["week"]] == order
    assert [r["component_id"] for r in view["views"]["day:2026-11-06"]] == order
    assert [a["component_id"] for a in view["alerts"]] == order
    row = _row(view["views"]["week"], "variables/prop2")
    assert row["name"] == "Prop 2" and row["group"] == "custom_props" and row["number"] == 2


def test_run_block(friday):
    run = friday["run"]
    assert set(run) == {
        "id",
        "rsid",
        "rsid_name",
        "segment_id",
        "today",
        "timezone",
        "status",
        "started_at",
        "finished_at",
        "tool_version",
        "dimension",
        "metric",
        "dimension_name",
        "metric_name",
    }
    assert run["dimension"] == "variables/page" and run["metric"] == "metrics/event1"
    # Page is not in this run's components, so its standard name is used; Event 1 is.
    assert run["dimension_name"] == "Page" and run["metric_name"] == "Event 1"
    assert run["rsid"] == "rs1" and run["rsid_name"] == "Suite One"
    assert run["segment_id"] == "seg1" and run["today"] == "2026-11-07"
    assert run["timezone"] == "US/Pacific" and run["status"] == "partial"
    assert run["tool_version"] == "0.1.0" and isinstance(run["id"], int)
    assert isinstance(run["started_at"], str) and isinstance(run["finished_at"], str)


def test_breakdowns(tmp_path):
    store, rid = _build(
        tmp_path,
        [],
        [],
        breakdowns={
            "browser": (
                ["Visits", "Page Views"],
                [("Chrome", [5.0, 9.0]), ("Safari", [2.0, 3.0])],
                [7.0, 12.0],
            ),
        },
    )
    view = build_view(store, rid)
    assert view["browser"] == {
        "labels": ["Visits", "Page Views"],
        "rows": [
            {"rank": 1, "item": "Chrome", "values": [5, 9]},
            {"rank": 2, "item": "Safari", "values": [2, 3]},
        ],
        "totals": [7, 12],
    }
    assert view["device"] == {"labels": [], "rows": [], "totals": []}


def test_empty_run_and_json_safe(tmp_path):
    store, rid = _build(tmp_path, [], [])
    view = build_view(store, rid)
    assert view["series"] == {} and view["alerts"] == [] and view["top100"] == []
    assert all(rows == [] for rows in view["views"].values())
    json.dumps(view, allow_nan=False)


def test_full_view_json_safe_and_non_finite_cleaned(tmp_path):
    cid = "metrics/event1"
    vals = _friday_series(cid) + [_dv("metrics/event2", YESTERDAY, math.nan, math.inf)]
    store, rid = _build(
        tmp_path,
        [_comp(cid, "E", number=1), _comp("metrics/event2", "N", number=2)],
        vals,
        top={"current": [(1, "x", 1.0)]},
    )
    view = build_view(store, rid)
    text = json.dumps(view, allow_nan=False)
    assert "NaN" not in text and "Infinity" not in text


def test_unknown_run_raises(tmp_path):
    store, _ = _build(tmp_path, [], [])
    with pytest.raises(KeyError):
        build_view(store, 999)


def test_rows_carry_component_kind(tmp_path):
    comps = [
        _comp("metrics/event1", "E", number=1),
        Component(
            "variables/prop1",
            "P",
            "dimension",
            "custom_props",
            1,
            Column("variables/prop1", "metrics/occurrences", "variables/prop1"),
            True,
        ),
    ]
    store, rid = _build(tmp_path, comps, [_dv(c.id, YESTERDAY, 1.0) for c in comps])
    view = build_view(store, rid)
    for key in ("day:2026-11-06", "week"):
        kinds = {r["component_id"]: r["kind"] for r in view["views"][key]}
        assert kinds == {"metrics/event1": "metric", "variables/prop1": "dimension"}


def test_run_block_names_from_exists_dimension(tmp_path):
    prop = Component(
        "variables/prop7",
        "Search term",
        "dimension",
        "custom_props",
        7,
        Column("variables/prop7", "metrics/occurrences", "variables/prop7"),
        True,
    )
    store = Store.open(tmp_path / "t.duckdb")
    rid = store.create_run(
        "rs1",
        "S",
        None,
        TODAY,
        "UTC",
        "0.1",
        dimension="variables/prop7",
        metric="metrics/occurrences",
    )
    store.add_components(rid, [prop])
    store.finish_run(rid, "ok")
    run = build_view(store, rid)["run"]
    assert run["dimension_name"] == "Search term"
    # occurrences is the exists column's metric, not a component, so it gets its standard name
    assert run["metric_name"] == "Occurrences"


def test_run_block_without_dimension(tmp_path):
    store = Store.open(tmp_path / "t.duckdb")
    rid = store.create_run("rs1", "S", None, TODAY, "UTC", "0.1")
    store.finish_run(rid, "ok")
    run = build_view(store, rid)["run"]
    assert run["dimension"] is None and run["dimension_name"] is None
    assert run["metric"] is None and run["metric_name"] is None


def test_alert_below_range_is_low(tmp_path):
    cid = "metrics/event1"
    store, rid = _build(
        tmp_path, [_comp(cid, "Dip", number=1)], [_dv(cid, YESTERDAY, 5.0, 20.0, 25.0, 15.0, True)]
    )
    alert = build_view(store, rid)["alerts"][0]
    assert alert["anomaly"] == "low" and alert["zero"] is False


def test_standard_metric_gets_plain_name(tmp_path):
    from ahd.summary import _display_name

    store, rid = _build(tmp_path, [], [])
    assert _display_name(store, rid, "metrics/occurrences", False) == "Occurrences"
    assert _display_name(store, rid, "metrics/pageviews", False) == "Page Views"
    assert _display_name(store, rid, "metrics/unknownthing", False) == "metrics/unknownthing"


def _drops_store(tmp_path, before, now):
    from datetime import date

    from ahd.api.reports import Column
    from ahd.catalog import Component

    def comps(counts):
        out = []
        for group, n in counts.items():
            for i in range(n):
                cid = f"{group}/{i}"
                out.append(Component(cid, cid, "metric", group, i, Column(cid, "m", None), True))
        return out

    s = Store.open(tmp_path / "d.duckdb")
    ids = []
    for counts in (before, now):
        rid = s.create_run("rs1", "N", None, date(2026, 11, 7), "UTC", "0.1")
        s.add_components(rid, comps(counts))
        s.finish_run(rid, "ok")
        ids.append(rid)
    return s, ids


def test_component_drops_over_threshold(tmp_path):
    from ahd.summary import component_drops

    s, (a, b) = _drops_store(
        tmp_path,
        {"custom_events": 100, "custom_evars": 10, "custom_props": 5},
        {"custom_events": 80, "custom_evars": 7, "custom_props": 0, "Reserved eVars": 3},
    )
    drops = component_drops(s, b)
    # 100 -> 80 is exactly 20 percent, so no warning. 10 -> 7 and 5 -> 0 are more.
    assert [d["group"] for d in drops] == ["custom_evars", "custom_props"]
    assert drops[0] == {
        "group": "custom_evars",
        "previous_run": a,
        "before": 10,
        "now": 7,
        "message": f"This run sees 7 eVars. Run {a} for the same report suite saw 10.",
    }
    assert component_drops(s, a) == []


def test_component_drops_ignores_growth_and_empty_before(tmp_path):
    from ahd.summary import component_drops

    s, (_, b) = _drops_store(
        tmp_path, {"custom_events": 2}, {"custom_events": 9, "custom_props": 1}
    )
    assert component_drops(s, b) == []
