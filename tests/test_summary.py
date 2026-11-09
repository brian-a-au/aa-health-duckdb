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
    )
    store.add_pair(rid, "variables/page", "metrics/event1")
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
    assert row["baseline"] == 10 and row["pct"] == pytest.approx(0.3)
    assert row["zero"] is False and row["anomaly"] is None
    # a non-Friday selectable day: baseline is the average of that weekday = 1
    thu = friday["views"]["day:2026-11-05"][0]
    assert thu["value"] == 1 and thu["baseline"] == 1 and thu["pct"] == 0


def test_view_keys_are_seven_days_and_week(friday):
    expected = {f"day:{(YESTERDAY - timedelta(days=n)).isoformat()}" for n in range(7)}
    assert set(friday["views"]) == expected | {"week"}


def test_week_sum_and_baseline(friday):
    row = friday["views"]["week"][0]
    # 2026-10-31..2026-11-06: six days of 1 plus 13
    assert row["value"] == 19
    # baseline 2026-08-08..2026-10-30: 12 Fridays of 10 plus 72 days of 1 = 192, / 12
    assert row["baseline"] == 16
    assert row["pct"] == pytest.approx(0.1875)
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
        assert row["pct"] is None
    # a selectable day with no stored value at all
    empty = view["views"]["day:2026-11-04"][0]
    assert empty["value"] is None and empty["pct"] is None
    assert empty["zero"] is False


def test_down_arrow_and_zero(tmp_path):
    cid = "metrics/event4"
    vals = [_dv(cid, d, 10.0) for d in _days(START, date(2026, 10, 30))]
    vals.append(_dv(cid, YESTERDAY, 0.0))
    store, rid = _build(tmp_path, [_comp(cid, "Dropped", number=4)], vals)
    row = build_view(store, rid)["views"]["day:2026-11-06"][0]
    assert row["value"] == 0 and row["zero"] is True
    assert row["baseline"] == 10 and row["pct"] == -1


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


def test_alerts_zero_and_anomaly(tmp_path):
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
        _dv("individual/1", YESTERDAY, 0.0),  # never an alert
    ]
    store, rid = _build(tmp_path, comps, vals)
    every = build_view(store, rid)["alerts"]
    # Each selectable day has its own rows: the zero on Nov 5 is an alert for that day.
    assert [(a["day"], a["component_id"]) for a in every if a["day"] != "2026-11-06"] == [
        ("2026-11-05", "metrics/event1")
    ]
    alerts = [a for a in every if a["day"] == "2026-11-06"]
    assert [a["component_id"] for a in alerts] == ["metrics/event2", "metrics/event3"]
    spike, zero = alerts
    assert spike == {
        "day": "2026-11-06",
        "name": "Spike",
        "component_id": "metrics/event2",
        "metric_type": None,
        "expected": 20,
        "actual": 30,
        "usual": 0,
        "pct_vs_expected": pytest.approx(0.5),
        "pct_vs_usual": None,
        "anomaly": "high",
        "zero": False,
        "unused": False,
        "days_in_row": 1,
        "drop_run": None,
        "streak_open": False,
    }
    assert zero["actual"] == 0 and zero["expected"] is None
    assert zero["anomaly"] is None and zero["zero"] is True and zero["unused"] is True
    assert zero["pct_vs_expected"] is None


def test_alert_zero_expected_has_no_pct(tmp_path):
    cid = "metrics/event1"
    store, rid = _build(
        tmp_path, [_comp(cid, "E", number=1)], [_dv(cid, YESTERDAY, 0.0, 0.0, 0.0, 0.0, False)]
    )
    alert = build_view(store, rid)["alerts"][0]
    assert alert["pct_vs_expected"] is None


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
    top = build_view(store, rid)["pairs"][0]["top100"]
    assert top == [
        {"movement": 2, "rank": 1, "item": "home", "value": 100},
        {"movement": "-", "rank": 2, "item": "about", "value": 50},
        {"movement": "*", "rank": 3, "item": "brand new", "value": 10},
    ]


def test_today_row_excluded_from_series(tmp_path):
    vals = [v for v in _friday_series("metrics/event1") if v.day != YESTERDAY]
    vals += [
        _dv("metrics/event1", YESTERDAY, 13.0, 8.0, 12.0, 4.0, True),
        _dv("individual/1", YESTERDAY, 2.0),
        _dv("individual/1", TODAY, 9.0),
    ]
    store, rid = _build(tmp_path, [_comp("metrics/event1", "Event 1", number=1)], vals)
    view = build_view(store, rid)
    s = view["series"]["metrics/event1"]
    assert len(s) == 91
    assert s[0][0] == "2026-08-08" and s[-1][0] == "2026-11-06"
    assert s[-1] == ["2026-11-06", 13, 8, 12, 4, "high"]
    assert s[0] == ["2026-08-08", 1, None, None, None, None]
    assert view["series"]["individual/1"] == [["2026-11-06", 2, None, None, None, None]]


def test_individual_in_series_not_views_or_alerts(tmp_path):
    store, rid = _build(
        tmp_path,
        [_comp("metrics/event1", "E", number=1)],
        [_dv("metrics/event1", YESTERDAY, 1.0), _dv("individual/1", YESTERDAY, 0.0)],
    )
    view = build_view(store, rid)
    assert "individual/1" in view["series"]
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
        "currency",
        "duration",
        "date_given",
    }
    assert run["date_given"] is False
    assert isinstance(run["duration"], str) and run["duration"].endswith(" s")
    [pair] = friday["pairs"]
    assert pair["dimension"] == "variables/page" and pair["metric"] == "metrics/event1"
    assert pair["series_id"] == "individual/1" and pair["pair"] == 1
    # No stored names: Page is not in this run's components, so its standard name is used.
    assert pair["dimension_name"] == "Page" and pair["metric_name"] == "Event 1"
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
    [pair] = view["pairs"]
    assert pair["browser"] == {
        "labels": ["Visits", "Page Views"],
        "rows": [
            {"rank": 1, "item": "Chrome", "values": [5, 9]},
            {"rank": 2, "item": "Safari", "values": [2, 3]},
        ],
        "totals": [7, 12],
    }
    assert pair["device"] == {"labels": [], "rows": [], "totals": []}


def test_empty_run_and_json_safe(tmp_path):
    store, rid = _build(tmp_path, [], [])
    view = build_view(store, rid)
    assert view["series"] == {} and view["alerts"] == [] and view["pairs"][0]["top100"] == []
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
    )
    store.add_pair(rid, "variables/prop7", "metrics/occurrences")
    store.add_components(rid, [prop])
    store.finish_run(rid, "ok")
    [pair] = build_view(store, rid)["pairs"]
    assert pair["dimension_name"] == "Search term"
    # occurrences is the exists column's metric, not a component, so it gets its standard name
    assert pair["metric_name"] == "Occurrences"


def test_run_without_pairs(tmp_path):
    store = Store.open(tmp_path / "t.duckdb")
    rid = store.create_run("rs1", "S", None, TODAY, "UTC", "0.1")
    store.finish_run(rid, "ok")
    assert build_view(store, rid)["pairs"] == []


def test_each_pair_has_its_own_tables(tmp_path):
    store, rid = _build(tmp_path, [], [], top={"current": [(1, "home", 5.0)]})
    pair = store.add_pair(
        rid, "variables/evar17", "metrics/cartadditions", "Search", "Carts", "int"
    )
    store.add_top_items(rid, "current", [(1, "shoes", 7.0)], pair=pair)
    store.add_breakdown(rid, "device", ["Other"], [("shoes", [7.0])], [7.0], pair=pair)
    first, second = build_view(store, rid)["pairs"]
    assert [r["item"] for r in first["top100"]] == ["home"]
    assert first["device"]["rows"] == []
    assert second["pair"] == 2 and second["series_id"] == "individual/2"
    assert (second["dimension_name"], second["metric_name"]) == ("Search", "Carts")
    assert [r["item"] for r in second["top100"]] == ["shoes"]
    assert second["device"]["rows"] == [{"rank": 1, "item": "shoes", "values": [7]}]


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


def _series(cid, friday_value, yesterday_value):
    """friday_value on every Friday, 0 on other days, yesterday_value yesterday."""
    out = []
    for d in _days(START, TODAY):
        v = friday_value if d.weekday() == 4 else 0.0
        out.append(_dv(cid, d, yesterday_value if d == YESTERDAY else v))
    return out


def test_unused_versus_dropped_to_zero(tmp_path):
    comps = [
        _comp("metrics/event1", "Dropped", number=1),
        _comp("metrics/event2", "Never used", number=2),
    ]
    vals = _series("metrics/event1", 5.0, 0.0) + _series("metrics/event2", 0.0, 0.0)
    # A value today does not count: today is not a full day.
    vals.append(_dv("metrics/event2", TODAY, 0.0))
    store, rid = _build(tmp_path, comps, vals)
    view = build_view(store, rid)
    alerts = {a["component_id"]: a for a in view["alerts"]}
    assert alerts["metrics/event1"]["zero"] and not alerts["metrics/event1"]["unused"]
    assert alerts["metrics/event2"]["zero"] and alerts["metrics/event2"]["unused"]
    for key in ("week", "day:2026-11-06"):
        rows = {r["component_id"]: r for r in view["views"][key]}
        assert rows["metrics/event1"]["unused"] is False
        assert rows["metrics/event2"]["unused"] is True


@pytest.mark.parametrize(
    ("friday_value", "yesterday_value", "included"),
    [
        (100.0, 85.0, True),  # 15 percent below a usual of 100
        (100.0, 95.0, False),  # 5 percent below: under the slider's lowest setting
        (100.0, 150.0, False),  # a rise is never a drop alert
        (8.0, 1.0, False),  # usual under 10: small numbers are skipped
    ],
)
def test_drop_candidates(tmp_path, friday_value, yesterday_value, included):
    cid = "metrics/event1"
    store, rid = _build(
        tmp_path, [_comp(cid, "E", number=1)], _series(cid, friday_value, yesterday_value)
    )
    alerts = [a for a in build_view(store, rid)["alerts"] if a["day"] == YESTERDAY.isoformat()]
    assert bool(alerts) is included
    if included:
        a = alerts[0]
        assert a["usual"] == pytest.approx(friday_value * 12 / 12)
        assert a["pct_vs_usual"] == pytest.approx(yesterday_value / friday_value - 1)
        assert a["zero"] is False and a["anomaly"] is None


def test_view_carries_change_alert_settings(tmp_path):
    from ahd.summary import DEFAULT_CHANGE_ALERT, MIN_USUAL

    cid = "metrics/event1"
    store, rid = _build(tmp_path, [_comp(cid, "E", number=1)], _series(cid, 1.0, 1.0))
    view = build_view(store, rid)
    assert view["change_alert"] == DEFAULT_CHANGE_ALERT == 0.30
    assert view["min_usual"] == MIN_USUAL == 10
    assert build_view(store, rid, change_alert=0.45)["change_alert"] == 0.45


@pytest.mark.parametrize(
    ("seconds", "text"),
    [(0, "0 s"), (45, "45 s"), (111, "1 min 51 s"), (3720, "1 h 2 min"), (-5, "0 s")],
)
def test_duration_text(seconds, text):
    from datetime import datetime

    from ahd.summary import duration_text

    start = datetime(2026, 11, 8, 9, 0, 0)
    assert duration_text(start, start + timedelta(seconds=seconds)) == text
    assert duration_text(start, None) is None and duration_text(None, start) is None


@pytest.mark.parametrize("threshold", [0.10, 0.30, 0.60])
def test_alerts_macro(tmp_path, threshold):
    # tests/test_report_js.py checks that the page shows the same alerts as this macro.
    comps = [_comp(f"metrics/event{i}", f"E{i}", number=i) for i in range(1, 8)]
    vals = (
        _series("metrics/event1", 100.0, 85.0)  # 15 percent drop
        + _series("metrics/event2", 100.0, 50.0)  # 50 percent drop
        + _series("metrics/event3", 100.0, 0.0)  # dropped to zero
        + _series("metrics/event4", 0.0, 0.0)  # never collected data
        + _series("metrics/event5", 8.0, 1.0)  # small usual value: skipped
        + [v for v in _series("metrics/event6", 100.0, 100.0) if v.day != YESTERDAY]
        + [_dv("metrics/event6", YESTERDAY, 140.0, 100.0, 120.0, 80.0, True)]  # Adobe flag
        # Never collected data, but Adobe flags yesterday's 0 as below its range.
        + [v for v in _series("metrics/event7", 0.0, 0.0) if v.day != YESTERDAY]
        + [_dv("metrics/event7", YESTERDAY, 0.0, 50.0, 80.0, 20.0, True)]
    )
    store, rid = _build(tmp_path, comps, vals)
    view = build_view(store, rid)
    day = YESTERDAY.isoformat()
    rows = store.con.execute(
        "select component_id, alert from alerts(?, threshold := ?)", [rid, threshold]
    ).fetchall()
    want = {"metrics/event3": "dropped to zero", "metrics/event6": "unusual"}
    if threshold < 0.15:
        want["metrics/event1"] = "large drop"
    if threshold < 0.50:
        want["metrics/event2"] = "large drop"
    assert dict(rows) == want
    day_row = next(r for r in view["views"][f"day:{day}"] if r["component_id"] == "metrics/event7")
    assert day_row["anomaly"] is None and day_row["unused"]
    other = store.con.execute(
        "select count(*) from alerts(?, for_day := ?)", [rid, "2026-11-05"]
    ).fetchone()[0]
    # On that Thursday every component is 0. Events 4 and 7 never collected data.
    assert other == 5


@pytest.mark.parametrize(
    ("friday", "yesterday", "threshold"),
    [(10.0, 7.0, 0.30), (100.0, 85.0, 0.15), (100.0, 70.0, 0.30), (100.0, 80.0, 0.20)],
)
def test_a_drop_of_exactly_the_threshold_is_not_flagged(tmp_path, friday, yesterday, threshold):
    store, rid = _build(
        tmp_path,
        [_comp("metrics/event1", "E1", number=1)],
        _series("metrics/event1", friday, yesterday),
    )
    view = build_view(store, rid, threshold)
    day = YESTERDAY.isoformat()
    row = view["views"][f"day:{day}"][0]
    assert row["pct"] == -threshold
    rows = store.con.execute("select * from alerts(?, threshold := ?)", [rid, threshold])
    assert rows.fetchall() == []


def test_nan_values_count_as_empty_for_never_collected(tmp_path):
    # NaN on some days and 0 on the rest: the page and the macro both call it never collected.
    vals = [
        _dv("metrics/event1", d, 0.0 if (d == YESTERDAY or d.day % 2) else float("nan"))
        for d in _days(START, TODAY)
    ]
    store, rid = _build(tmp_path, [_comp("metrics/event1", "E1", number=1)], vals)
    view = build_view(store, rid)
    row = view["views"][f"day:{YESTERDAY.isoformat()}"][0]
    assert row["zero"] and row["unused"]
    assert store.con.execute("select count(*) from alerts(?)", [rid]).fetchone()[0] == 0


def test_component_days_matches_the_report_view(tmp_path):
    store, rid = _build(
        tmp_path, [_comp("metrics/event1", "Event 1", number=1)], _friday_series("metrics/event1")
    )
    row = store.con.execute(
        "select value, usual, pct_vs_usual, zero, unused from component_days"
        " where run_id = ? and is_yesterday",
        [rid],
    ).fetchone()
    day = build_view(store, rid)["views"]["day:2026-11-06"][0]
    assert row[0] == day["value"] and row[1] == day["baseline"]
    assert row[2] == pytest.approx(day["pct"]) and (row[3], row[4]) == (False, False)
    # Today is not a full day, so component_days leaves it out.
    days = store.con.execute(
        "select min(day), max(day), count(*) from component_days where run_id = ?", [rid]
    ).fetchone()
    assert days == (START, YESTERDAY, 91)


def _streak_values(cid, last_days):
    """100 on every day, then the values in last_days, oldest first, ending yesterday."""
    out = [_dv(cid, d, 100.0) for d in _days(START, YESTERDAY - timedelta(days=len(last_days)))]
    for back, v in enumerate(reversed(last_days)):
        day = YESTERDAY - timedelta(days=back)
        if isinstance(v, tuple):  # (value, Adobe flag direction)
            value, flag = v
            upper, lower = (value - 1, value - 2) if flag == "high" else (value + 2, value + 1)
            out.append(_dv(cid, day, value, value, upper, lower, True))
        else:
            out.append(_dv(cid, day, v))
    return out


def test_days_in_row(tmp_path):
    cases = {
        # The last 3 days were 0, and the day before was normal.
        "metrics/event1": [100.0, 0.0, 0.0, 0.0],
        # 2 days above Adobe's range, after a day below it: only the same direction counts.
        "metrics/event2": [(5.0, "low"), (500.0, "high"), (500.0, "high")],
        # A 0 that Adobe flagged is an unusual value, so it ends a row of zeros.
        "metrics/event3": [0.0, (0.0, "low"), 0.0],
        # Drops of 50, 20 and 40 percent, oldest first, after a normal day.
        "metrics/event4": [100.0, 50.0, 80.0, 60.0],
        # 0 on all 7 days that the reader can pick: the row may be longer.
        "metrics/event5": [0.0] * 7,
        # A day without data ends the row.
        "metrics/event6": [0.0, None, 0.0],
    }
    comps = [_comp(cid, cid, number=i) for i, cid in enumerate(cases, start=1)]
    vals = []
    for cid, last in cases.items():
        vals += [v for v in _streak_values(cid, last) if v.value is not None]
    store, rid = _build(tmp_path, comps, vals)
    day = YESTERDAY.isoformat()
    got = {a["component_id"]: a for a in build_view(store, rid)["alerts"] if a["day"] == day}
    assert {cid: a["days_in_row"] for cid, a in got.items()} == {
        "metrics/event1": 3,
        "metrics/event2": 2,
        "metrics/event3": 1,
        "metrics/event4": 3,
        "metrics/event5": 7,
        "metrics/event6": 1,
    }
    assert [cid for cid, a in got.items() if a["streak_open"]] == ["metrics/event5"]
    # For a drop, the smallest drop so far on each day back, from yesterday.
    assert got["metrics/event4"]["drop_run"] == pytest.approx([-0.4, -0.2, -0.2])
    assert all(a["drop_run"] is None for cid, a in got.items() if cid != "metrics/event4")
    # The day before yesterday counts back from its own day.
    before = YESTERDAY - timedelta(days=1)
    earlier = [a for a in build_view(store, rid)["alerts"] if a["day"] == before.isoformat()]
    rows = {a["component_id"]: a for a in earlier}
    assert rows["metrics/event1"]["days_in_row"] == 2
    assert rows["metrics/event4"]["drop_run"] == pytest.approx([-0.2, -0.2])


def test_pair_weekday_average(tmp_path):
    cid = "metrics/event1"
    vals = _series(cid, 12.0, 0.0) + [
        _dv("individual/1", d, 24.0 if d.weekday() == 4 else 6.0) for d in _days(START, TODAY)
    ]
    store, rid = _build(tmp_path, [_comp(cid, "E", number=1)], vals)
    pair = build_view(store, rid)["pairs"][0]
    averages = pair["weekday_average"]
    assert len(averages) == 7 and averages[YESTERDAY.isoformat()] == pytest.approx(24.0)
    assert averages["2026-11-05"] == pytest.approx(6.0)


def test_calendar_lists_alerts_for_every_day(tmp_path):
    cid1, cid2, cid3, cid4 = (f"metrics/event{i}" for i in range(1, 5))
    day = date(2026, 10, 15)
    vals = (
        # Adobe flagged a value above its range, and on another day below it.
        [v for v in _streak_values(cid1, []) if v.day not in (day, YESTERDAY)]
        + [_dv(cid1, day, 500.0, 100.0, 130.0, 70.0, True)]
        + [_dv(cid1, YESTERDAY, 5.0, 100.0, 130.0, 70.0, True)]
        # 0 on one day, with data on the others.
        + [_dv(cid2, d, 0.0 if d == day else 100.0) for d in _days(START, YESTERDAY)]
        # 0 on every day: never collected data, so never an alert, even with an Adobe flag.
        + [_dv(cid3, d, 0.0) for d in _days(START, YESTERDAY) if d != day]
        + [_dv(cid3, day, 0.0, 50.0, 80.0, 20.0, True)]
        # A 0 that Adobe flagged counts once, as below its range.
        + [_dv(cid4, d, 100.0) for d in _days(START, YESTERDAY) if d != day]
        + [_dv(cid4, day, 0.0, 100.0, 130.0, 70.0, True)]
    )
    comps = [_comp(c, c, number=i) for i, c in enumerate((cid1, cid2, cid3, cid4), start=1)]
    store, rid = _build(tmp_path, comps, vals)
    calendar = {c["day"]: c for c in build_view(store, rid)["calendar"]}
    assert len(calendar) == 91 and min(calendar) == START.isoformat()
    assert max(calendar) == YESTERDAY.isoformat()
    assert calendar[day.isoformat()] == {
        "day": day.isoformat(),
        "high": [cid1],
        "low": [cid4],
        "zero": [cid2],
        "alerts": 3,
    }
    assert calendar[YESTERDAY.isoformat()]["low"] == [cid1]
    assert calendar["2026-10-14"]["alerts"] == 0 and calendar["2026-10-14"]["zero"] == []


def test_calendar_leaves_out_a_day_without_data(tmp_path):
    cid = "metrics/event1"
    gap = date(2026, 10, 1)
    vals = [_dv(cid, d, 100.0) for d in _days(START, YESTERDAY) if d != gap]
    store, rid = _build(tmp_path, [_comp(cid, "E", number=1)], vals)
    days = [c["day"] for c in build_view(store, rid)["calendar"]]
    assert len(days) == 90 and gap.isoformat() not in days


def _run(store, today, rsid="rs1", segment=None, last=None, status="ok", issues=0):
    """A run with 4 components. last maps a component to its value on the run's last day."""
    rid = store.create_run(rsid, "Suite", segment, today, "UTC", "0.1")
    cids = [f"metrics/event{i}" for i in range(1, 5)]
    store.add_components(rid, [_comp(c, c, number=i) for i, c in enumerate(cids, start=1)])
    yesterday = today - timedelta(days=1)
    values = []
    for c in cids:
        for d in _days(today - timedelta(days=91), yesterday):
            v = 0.0 if c == "metrics/event4" else 100.0
            if d == yesterday and c in (last or {}):
                v = last[c]
            if isinstance(v, tuple):  # (value, Adobe flag direction)
                value, flag = v
                upper, lower = (value - 1, value - 2) if flag == "high" else (value + 2, value + 1)
                values.append(_dv(c, d, value, value, upper, lower, True))
            else:
                values.append(_dv(c, d, v))
    store.add_daily_values(rid, values)
    for n in range(issues):
        store.add_issue(rid, "component", f"metrics/x{n}", "unauthorized_metric", "hidden")
    store.finish_run(rid, status)
    return rid


def _tile_counts(view):
    """What the page's tiles count on the run's last day, at the lowest slider setting."""
    yesterday = date.fromisoformat(view["run"]["today"]) - timedelta(days=1)
    rows = view["views"][f"day:{yesterday.isoformat()}"]
    return {
        "checked": len(rows),
        "high": sum(r["anomaly"] == "high" for r in rows),
        "low": sum(r["anomaly"] == "low" for r in rows),
        "zero": sum(r["zero"] and not r["unused"] and not r["anomaly"] for r in rows),
        "unused": sum(r["zero"] and r["unused"] for r in rows),
        "drops": sorted(
            r["pct"]
            for r in rows
            if not r["anomaly"]
            and r["value"] != 0
            and r["pct"] is not None
            and r["pct"] < -0.1
            and r["baseline"] >= 10
        ),
        "problems": len(view["issues"]),
    }


def test_history_lists_earlier_runs_by_the_tile_rules(tmp_path):
    store = Store.open(tmp_path / "h.duckdb")
    d = date(2026, 11, 1)
    first = _run(store, d, last={"metrics/event1": 50.0}, issues=2)
    _run(store, d, segment="seg1")  # another segment
    _run(store, d, rsid="rs2")  # another suite
    _run(store, d + timedelta(days=1), status="failed")
    second = _run(
        store,
        d + timedelta(days=2),
        status="partial",
        last={"metrics/event2": (500.0, "high"), "metrics/event3": 0.0},
    )
    third = _run(store, d + timedelta(days=4), last={"metrics/event1": (5.0, "low")})
    later = _run(store, d + timedelta(days=5))  # a later run date
    rerun = _run(store, d + timedelta(days=4))  # the same date, made after this run
    backfill = _run(store, d + timedelta(days=3))  # made later, for an earlier date
    history = build_view(store, third)["history"]
    assert [h["run_id"] for h in history] == [third, backfill, second, first]
    assert later not in [h["run_id"] for h in history]
    assert rerun not in [h["run_id"] for h in history]
    assert history[0]["day"] == "2026-11-04" and history[0]["today"] == "2026-11-05"
    assert history[2]["status"] == "partial"
    for h in history:
        want = _tile_counts(build_view(store, h["run_id"]))
        assert h["drops"] == pytest.approx(want.pop("drops")), h["run_id"]
        assert {k: h[k] for k in want} == want, h["run_id"]
    assert history[3]["drops"] == pytest.approx([-0.5]) and history[3]["problems"] == 2
    assert (history[2]["high"], history[2]["zero"], history[2]["unused"]) == (1, 1, 1)
    assert history[0]["low"] == 1


def test_history_keeps_this_run_even_when_it_failed(tmp_path):
    store = Store.open(tmp_path / "h.duckdb")
    rid = _run(store, date(2026, 11, 1), status="failed")
    history = build_view(store, rid)["history"]
    assert [h["run_id"] for h in history] == [rid] and history[0]["status"] == "failed"


def test_history_keeps_the_newest_runs(tmp_path, monkeypatch):
    from ahd import summary

    store = Store.open(tmp_path / "h.duckdb")
    runs = [_run(store, date(2026, 11, 1) + timedelta(days=n)) for n in range(4)]
    _run(store, date(2026, 11, 2))  # a second run for an earlier date: still older
    monkeypatch.setattr(
        summary, "_HISTORY_SQL", summary._HISTORY_SQL.replace("LIMIT 30", "LIMIT 2")
    )
    history = build_view(store, runs[-1])["history"]
    assert [h["run_id"] for h in history] == [runs[3], runs[2]]
