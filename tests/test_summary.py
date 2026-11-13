import json
import math
from datetime import date, datetime, timedelta

import pytest

from ahd.api.reports import Column, DailyValue
from ahd.catalog import Component
from ahd.store import Store
from ahd.summary import MIN_SAMPLE, build_view

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
        # The day before has no row, so the flag may have started earlier.
        "since_days": 1,
        "since_open": True,
        "changes": None,
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
        (100.0, 150.0, True),  # 50 percent above: a spike
        (100.0, 105.0, False),  # 5 percent above: under the spike slider's lowest setting
        (8.0, 1.0, False),  # usual under 10: small numbers are skipped
        (8.0, 30.0, False),  # the same for a spike
    ],
)
def test_change_candidates(tmp_path, friday_value, yesterday_value, included):
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
    comps = [_comp(f"metrics/event{i}", f"E{i}", number=i) for i in range(1, 11)]
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
        + _series("metrics/event8", 100.0, 160.0)  # 60 percent spike
        + _series("metrics/event9", 100.0, 130.0)  # 30 percent spike
        + _series("metrics/event10", 8.0, 30.0)  # small usual value: skipped
    )
    store, rid = _build(tmp_path, comps, vals)
    view = build_view(store, rid)
    day = YESTERDAY.isoformat()
    rows = store.con.execute(
        "select component_id, alert from alerts(?, threshold := ?)", [rid, threshold]
    ).fetchall()
    # The spike setting keeps its default of 50 percent.
    want = {
        "metrics/event3": "dropped to zero",
        "metrics/event6": "unusual",
        "metrics/event8": "large spike",
    }
    if threshold < 0.15:
        want["metrics/event1"] = "large drop"
    if threshold < 0.50:
        want["metrics/event2"] = "large drop"
    assert dict(rows) == want
    # The spike setting works the same way.
    spikes = store.con.execute(
        "select component_id from alerts(?, spike_threshold := ?) where alert = 'large spike'",
        [rid, threshold],
    ).fetchall()
    # A spike of exactly the setting is not "more than" it, as for a drop.
    assert sorted(c for (c,) in spikes) == sorted(
        (["metrics/event8"] if threshold < 0.60 else [])
        + (["metrics/event9"] if threshold < 0.30 else [])
    )
    day_row = next(r for r in view["views"][f"day:{day}"] if r["component_id"] == "metrics/event7")
    assert day_row["anomaly"] is None and day_row["unused"]
    other = store.con.execute(
        "select count(*) from alerts(?, for_day := ?)", [rid, "2026-11-05"]
    ).fetchone()[0]
    # On that Thursday every component is 0. Events 4 and 7 never collected data.
    assert other == 8


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


def test_alerts_macro_has_no_drops_or_spikes_before_the_last_7_days(tmp_path):
    # Only the 7 days the report can pick have a weekday average from the 12 weeks before them.
    # An older day would be compared with weeks that include the day itself, so the macro and
    # component_days give it no average, as the report's calendar does.
    cid = "metrics/event1"
    old = TODAY - timedelta(days=30)
    week_ago = TODAY - timedelta(days=7)
    values = [
        _dv(
            cid,
            d,
            3.0 if d in (old, week_ago) else (0.0 if d == TODAY - timedelta(days=20) else 100.0),
        )
        for d in _days(START, TODAY)
    ]
    store, rid = _build(tmp_path, [_comp(cid, "Event 1", number=1)], values)
    assert store.con.execute("FROM alerts(?, for_day := ?)", [rid, old]).fetchall() == []
    row = store.con.execute(
        "SELECT usual, pct_vs_usual FROM component_days WHERE run_id = ? AND day = ?", [rid, old]
    ).fetchone()
    assert row == (None, None)
    # A drop to zero needs no average, so it stays an alert on any day.
    zero = store.con.execute(
        "SELECT alert FROM alerts(?, for_day := ?)", [rid, TODAY - timedelta(days=20)]
    ).fetchall()
    assert zero == [("dropped to zero",)]
    # The first of the 7 days still has its average and its large drop.
    first = store.con.execute(
        "SELECT alert, usual FROM alerts(?, for_day := ?)", [rid, week_ago]
    ).fetchall()
    assert first == [("large drop", 100.0)]
    view = build_view(store, rid)
    assert _row(view["views"][f"day:{week_ago.isoformat()}"], cid)["baseline"] == 100.0


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


def _falling(cid, per_week, base=1000.0, factor=None, skip=None):
    """A value that changes by per_week each week, the same on every day of a week."""
    out = []
    for d in _days(START, YESTERDAY):
        if d == skip:
            continue
        weeks = (d - START).days / 7
        out.append(_dv(cid, d, base * (1 + per_week) ** weeks * (factor(d) if factor else 1)))
    return out


def test_slow_declines_lists_a_steady_fall(tmp_path):
    cids = [f"metrics/event{i}" for i in range(1, 8)]
    vals = (
        _falling(cids[0], -0.04)
        + _falling(cids[1], -0.06)
        # Flat, and a fall of 2 percent a week: too slow.
        + _falling(cids[2], 0.0)
        + _falling(cids[3], -0.02)
        # A fall of 4 percent a week, but the weeks jump up and down by 30 percent.
        + _falling(cids[4], -0.04, factor=lambda d: 1.3 if (YESTERDAY - d).days // 7 % 2 else 0.7)
        # A day without a value leaves out its week, so the component has 11 weeks.
        + _falling(cids[5], -0.04, skip=date(2026, 10, 1))
        # Fewer than MIN_USUAL a day in the first week.
        + _falling(cids[6], -0.04, base=5.0)
    )
    comps = [_comp(c, c, number=i) for i, c in enumerate(cids, start=1)]
    store, rid = _build(tmp_path, comps, vals)
    declines = build_view(store, rid)["declines"]
    # The steepest first.
    assert [d["component_id"] for d in declines] == [cids[1], cids[0]]
    d = declines[1]
    assert d["per_week"] == pytest.approx(-0.04, abs=1e-4)
    assert d["overall"] == pytest.approx(0.96**11 - 1, abs=1e-4)
    assert d["fit"] == 1.0
    # 12 weeks of 7 days, oldest first. The last week ends yesterday.
    assert len(d["totals"]) == 12
    assert all(a > b for a, b in zip(d["totals"], d["totals"][1:]))
    last = sum(v.value for v in _falling(cids[0], -0.04) if v.day > YESTERDAY - timedelta(days=7))
    assert d["totals"][-1] == pytest.approx(last)


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
        drops = want.pop("drops")
        assert h["drops"] == pytest.approx(drops), h["run_id"]
        assert {k: h[k] for k in want} == want, h["run_id"]
    assert history[3]["drops"] == pytest.approx([-0.5]) and history[3]["problems"] == 2
    assert (history[2]["high"], history[2]["zero"], history[2]["unused"]) == (1, 1, 1)
    assert history[0]["low"] == 1


def test_history_orders_runs_of_one_date_by_start_time_not_by_number(tmp_path):
    # After ahd import, an old run can have a higher number than a run that was made later.
    store = Store.open(tmp_path / "h.duckdb")
    d = date(2026, 11, 5)
    made_last = _run(store, d)
    imported = _run(store, d)
    same_time = _run(store, d)
    start = datetime(2026, 11, 5, 9, 0)
    for rid, at in ((made_last, start), (imported, start - timedelta(hours=1)), (same_time, start)):
        store.con.execute("UPDATE runs SET started_at = ? WHERE run_id = ?", [at, rid])

    def ids(rid):
        return [h["run_id"] for h in build_view(store, rid)["history"]]

    assert ids(made_last) == [made_last, imported]
    assert ids(imported) == [imported]
    # Runs that started at the same time go by their number.
    assert ids(same_time) == [same_time, made_last, imported]


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


# The local anomaly methods. Each rule is checked on the page data and on the alerts macro's
# helper, anomaly_days, so the two copies of the rules in summary.py and views.sql agree.
LOCAL_METHODS = ("weekday-median", "weekday-iqr", "weekday-trend")
# The sensitivities of a local method, and each local method with each of them.
LEVELS = ("low", "normal", "high")
LOCAL_LEVELS = [(m, s) for m in LOCAL_METHODS for s in LEVELS]
ALL_METHODS = ("adobe", *LOCAL_METHODS)
LABELS = {
    "weekday-median": "Weekday median",
    "weekday-iqr": "Weekday quartile range",
    "weekday-trend": "Weekday trend",
}
# The methods whose sample is the earlier values of the same weekday.
WEEKDAY_METHODS = ("weekday-median", "weekday-iqr")
# The 12 Fridays before yesterday that the report can use as the sample for yesterday.
EARLIER_FRIDAYS = [d for d in _days(START, YESTERDAY - timedelta(days=1)) if d.weekday() == 4]
MISSING = object()  # no stored row for that day


def _fridays(cid, sample, last, other=0.0):
    """sample on the 12 earlier Fridays, oldest first, last yesterday, other on other days.

    A sample entry of MISSING stores no row, and None stores a row without a value.
    """
    assert len(sample) == len(EARLIER_FRIDAYS) == 12
    on = dict(zip(EARLIER_FRIDAYS, sample, strict=True))
    out = []
    for d in _days(START, TODAY):
        v = last if d == YESTERDAY else on.get(d, other)
        if v is not MISSING:
            out.append(_dv(cid, d, v))
    return out


def _yesterday(store, rid, method, cid, sensitivity="normal"):
    """(expected, upper, lower, anomaly) of yesterday on the page and in the macro.

    It checks that both agree and returns the page's values.
    """
    view = build_view(store, rid, method=method, sensitivity=sensitivity)
    day = YESTERDAY.isoformat()
    [point] = [p for p in view["series"][cid] if p[0] == day]
    page = tuple(point[2:])
    row = store.con.execute(
        "SELECT round(expected, 4), round(upper, 4), round(lower, 4), anomaly"
        " FROM anomaly_days(?, method := ?, sensitivity := ?) WHERE component_id = ? AND day = ?",
        [rid, method, sensitivity, cid, YESTERDAY],
    ).fetchone()
    assert row == pytest.approx(page), (page, row)
    assert _row(view["views"][f"day:{day}"], cid)["anomaly"] == page[3]
    return page


S12 = [100.0, 110.0, 90.0, 100.0, 120.0, 80.0, 100.0, 105.0, 95.0, 100.0, 130.0, 70.0]


@pytest.mark.parametrize("method", WEEKDAY_METHODS)
def test_local_method_flags_a_high_and_a_low_value(tmp_path, method):
    comps = [_comp(f"metrics/event{i}", f"E{i}", number=i) for i in (1, 2, 3)]
    vals = (
        _fridays("metrics/event1", [100.0] * 12, 200.0)
        + _fridays("metrics/event2", [100.0] * 12, 50.0)
        + _fridays("metrics/event3", [100.0] * 12, 100.0)
    )
    store, rid = _build(tmp_path, comps, vals)
    assert _yesterday(store, rid, method, "metrics/event1")[3] == "high"
    assert _yesterday(store, rid, method, "metrics/event2")[3] == "low"
    assert _yesterday(store, rid, method, "metrics/event3")[3] is None
    alerts = store.con.execute(
        "SELECT component_id, alert, anomaly FROM alerts(?, method := ?)", [rid, method]
    ).fetchall()
    assert sorted(alerts) == [
        ("metrics/event1", "unusual", "high"),
        ("metrics/event2", "unusual", "low"),
    ]
    # Adobe flagged nothing, so by default both are only a large spike and a large drop.
    assert sorted(
        store.con.execute("SELECT component_id, alert FROM alerts(?)", [rid]).fetchall()
    ) == [
        ("metrics/event1", "large spike"),
        ("metrics/event2", "large drop"),
    ]


@pytest.mark.parametrize(
    ("method", "sample", "expected", "upper", "lower"),
    [
        # median 100, median distance 7.5, so 3 * 1.4826 * 7.5 = 33.3585 on each side
        ("weekday-median", S12, 100, 133.3585, 66.6415),
        # quartiles 93.75 and 106.25, range 12.5, so 1.5 * 12.5 = 18.75 past each quartile
        ("weekday-iqr", S12, 100, 125, 75),
        # No spread: the range is at least 5 percent on each side of the median.
        ("weekday-median", [100.0] * 10 + [101.0, 99.0], 100, 105, 95),
        ("weekday-iqr", [100.0] * 10 + [101.0, 99.0], 100, 105, 95),
        # A sample of 0 or more: the lower limit stops at 0.
        ("weekday-median", [10.0] * 6 + [100.0] * 6, 55, 255.151, 0),
        ("weekday-iqr", [10.0] * 6 + [100.0] * 6, 55, 235, 0),
        # A sample with negative values keeps a negative lower limit.
        ("weekday-median", [-v for v in S12], -100, -66.6415, -133.3585),
        ("weekday-iqr", [-v for v in S12], -100, -75, -125),
        ("weekday-median", [-100.0 + 50 * n for n in range(12)], 175, 842.17, -492.17),
        ("weekday-iqr", [-100.0 + 50 * n for n in range(12)], 175, 725, -375),
    ],
)
def test_local_range_matches_hand_computed_numbers(
    tmp_path, method, sample, expected, upper, lower
):
    cid = "metrics/event1"
    store, rid = _build(tmp_path, [_comp(cid, "E1", number=1)], _fridays(cid, sample, expected))
    got = _yesterday(store, rid, method, cid)
    assert got[:3] == pytest.approx((expected, upper, lower))
    assert got[3] is None


@pytest.mark.parametrize(
    ("method", "sensitivity", "sample", "expected", "upper", "lower"),
    [
        # median 100, median distance 7.5, spread 1.4826 * 7.5 = 11.1195. low uses 4 spreads,
        # normal 3 and high 2.
        ("weekday-median", "low", S12, 100, 144.478, 55.522),
        ("weekday-median", "normal", S12, 100, 133.3585, 66.6415),
        ("weekday-median", "high", S12, 100, 122.239, 77.761),
        # quartiles 93.75 and 106.25, range 12.5. low uses 2 ranges past each quartile, normal
        # 1.5 and high 1.
        ("weekday-iqr", "low", S12, 100, 131.25, 68.75),
        ("weekday-iqr", "normal", S12, 100, 125, 75),
        ("weekday-iqr", "high", S12, 100, 118.75, 81.25),
        # The guards stay the same: at least 5 percent on each side of the median ...
        ("weekday-median", "high", [100.0] * 10 + [101.0, 99.0], 100, 105, 95),
        ("weekday-iqr", "high", [100.0] * 10 + [101.0, 99.0], 100, 105, 95),
        ("weekday-median", "low", [100.0] * 10 + [101.0, 99.0], 100, 105, 95),
        # ... and the lower limit stops at 0 for a sample of 0 or more. Median 55, median
        # distance 45, so 4 spreads are 266.868 and 2 spreads are 133.434.
        ("weekday-median", "low", [10.0] * 6 + [100.0] * 6, 55, 321.868, 0),
        ("weekday-median", "high", [10.0] * 6 + [100.0] * 6, 55, 188.434, 0),
        # quartiles 10 and 100, range 90
        ("weekday-iqr", "low", [10.0] * 6 + [100.0] * 6, 55, 280, 0),
        ("weekday-iqr", "high", [10.0] * 6 + [100.0] * 6, 55, 190, 0),
        # A sample with negative values keeps a negative lower limit.
        ("weekday-median", "high", [-v for v in S12], -100, -77.761, -122.239),
        ("weekday-iqr", "low", [-v for v in S12], -100, -68.75, -131.25),
    ],
)
def test_the_sensitivity_scales_the_range(
    tmp_path, method, sensitivity, sample, expected, upper, lower
):
    cid = "metrics/event1"
    store, rid = _build(tmp_path, [_comp(cid, "E1", number=1)], _fridays(cid, sample, expected))
    got = _yesterday(store, rid, method, cid, sensitivity)
    assert got[:3] == pytest.approx((expected, upper, lower))
    assert got[3] is None


@pytest.mark.parametrize(
    ("method", "value", "flags"),
    [
        # Each value is just inside the range of one level and outside the next narrower one.
        ("weekday-median", 144.0, {"low": None, "normal": "high", "high": "high"}),
        ("weekday-median", 133.0, {"low": None, "normal": None, "high": "high"}),
        ("weekday-median", 56.0, {"low": None, "normal": "low", "high": "low"}),
        ("weekday-median", 67.0, {"low": None, "normal": None, "high": "low"}),
        ("weekday-iqr", 131.0, {"low": None, "normal": "high", "high": "high"}),
        ("weekday-iqr", 125.0, {"low": None, "normal": None, "high": "high"}),
        ("weekday-iqr", 118.75, {"low": None, "normal": None, "high": None}),
        ("weekday-iqr", 69.0, {"low": None, "normal": "low", "high": "low"}),
    ],
)
def test_a_higher_sensitivity_flags_a_smaller_change(tmp_path, method, value, flags):
    cid = "metrics/event1"
    store, rid = _build(tmp_path, [_comp(cid, "E1", number=1)], _fridays(cid, S12, value))
    got = {s: _yesterday(store, rid, method, cid, s)[3] for s in LEVELS}
    assert got == flags


@pytest.mark.parametrize("method", WEEKDAY_METHODS)
@pytest.mark.parametrize(
    ("sample", "has_range"),
    [
        # The page and the macro must agree on the sample size. A sample of 4 to 7 values
        # would find a macro that still needs fewer values than MIN_SAMPLE, or more.
        ([MISSING] * 8 + [100.0] * 4, False),  # 4 earlier values
        ([MISSING] * 5 + [100.0] * 7, False),  # 7 earlier values
        ([MISSING] * 4 + [100.0] * 8, True),  # 8 earlier values
        # NULL, NaN and Infinity are left out of the sample.
        ([None, math.nan, math.inf, -math.inf, None] + [100.0] * 7, False),
        ([None, math.nan, math.inf, -math.inf] + [100.0] * 8, True),
    ],
)
def test_a_range_needs_8_earlier_values(tmp_path, method, sample, has_range):
    assert MIN_SAMPLE == 8
    cid = "metrics/event1"
    store, rid = _build(tmp_path, [_comp(cid, "E1", number=1)], _fridays(cid, sample, 1000.0))
    got = _yesterday(store, rid, method, cid)
    if has_range:
        assert got == pytest.approx((100, 105, 95, "high"))
    else:
        assert got == (None, None, None, None)


@pytest.mark.parametrize("method", WEEKDAY_METHODS)
def test_only_the_last_5_weeks_have_a_range(tmp_path, method):
    # docs/report.md says that the first 8 of the 13 weeks have no range and no flags.
    cid = "metrics/event1"
    vals = [_dv(cid, d, 100.0) for d in _days(START, YESTERDAY)]
    store, rid = _build(tmp_path, [_comp(cid, "E1", number=1)], vals)
    series = build_view(store, rid, method=method)["series"][cid]
    assert len(series) == 91
    with_range = [p[0] for p in series if p[2] is not None]
    assert with_range == [d.isoformat() for d in _days(START + timedelta(weeks=8), YESTERDAY)]
    assert len(with_range) == 35
    macro = store.con.execute(
        "SELECT count(expected) FROM anomaly_days(?, method := ?)", [rid, method]
    ).fetchone()[0]
    assert macro == 35


@pytest.mark.parametrize("method", WEEKDAY_METHODS)
def test_a_value_that_is_not_finite_is_not_flagged(tmp_path, method):
    cid = "metrics/event1"
    store, rid = _build(tmp_path, [_comp(cid, "E1", number=1)], _fridays(cid, [100.0] * 12, None))
    assert _yesterday(store, rid, method, cid) == pytest.approx((100, 105, 95, None))


@pytest.mark.parametrize("method", WEEKDAY_METHODS)
@pytest.mark.parametrize(("usual", "flagged"), [(9.0, False), (10.0, True)])
def test_a_median_below_10_gives_no_flag(tmp_path, method, usual, flagged):
    cid = "metrics/event1"
    store, rid = _build(tmp_path, [_comp(cid, "E1", number=1)], _fridays(cid, [usual] * 12, 1000.0))
    got = _yesterday(store, rid, method, cid)
    assert got[:3] == pytest.approx((usual, usual * 1.05, usual * 0.95))
    assert got[3] == ("high" if flagged else None)


@pytest.mark.parametrize(
    ("method", "sample", "value", "flag"),
    [
        (m, [100.0] * 12, v, f)
        for m in WEEKDAY_METHODS
        for v, f in ((105.0, None), (95.0, None), (105.01, "high"), (94.99, "low"))
    ]
    + [
        # quartiles 80 and 120, range 40, so the limits are 20 and 180
        ("weekday-iqr", [80.0] * 4 + [100.0] * 4 + [120.0] * 4, v, f)
        for v, f in ((180.0, None), (20.0, None), (180.5, "high"), (19.5, "low"))
    ],
)
def test_a_value_on_a_limit_is_not_flagged(tmp_path, method, sample, value, flag):
    cid = "metrics/event1"
    store, rid = _build(tmp_path, [_comp(cid, "E1", number=1)], _fridays(cid, sample, value))
    assert _yesterday(store, rid, method, cid)[3] == flag


def _values(cid, value_of):
    """A row for each day from START to TODAY with the value value_of(day).

    MISSING stores no row, and None stores a row without a value.
    """
    return [_dv(cid, d, v) for d in _days(START, TODAY) if (v := value_of(d)) is not MISSING]


def _flat(cid, usual, last, on=None):
    """usual on every day, last yesterday, and the values of on for the days it names."""
    on = {YESTERDAY: last, **(on or {})}
    return _values(cid, lambda d: on.get(d, usual))


def _point(store, rid, method, cid, day, sensitivity="normal"):
    """(expected, upper, lower, anomaly) of one day on the page and in the macro.

    It checks that both agree and returns the page's values.
    """
    view = build_view(store, rid, method=method, sensitivity=sensitivity)
    [point] = [p for p in view["series"][cid] if p[0] == day.isoformat()]
    page = tuple(point[2:])
    row = store.con.execute(
        "SELECT round(expected, 4), round(upper, 4), round(lower, 4), anomaly"
        " FROM anomaly_days(?, method := ?, sensitivity := ?) WHERE component_id = ? AND day = ?",
        [rid, method, sensitivity, cid, day],
    ).fetchone()
    assert row == pytest.approx(page), (page, row)
    return page


@pytest.mark.parametrize("method", LOCAL_METHODS)
@pytest.mark.parametrize(
    ("value", "flag"),
    [(200.0, "high"), (50.0, "low"), (105.0, None), (95.0, None), (105.01, "high"), (94.99, "low")],
)
def test_every_local_method_flags_a_change_of_a_flat_component(tmp_path, method, value, flag):
    # 100 on every day: no spread, so the range is 5 percent on each side of 100.
    cid = "metrics/event1"
    store, rid = _build(tmp_path, [_comp(cid, "E1", number=1)], _flat(cid, 100.0, value))
    assert _yesterday(store, rid, method, cid) == pytest.approx((100, 105, 95, flag))


@pytest.mark.parametrize("method", LOCAL_METHODS)
@pytest.mark.parametrize(("usual", "flagged"), [(9.0, False), (10.0, True)])
def test_every_local_method_needs_an_expected_value_of_10(tmp_path, method, usual, flagged):
    cid = "metrics/event1"
    store, rid = _build(tmp_path, [_comp(cid, "E1", number=1)], _flat(cid, usual, 1000.0))
    got = _yesterday(store, rid, method, cid)
    assert got[:3] == pytest.approx((usual, usual * 1.05, usual * 0.95))
    assert got[3] == ("high" if flagged else None)


def test_weekday_trend_has_a_range_in_the_last_4_weeks(tmp_path):
    # docs/report.md says so: the 7 days before a day give its level, so the first week has no
    # level, and a day needs 8 earlier days of its weekday with a level.
    cid = "metrics/event1"
    vals = [_dv(cid, d, 100.0) for d in _days(START, YESTERDAY)]
    store, rid = _build(tmp_path, [_comp(cid, "E1", number=1)], vals)
    series = build_view(store, rid, method="weekday-trend")["series"][cid]
    assert len(series) == 91
    with_range = [p[0] for p in series if p[2] is not None]
    assert with_range == [d.isoformat() for d in _days(START + timedelta(weeks=9), YESTERDAY)]
    assert len(with_range) == 28
    macro = store.con.execute(
        "SELECT count(expected) FROM anomaly_days(?, method := 'weekday-trend')", [rid]
    ).fetchone()[0]
    assert macro == 28


def _trend(cid, fridays, last, other=100.0, recent=None):
    """fridays on the 12 earlier Fridays, last yesterday, other on the other days.

    recent, when given, is the value of the 6 days between the last two Fridays. The Thursday
    before each Friday makes up for the Friday before it, so the level of each Friday is other,
    or recent for yesterday: e.g., after a Friday of 70, the Thursday is 130, and the 7 days
    add up to 700.
    """
    on = dict(zip(EARLIER_FRIDAYS, fridays, strict=True))
    week = set(_days(YESTERDAY - timedelta(days=6), YESTERDAY - timedelta(days=1)))
    vals = []
    for v in _fridays(cid, fridays, last, other):
        level = recent if v.day in week and recent is not None else other
        before = on.get(v.day - timedelta(days=6))
        if v.day.weekday() == 3 and isinstance(before, float) and math.isfinite(before):
            v = _dv(cid, v.day, 2 * level - before)
        elif v.day in week and recent is not None:
            v = _dv(cid, v.day, recent)
        vals.append(v)
    return vals


@pytest.mark.parametrize(
    ("fridays", "recent", "expected", "upper", "lower"),
    [
        # The first Friday has no level, because the days before it are not in the run. Every
        # other level is 100, so the ratios are S12[1:] / 100: median 1, median distance 0.1,
        # so 3 * 1.4826 * 0.1 = 0.44478 on each side.
        (S12, None, 100, 144.478, 55.522),
        # The same ratios, and yesterday's level is 300.
        (S12, 300.0, 300, 433.434, 166.566),
        # No spread: the range is at least 5 percent on each side.
        ([100.0] * 12, None, 100, 105, 95),
        # ratios 0.4, 1.6 and 1: median 1, median distance 0.6, so 3 * 1.4826 * 0.6 = 2.66868.
        # No ratio is below 0, so the lower limit stops at 0.
        ([100.0, 40.0, 160.0] * 4, None, 100, 366.868, 0),
    ],
)
def test_weekday_trend_matches_hand_computed_numbers(
    tmp_path, fridays, recent, expected, upper, lower
):
    cid = "metrics/event1"
    vals = _trend(cid, fridays, expected, recent=recent)
    store, rid = _build(tmp_path, [_comp(cid, "E1", number=1)], vals)
    got = _yesterday(store, rid, "weekday-trend", cid)
    assert got[:3] == pytest.approx((expected, upper, lower))
    assert got[3] is None
    store.con.close()
    for value, flag in ((upper + 1, "high"), (lower - 1, "low")):
        (tmp_path / flag).mkdir()
        vals = _trend(cid, fridays, value, recent=recent)
        store, rid = _build(tmp_path / flag, [_comp(cid, "E1", number=1)], vals)
        assert _yesterday(store, rid, "weekday-trend", cid)[3] == flag
        store.con.close()


@pytest.mark.parametrize(
    ("sensitivity", "upper", "lower"),
    [
        # The ratios of test_weekday_trend_matches_hand_computed_numbers: median 1, spread
        # 1.4826 * 0.1 = 0.14826. 4 spreads are 0.59304 and 2 spreads are 0.29652.
        ("low", 159.304, 40.696),
        ("normal", 144.478, 55.522),
        ("high", 129.652, 70.348),
    ],
)
def test_weekday_trend_scales_its_range_by_the_sensitivity(tmp_path, sensitivity, upper, lower):
    cid = "metrics/event1"
    store, rid = _build(tmp_path, [_comp(cid, "E1", number=1)], _trend(cid, S12, 100.0))
    got = _yesterday(store, rid, "weekday-trend", cid, sensitivity)
    assert got == pytest.approx((100, upper, lower, None))
    store.con.close()
    for value, flag in ((upper + 1, "high"), (lower - 1, "low")):
        (tmp_path / flag).mkdir()
        vals = _trend(cid, S12, value)
        store, rid = _build(tmp_path / flag, [_comp(cid, "E1", number=1)], vals)
        assert _yesterday(store, rid, "weekday-trend", cid, sensitivity)[3] == flag
        store.con.close()


@pytest.mark.parametrize(
    ("fridays", "has_range"),
    [
        # A Friday has a ratio only when it and the Friday before it have a value.
        ([MISSING] * 4 + [100.0] * 8, False),  # 7 ratios
        ([MISSING] * 3 + [100.0] * 9, True),  # 8 ratios
        # NULL, NaN and Infinity are left out.
        ([None, math.nan, math.inf, -math.inf] + [100.0] * 8, False),
        ([None, math.nan, math.inf] + [100.0] * 9, True),
    ],
)
def test_weekday_trend_needs_8_earlier_ratios(tmp_path, fridays, has_range):
    cid = "metrics/event1"
    store, rid = _build(tmp_path, [_comp(cid, "E1", number=1)], _trend(cid, fridays, 1000.0))
    got = _yesterday(store, rid, "weekday-trend", cid)
    if has_range:
        assert got == pytest.approx((100, 105, 95, "high"))
    else:
        assert got == (None, None, None, None)


@pytest.mark.parametrize(
    ("gap", "has_range"),
    [(100.0, True), (None, False), (math.nan, False), (math.inf, False), (MISSING, False)],
)
def test_weekday_trend_needs_a_value_on_each_of_the_7_days_before(tmp_path, gap, has_range):
    cid = "metrics/event1"
    vals = _flat(cid, 100.0, 1000.0, {YESTERDAY - timedelta(days=3): gap})
    store, rid = _build(tmp_path, [_comp(cid, "E1", number=1)], vals)
    got = _yesterday(store, rid, "weekday-trend", cid)
    if has_range:
        assert got == pytest.approx((100, 105, 95, "high"))
    else:
        assert got == (None, None, None, None)


@pytest.mark.parametrize("usual", [0.0, -100.0])
def test_weekday_trend_needs_a_level_above_0(tmp_path, usual):
    # The same value on every day: every level is that value, so no day has a range.
    cid = "metrics/event1"
    store, rid = _build(tmp_path, [_comp(cid, "E1", number=1)], _flat(cid, usual, 1000.0))
    assert _yesterday(store, rid, "weekday-trend", cid) == (None, None, None, None)


def test_weekday_trend_uses_the_average_of_the_7_days_before(tmp_path):
    # 100 on every day, and 30 and 170 on two of the 7 days before yesterday. Their average is
    # still 100. The 30 lowers the level of the next days: (600 + 30) / 7 = 90 for the third day
    # after it, so its value of 100 is above the range.
    cid = "metrics/event1"
    on = {YESTERDAY - timedelta(days=5): 30.0, YESTERDAY - timedelta(days=2): 170.0}
    store, rid = _build(tmp_path, [_comp(cid, "E1", number=1)], _flat(cid, 100.0, 1000.0, on))
    assert _yesterday(store, rid, "weekday-trend", cid) == pytest.approx((100, 105, 95, "high"))
    got = _point(store, rid, "weekday-trend", cid, YESTERDAY - timedelta(days=3))
    assert got == pytest.approx((90, 94.5, 85.5, "high"))


def _growing(cid, jump=1.0):
    """2 percent more each day, weekends at 60 percent, and up to 1 percent of noise.

    jump multiplies yesterday's value.
    """
    import random

    rnd = random.Random(5)
    out = []
    for d in _days(START, YESTERDAY):
        v = 100 * 1.02 ** (d - START).days * (0.6 if d.weekday() >= 5 else 1.0)
        v *= 1 + rnd.uniform(-0.01, 0.01)
        out.append(_dv(cid, d, v * (jump if d == YESTERDAY else 1.0)))
    return out


def test_weekday_trend_follows_steady_growth(tmp_path):
    cid = "metrics/event1"
    store, rid = _build(tmp_path, [_comp(cid, "E1", number=1)], _growing(cid))
    last_week = list(_days(YESTERDAY - timedelta(days=6), YESTERDAY))
    got = [_point(store, rid, "weekday-trend", cid, d) for d in last_week]
    assert all(p[0] is not None for p in got)
    assert [p[3] for p in got] == [None] * 7
    store.con.close()
    # A sudden jump of 20 percent is flagged.
    (tmp_path / "jump").mkdir()
    store, rid = _build(tmp_path / "jump", [_comp(cid, "E1", number=1)], _growing(cid, 1.2))
    assert _yesterday(store, rid, "weekday-trend", cid)[3] == "high"


@pytest.mark.parametrize("method", ["nope", "Adobe", ""])
def test_an_unknown_method_raises(tmp_path, method):
    import duckdb

    cid = "metrics/event1"
    store, rid = _build(tmp_path, [_comp(cid, "E1", number=1)], _fridays(cid, [100.0] * 12, 1.0))
    with pytest.raises(ValueError, match="anomaly method"):
        build_view(store, rid, method=method)
    for sql in ("FROM alerts(?, method := ?)", "FROM anomaly_days(?, method := ?)"):
        with pytest.raises(duckdb.Error, match="anomaly method"):
            store.con.execute(sql, [rid, method]).fetchall()
        # The same for a run without any value.
        with pytest.raises(duckdb.Error, match="anomaly method"):
            store.con.execute(sql, [999, method]).fetchall()
    with pytest.raises(duckdb.Error, match="anomaly method"):
        store.con.execute("FROM alerts(?, method := NULL)", [rid]).fetchall()


@pytest.mark.parametrize("sensitivity", ["nope", "Normal", "", "medium"])
def test_an_unknown_sensitivity_raises(tmp_path, sensitivity):
    import duckdb

    cid = "metrics/event1"
    store, rid = _build(tmp_path, [_comp(cid, "E1", number=1)], _fridays(cid, [100.0] * 12, 1.0))
    for method in ALL_METHODS:
        with pytest.raises(ValueError, match="anomaly sensitivity"):
            build_view(store, rid, method=method, sensitivity=sensitivity)
    for sql in (
        "FROM alerts(?, method := ?, sensitivity := ?)",
        "FROM anomaly_days(?, method := ?, sensitivity := ?)",
    ):
        for method in ALL_METHODS:
            with pytest.raises(duckdb.Error, match="Use low, normal or high."):
                store.con.execute(sql, [rid, method, sensitivity]).fetchall()
            # The same for a run without any value.
            with pytest.raises(duckdb.Error, match="anomaly sensitivity"):
                store.con.execute(sql, [999, method, sensitivity]).fetchall()
    with pytest.raises(duckdb.Error, match="anomaly sensitivity"):
        store.con.execute("FROM alerts(?, sensitivity := NULL)", [rid]).fetchall()
    # An unknown method is named first.
    with pytest.raises(duckdb.Error, match="anomaly method"):
        store.con.execute(
            "FROM anomaly_days(?, method := 'x', sensitivity := 'x')", [rid]
        ).fetchall()


def test_the_sensitivity_does_not_apply_to_adobe(tmp_path):
    store, rid = _build(tmp_path, *_macro_data())
    # The page refuses it, because a report would name a sensitivity that changes nothing.
    for sensitivity in ("low", "high"):
        with pytest.raises(ValueError, match="only to a local anomaly method"):
            build_view(store, rid, sensitivity=sensitivity)
    # The macros accept it, and it changes nothing.
    every = "SELECT * FROM anomaly_days(?{}) ORDER BY ALL"
    plain = store.con.execute(every.format(""), [rid]).fetchall()
    for sensitivity in LEVELS:
        got = store.con.execute(every.format(", sensitivity := ?"), [rid, sensitivity]).fetchall()
        assert got == plain
        alerts = store.con.execute(
            "SELECT * FROM alerts(?, method := 'adobe', sensitivity := ?)", [rid, sensitivity]
        ).fetchall()
        assert alerts == store.con.execute("SELECT * FROM alerts(?)", [rid]).fetchall()


def test_only_a_local_method_has_factors():
    from ahd.summary import SENSITIVITIES, _method

    assert SENSITIVITIES == LEVELS
    with pytest.raises(ValueError, match="has no sensitivity"):
        _method("adobe").factor("normal")
    for method in LOCAL_METHODS:
        got = [f"{float(_method(method).factor(s)):g}" for s in LEVELS]
        assert got == [FACTORS[method][s] for s in LEVELS]


@pytest.mark.parametrize("method", LOCAL_METHODS)
def test_normal_is_the_default_sensitivity(tmp_path, method):
    store, rid = _build(tmp_path, *_macro_data())
    assert build_view(store, rid, method=method) == build_view(
        store, rid, method=method, sensitivity="normal"
    )
    for sql in (
        "SELECT * FROM anomaly_days(?, method := ?{}) ORDER BY ALL",
        "SELECT * FROM alerts(?, method := ?, threshold := 0.1{})",
    ):
        plain = store.con.execute(sql.format(""), [rid, method]).fetchall()
        normal = store.con.execute(
            sql.format(", sensitivity := 'normal'"), [rid, method]
        ).fetchall()
        assert plain == normal


def test_methods_have_keys_and_labels(tmp_path):
    import duckdb

    from ahd.summary import ANOMALY_LABELS, ANOMALY_METHODS

    assert ANOMALY_METHODS == ("adobe", "weekday-median", "weekday-iqr", "weekday-trend")
    assert ANOMALY_LABELS == {
        "adobe": "Adobe",
        "weekday-median": "Weekday median",
        "weekday-iqr": "Weekday quartile range",
        "weekday-trend": "Weekday trend",
    }
    store, rid = _build(tmp_path, [], [])
    for method in ANOMALY_METHODS:
        got = build_view(store, rid, method=method)["anomaly_method"]
        assert set(got) == {"key", "label", "adobe", "rule", "sample", "sensitivity", "title"}
        assert got["sensitivity"] == "normal" and got["title"] == got["label"]
        assert (got["key"], got["label"], got["adobe"]) == (
            method,
            ANOMALY_LABELS[method],
            method == "adobe",
        )
        assert got["rule"].endswith(".")
        # The page puts the sample after a number, e.g., "at least 8 earlier values".
        assert (got["sample"] is None) is (method == "adobe")
    assert build_view(store, rid)["anomaly_method"]["key"] == "adobe"
    samples = {
        m: build_view(store, rid, method=m)["anomaly_method"]["sample"] for m in LOCAL_METHODS
    }
    assert samples == {
        "weekday-median": "earlier values of its weekday",
        "weekday-iqr": "earlier values of its weekday",
        "weekday-trend": "earlier values of its weekday that have a level",
    }
    # The macro names every method when it gets an unknown one.
    with pytest.raises(duckdb.Error, match=", ".join(ANOMALY_METHODS[:-1]) + " or weekday-trend."):
        store.con.execute("FROM anomaly_days(?, method := 'nope')", [rid]).fetchall()
    # docs/report.md quotes these rules.
    assert build_view(store, rid, method="weekday-trend")["anomaly_method"]["rule"] == (
        "A value is unusual when it is far from what its recent level and its weekday suggest. "
        "The level is the average of the 7 days before it, and far means more than 3 times the "
        "usual spread of how the earlier values of the same weekday compared with their level."
    )
    assert build_view(store, rid, method="weekday-median")["anomaly_method"]["rule"] == (
        "A value is unusual when it is far from the median of the earlier values of the same "
        "weekday in this run. Far means more than 3 times the usual spread of those values."
    )
    assert build_view(store, rid, method="weekday-iqr")["anomaly_method"]["rule"] == (
        "A value is unusual when it is far outside the middle half of the earlier values of the "
        "same weekday in this run. Far means more than 1.5 times the width of the middle half "
        "past its edge."
    )


# The factor of each local method for each sensitivity, as the rule on the page states it.
FACTORS = {
    "weekday-median": {"low": "4", "normal": "3", "high": "2"},
    "weekday-iqr": {"low": "2", "normal": "1.5", "high": "1"},
    "weekday-trend": {"low": "4", "normal": "3", "high": "2"},
}


@pytest.mark.parametrize(("method", "sensitivity"), LOCAL_LEVELS)
def test_the_page_data_names_the_sensitivity(tmp_path, method, sensitivity):
    store, rid = _build(tmp_path, [], [])
    got = build_view(store, rid, method=method, sensitivity=sensitivity)["anomaly_method"]
    label = LABELS[method]
    assert got["sensitivity"] == sensitivity
    assert got["title"] == (
        label if sensitivity == "normal" else f"{label}, {sensitivity} sensitivity"
    )
    # The label stays the name of the method, because the page puts it in sentences, e.g.,
    # "Flagged by the Weekday median method".
    assert got["label"] == label
    # Only the factor differs from the rule of normal, which test_methods_have_keys_and_labels
    # checks word for word.
    normal = build_view(store, rid, method=method)["anomaly_method"]["rule"]
    was = _more_than(FACTORS[method]["normal"])
    assert normal.count(was) == 1
    assert got["rule"] == normal.replace(was, _more_than(FACTORS[method][sensitivity]))
    assert "1 times" not in got["rule"]


def _more_than(factor: str) -> str:
    """The words of a rule before the spread or the width, e.g., "more than 2 times the". A
    factor of 1 reads "more than the", e.g., "more than the width of the middle half"."""
    return "more than the" if factor == "1" else f"more than {factor} times the"


def _macro_data():
    """The components of test_alerts_macro, and two that only a local method flags."""
    comps = [_comp(f"metrics/event{i}", f"E{i}", number=i) for i in range(1, 13)]
    vals = (
        _series("metrics/event1", 100.0, 85.0)  # 15 percent drop
        + _series("metrics/event2", 100.0, 50.0)  # 50 percent drop
        + _series("metrics/event3", 100.0, 0.0)  # dropped to zero
        + _series("metrics/event4", 0.0, 0.0)  # never collected data
        + _series("metrics/event5", 8.0, 1.0)  # small usual value: skipped
        + [v for v in _series("metrics/event6", 100.0, 100.0) if v.day != YESTERDAY]
        + [_dv("metrics/event6", YESTERDAY, 140.0, 100.0, 120.0, 80.0, True)]  # Adobe flag
        + [v for v in _series("metrics/event7", 0.0, 0.0) if v.day != YESTERDAY]
        + [_dv("metrics/event7", YESTERDAY, 0.0, 50.0, 80.0, 20.0, True)]
        + _series("metrics/event8", 100.0, 160.0)  # 60 percent spike
        + _series("metrics/event9", 100.0, 130.0)  # 30 percent spike
        + _series("metrics/event10", 8.0, 30.0)  # small usual value: skipped
        + _fridays("metrics/event11", S12, 140.0, other=50.0)  # local high
        # Below the range of every local method. For weekday-trend it is 58.9 to 130.8.
        + _fridays("metrics/event12", S12, 50.0, other=50.0)  # local low
    )
    return comps, vals


def test_the_default_method_of_the_macro_is_adobe(tmp_path):
    store, rid = _build(tmp_path, *_macro_data())
    every = "SELECT * FROM alerts(?, for_day := ?{})"
    for day in _days(TODAY - timedelta(days=91), YESTERDAY):
        plain = store.con.execute(every.format(""), [rid, day]).fetchall()
        adobe = store.con.execute(every.format(", method := 'adobe'"), [rid, day]).fetchall()
        assert plain == adobe
    unusual = store.con.execute(
        "SELECT component_id FROM alerts(?) WHERE alert = 'unusual'", [rid]
    ).fetchall()
    assert unusual == [("metrics/event6",)]


@pytest.mark.parametrize("method", ALL_METHODS)
def test_anomaly_days_leaves_out_the_individual_component_summary(tmp_path, method):
    # The pairs of the Individual Component Summary have daily values, e.g., individual/1, but
    # no row in components. anomaly_days gives the components only, as component_days does.
    comps, vals = _macro_data()
    pair = [_dv("individual/1", v.day, v.value, 1.0, 2.0, 0.5, True) for v in vals[:92]]
    pair += [_dv("individual/2", v.day, v.value * 3, 1.0, 2.0, 0.5, True) for v in vals[:92]]
    store, rid = _build(tmp_path, comps, vals + pair)
    found = store.con.execute(
        "SELECT DISTINCT component_id FROM anomaly_days(?, method := ?) ORDER BY ALL",
        [rid, method],
    ).fetchall()
    assert found == sorted((c.id,) for c in comps)
    days = store.con.execute(
        "SELECT component_id, day FROM anomaly_days(?, method := ?) ORDER BY ALL", [rid, method]
    ).fetchall()
    every = store.con.execute(
        "SELECT component_id, day FROM component_days WHERE run_id = ? ORDER BY ALL", [rid]
    ).fetchall()
    assert days == every


@pytest.mark.parametrize(("method", "sensitivity"), [("adobe", "normal"), *LOCAL_LEVELS])
def test_page_alerts_match_the_alerts_macro(tmp_path, method, sensitivity):
    store, rid = _build(tmp_path, *_macro_data())
    view = build_view(store, rid, method=method, sensitivity=sensitivity)
    for day in [YESTERDAY - timedelta(days=n) for n in range(7)]:
        page = {}
        for a in view["alerts"]:
            if a["day"] != day.isoformat() or (a["zero"] and a["unused"]):
                continue
            if a["anomaly"]:
                kind = "unusual"
            elif a["zero"]:
                kind = "dropped to zero"
            else:
                kind = "large spike" if a["pct_vs_usual"] > 0 else "large drop"
            page[a["component_id"]] = (kind, a["anomaly"], a["expected"])
        rows = store.con.execute(
            "SELECT component_id, alert, anomaly, expected FROM alerts(?, threshold := 0.10,"
            " spike_threshold := 0.10, for_day := ?, method := ?, sensitivity := ?)",
            [rid, day, method, sensitivity],
        ).fetchall()
        macro = {r[0]: r[1:] for r in rows}
        assert set(macro) == set(page), day
        for cid, want in page.items():
            assert macro[cid] == pytest.approx(want), (day, cid)
    # Yesterday's flags. weekday-trend also flags event11 and event12 on the 6 days before,
    # because the low Friday before them lowers their level. See
    # test_weekday_trend_uses_the_average_of_the_7_days_before.
    flags = {
        a["component_id"]: a["anomaly"]
        for a in view["alerts"]
        if a["anomaly"] and a["day"] == YESTERDAY.isoformat()
    }
    if method == "adobe":
        assert flags == {"metrics/event6": "high"}
    else:
        # The wider range of low leaves some of them out. See
        # test_a_higher_sensitivity_narrows_every_range.
        if sensitivity != "low":
            assert flags["metrics/event11"] == "high" and flags["metrics/event12"] == "low"
        assert "metrics/event7" not in flags  # never collected data


def _random_run(tmp_path):
    """Components with random values, some not finite, and some days without a row."""
    import random

    rnd = random.Random(7)
    comps = [_comp(f"metrics/event{i}", f"E{i}", number=i) for i in range(1, 9)]
    vals = []
    for i in range(1, 9):
        for d in _days(START - timedelta(days=5), TODAY):
            pick = rnd.random()
            if pick < 0.05:
                continue
            v = rnd.choice([math.nan, math.inf, None]) if pick < 0.1 else rnd.gauss(50, 30)
            if i == 2 and v is not None:
                v = abs(v)  # a sample of 0 or more
            if i == 3:
                v = 0.0 if pick < 0.8 else 40.0  # mostly 0
            vals.append(_dv(f"metrics/event{i}", d, v))
    # Values close to 100 and sometimes far from it. weekday-trend needs a value on every day to
    # give many ranges, because the 7 days before a day must all have a value. event10 is the
    # same with one day without a value.
    for i in (9, 10):
        comps.append(_comp(f"metrics/event{i}", f"E{i}", number=i))
        for d in _days(START - timedelta(days=5), TODAY):
            v = rnd.gauss(100, 3) * (rnd.choice([0.5, 1.5]) if rnd.random() < 0.05 else 1)
            gap = i == 10 and d == START + timedelta(days=40)
            vals.append(_dv(f"metrics/event{i}", d, math.nan if gap else v))
    return _build(tmp_path, comps, vals)


@pytest.mark.parametrize(("method", "sensitivity"), LOCAL_LEVELS)
def test_page_and_macro_agree_on_every_day(tmp_path, method, sensitivity):
    store, rid = _random_run(tmp_path)
    view = build_view(store, rid, method=method, sensitivity=sensitivity)
    page = {(cid, p[0]): tuple(p[2:]) for cid, points in view["series"].items() for p in points}
    macro = {
        (cid, day.isoformat()): rest
        for cid, day, *rest in store.con.execute(
            "SELECT component_id, day, round(expected, 4), round(upper, 4), round(lower, 4),"
            " anomaly FROM anomaly_days(?, method := ?, sensitivity := ?)",
            [rid, method, sensitivity],
        ).fetchall()
    }
    assert set(page) == set(macro)
    for key, want in page.items():
        assert tuple(macro[key]) == pytest.approx(want), key
    assert any(p[3] for p in page.values()), "the data should give some flags"
    # Days before the 91 days get no range, and today is not in the series.
    assert min(day for _, day in page) == START.isoformat()


@pytest.mark.parametrize("method", LOCAL_METHODS)
def test_a_higher_sensitivity_narrows_every_range(tmp_path, method):
    store, rid = _random_run(tmp_path)
    days = {}
    for s in LEVELS:
        days[s] = {
            (cid, day): rest
            for cid, day, *rest in store.con.execute(
                "SELECT component_id, day, expected, lower, upper, anomaly"
                " FROM anomaly_days(?, method := ?, sensitivity := ?)",
                [rid, method, s],
            ).fetchall()
        }
    low, normal, high = (days[s] for s in LEVELS)
    assert set(low) == set(normal) == set(high)
    for key, (expected, lower, upper, _) in normal.items():
        # The same days have a range, with the same expected value.
        assert low[key][0] == expected == high[key][0]
        if expected is None:
            continue
        assert low[key][1] <= lower <= high[key][1]
        assert low[key][2] >= upper >= high[key][2]
    flagged = {s: {k for k, v in days[s].items() if v[3]} for s in LEVELS}
    assert flagged["low"] < flagged["normal"] < flagged["high"]


@pytest.mark.parametrize(("method", "sensitivity"), LOCAL_LEVELS)
def test_a_local_method_reaches_every_part_of_the_page(tmp_path, method, sensitivity):
    # A run that may not use Adobe's anomaly detection has no flags from Adobe.
    store = Store.open(tmp_path / "h.duckdb")
    before = _run(store, date(2026, 11, 5))
    rid = _run(store, date(2026, 11, 6), last={"metrics/event2": 500.0})
    store.con.execute(
        "INSERT INTO issues VALUES (?, 'run', NULL, 'anomaly_not_allowed', 'not allowed')", [rid]
    )
    day = "2026-11-05"
    adobe = build_view(store, rid)
    view = build_view(store, rid, method=method, sensitivity=sensitivity)
    assert _row(adobe["views"][f"day:{day}"], "metrics/event2")["anomaly"] is None
    assert _row(view["views"][f"day:{day}"], "metrics/event2")["anomaly"] == "high"
    assert [a["anomaly"] for a in view["alerts"] if a["component_id"] == "metrics/event2"] == [
        "high"
    ]
    point = [p for p in view["series"]["metrics/event2"] if p[0] == day]
    assert point == [[day, 500, 100, 105, 95, "high"]]
    [cal] = [c for c in view["calendar"] if c["day"] == day]
    assert cal["high"] == ["metrics/event2"]
    assert [(h["run_id"], h["high"]) for h in view["history"]] == [(rid, 1), (before, 0)]
    assert [h["high"] for h in adobe["history"]] == [0, 0]
    for h in view["history"]:
        want = _tile_counts(build_view(store, h["run_id"], method=method, sensitivity=sensitivity))
        drops = want.pop("drops")
        assert drops == pytest.approx(h["drops"])
        assert {k: h[k] for k in want} == want
    # component_days keeps Adobe's values.
    flags = store.con.execute(
        "SELECT count(*) FROM component_days WHERE run_id = ? AND anomaly IS NOT NULL", [rid]
    ).fetchone()[0]
    assert flags == 0


@pytest.mark.parametrize("method", LOCAL_METHODS)
def test_a_local_method_keeps_every_count(tmp_path, method):
    comps, vals = _macro_data()
    store, rid = _build(
        tmp_path,
        comps,
        vals,
        top={"current": [(1, "x", 1.0)]},
        issues=[("run", None, "anomaly_not_allowed", "not allowed")],
    )
    adobe = build_view(store, rid)
    view = build_view(store, rid, method=method)
    for key, rows in adobe["views"].items():
        for a, b in zip(rows, view["views"][key], strict=True):
            assert {**a, "anomaly": None} == {**b, "anomaly": None}
    for a, b in zip(adobe["series"].items(), view["series"].items(), strict=True):
        assert a[0] == b[0] and [p[:2] for p in a[1]] == [p[:2] for p in b[1]]
    for key in ("run", "declines", "pairs", "issues", "component_drops", "change_alert"):
        assert adobe[key] == view[key]
    assert [c["day"] for c in adobe["calendar"]] == [c["day"] for c in view["calendar"]]


def test_the_default_method_is_adobe(tmp_path):
    comps, vals = _macro_data()
    store, rid = _build(tmp_path, comps, vals)
    assert build_view(store, rid) == build_view(store, rid, method="adobe")


def test_a_query_without_the_part_to_replace_fails_for_a_local_method():
    # Without the check, the page would show Adobe's values under the name of a local method.
    from ahd.summary import _DV, _LOCAL_RUN_DV, _for_method

    assert _for_method("SELECT 1", "adobe", _DV, _LOCAL_RUN_DV) == "SELECT 1"
    for sql in ("SELECT 1", f"WITH {_DV}, {_DV} SELECT 1"):
        with pytest.raises(RuntimeError, match="exactly once"):
            _for_method(sql, "weekday-median", _DV, _LOCAL_RUN_DV)
    sql = f"WITH {_DV} SELECT 1"
    assert _for_method(sql, "weekday-iqr", _DV, _LOCAL_RUN_DV) == f"WITH {_LOCAL_RUN_DV} SELECT 1"


# ---- since when an alert has held -------------------------------------------------------------
# An alert follows one condition: a value of 0, then the direction of the anomaly flag, then a
# change past the drop or spike threshold. Its streak is the unbroken run of days, ending on the
# alert's day, on which that condition held. It is "at least" when the day before the streak
# cannot be judged for the condition.

PLAIN = "plain"  # a value without Adobe's expected value, so a flag cannot be judged that day


def _history(cid, last, base=100.0):
    """base on every day with Adobe's range around it and no flag, then the values in last,
    oldest first, ending yesterday.

    An entry of last is a value, (value, "high" or "low") for a value that Adobe flagged,
    (PLAIN, value) for a value without Adobe's range, None for a day without a row, or NaN.
    """
    out = []
    for d in _days(START, YESTERDAY - timedelta(days=len(last))):
        out.append(_dv(cid, d, base, base, base * 1.3, base * 0.7, False))
    for back, v in enumerate(reversed(last)):
        day = YESTERDAY - timedelta(days=back)
        if v is None:
            continue
        if isinstance(v, tuple) and v[0] == PLAIN:
            out.append(_dv(cid, day, v[1]))
        elif isinstance(v, tuple):
            value, flag = v
            if flag == "high":
                out.append(_dv(cid, day, value, base, value - 1, base * 0.7, True))
            else:
                out.append(_dv(cid, day, value, base, base * 1.3, value + 1, True))
        else:
            out.append(_dv(cid, day, v, base, base * 1.3, base * 0.7, False))
    return out


SINCE_CASES = {
    # A drop to zero on yesterday only.
    "metrics/event1": [100.0, 0.0],
    # Zero on the last 3 days.
    "metrics/event2": [100.0, 0.0, 0.0, 0.0],
    # Adobe flagged the first two zeros, so the label changed from unusual to dropped to zero.
    # The value was 0 on all 4 days, so it is one streak.
    "metrics/event3": [100.0, (0.0, "low"), (0.0, "low"), 0.0, 0.0],
    # A day with data breaks the streak.
    "metrics/event4": [0.0, 100.0, 0.0, 0.0],
    # Zero on 10 days. A zero can be judged on every day of the run, so the start is known.
    "metrics/event5": [100.0] + [0.0] * 10,
    # A day without a row cannot be judged, so the streak may be longer.
    "metrics/event6": [0.0, None, 0.0],
    # A value that is not a number cannot be judged either.
    "metrics/event7": [0.0, math.nan, 0.0],
    # Above the range on 2 days, after a day below it: only the same direction counts.
    "metrics/event8": [(5.0, "low"), (500.0, "high"), (500.0, "high")],
    # Above the range on 2 days, after a day without Adobe's range.
    "metrics/event9": [(PLAIN, 100.0), (500.0, "high"), (500.0, "high")],
    # Drops of 50, 20 and 40 percent, oldest first.
    "metrics/event10": [100.0, 50.0, 80.0, 60.0],
    # A drop on every day that has a weekday average, and on the day before them.
    "metrics/event11": [50.0] * 8,
    # A value of 0 is also more than 30 percent below the weekday average.
    "metrics/event12": [100.0, 0.0, 50.0],
    # Spikes of 50, 20 and 80 percent, oldest first.
    "metrics/event13": [100.0, 150.0, 120.0, 180.0],
    # Above the range on every day of the run.
    "metrics/event14": [(500.0, "high")] * 91,
    # Never collected data: not an alert.
    "metrics/event15": [0.0] * 91,
}


@pytest.fixture
def since_run(tmp_path):
    comps = [_comp(cid, cid, number=i) for i, cid in enumerate(SINCE_CASES, start=1)]
    vals = []
    for cid, last in SINCE_CASES.items():
        vals += _history(cid, last, 0.0 if cid == "metrics/event15" else 100.0)
    return _build(tmp_path, comps, vals)


def _since(store, rid, **options):
    """{component: (alert, since, days, at_least, is_new)} from the alerts() macro."""
    args = "".join(f", {k} := ?" for k in options)
    rows = store.con.execute(
        f"SELECT component_id, alert, since, days, at_least, is_new FROM alerts(?{args})",
        [rid, *options.values()],
    ).fetchall()
    return {r[0]: tuple(r[1:]) for r in rows}


def _ago(n):
    return YESTERDAY - timedelta(days=n)


def test_alerts_macro_says_since_when_each_alert_held(since_run):
    got = _since(*since_run)
    assert got == {
        "metrics/event1": ("dropped to zero", YESTERDAY, 1, False, True),
        "metrics/event2": ("dropped to zero", _ago(2), 3, False, False),
        "metrics/event3": ("dropped to zero", _ago(3), 4, False, False),
        "metrics/event4": ("dropped to zero", _ago(1), 2, False, False),
        "metrics/event5": ("dropped to zero", _ago(9), 10, False, False),
        "metrics/event6": ("dropped to zero", YESTERDAY, 1, True, False),
        "metrics/event7": ("dropped to zero", YESTERDAY, 1, True, False),
        "metrics/event8": ("unusual", _ago(1), 2, False, False),
        "metrics/event9": ("unusual", _ago(1), 2, True, False),
        "metrics/event10": ("large drop", YESTERDAY, 1, False, True),
        # The day before the 7 days has no weekday average, so it cannot be judged.
        "metrics/event11": ("large drop", _ago(6), 7, True, False),
        "metrics/event12": ("large drop", _ago(1), 2, False, False),
        "metrics/event13": ("large spike", YESTERDAY, 1, False, True),
        # The streak starts on the first day of the run, and the day before is not in it.
        "metrics/event14": ("unusual", START, 91, True, False),
    }


def test_the_thresholds_make_a_streak_longer(since_run):
    got = _since(*since_run, threshold=0.15, spike_threshold=0.10)
    assert got["metrics/event10"] == ("large drop", _ago(2), 3, False, False)
    assert got["metrics/event13"] == ("large spike", _ago(2), 3, False, False)
    shorter = _since(*since_run, threshold=0.45)
    assert shorter["metrics/event11"] == ("large drop", _ago(6), 7, True, False)
    assert "metrics/event10" not in shorter


def test_a_streak_ends_on_for_day(since_run):
    got = _since(*since_run, for_day=_ago(2))
    # On that day, Adobe flagged the 0, so the alert is unusual. Its streak follows the 0.
    assert got["metrics/event3"] == ("unusual", _ago(3), 2, False, False)
    assert got["metrics/event2"] == ("dropped to zero", _ago(2), 1, False, True)
    assert got["metrics/event14"] == ("unusual", START, 89, True, False)
    # A day before the 7 days with a weekday average has drops to zero and flags only.
    older = _since(*since_run, for_day=_ago(8))
    assert older == {
        "metrics/event5": ("dropped to zero", _ago(9), 2, False, False),
        "metrics/event14": ("unusual", START, 83, True, False),
    }


def test_the_new_columns_come_after_the_old_ones(since_run):
    cur = since_run[0].con.execute("SELECT * FROM alerts(?)", [since_run[1]])
    assert [d[0] for d in cur.description] == [
        "run_id",
        "day",
        "component_id",
        "name",
        "group",
        "metric_type",
        "value",
        "usual",
        "expected",
        "pct_vs_usual",
        "alert",
        "anomaly",
        "since",
        "days",
        "at_least",
        "is_new",
    ]


def _jump(cid):
    """100 on every day, and 1000 on each of the last 35 days, which have a local range."""
    return [
        _dv(cid, d, 1000.0 if d >= TODAY - timedelta(days=35) else 100.0)
        for d in _days(START, TODAY)
    ]


@pytest.mark.parametrize(
    ("method", "want"),
    [
        # Adobe gave no range, so the values are large spikes, on each of the last 7 days.
        ("adobe", ("large spike", _ago(6), 7, True, False)),
        # The median of each weekday stays 100, so every day with a range is flagged. The day
        # before them has no range.
        ("weekday-median", ("unusual", TODAY - timedelta(days=35), 35, True, False)),
        # The quartiles take in the values of 1000 after 3 weeks, so the last 2 weeks are not
        # flagged. They are spikes.
        ("weekday-iqr", ("large spike", _ago(6), 7, True, False)),
    ],
)
def test_a_streak_follows_the_anomaly_method(tmp_path, method, want):
    store, rid = _build(
        tmp_path, [_comp("metrics/event1", "E1", number=1)], _jump("metrics/event1")
    )
    assert _since(store, rid, method=method) == {"metrics/event1": want}


def _spread_then(cid, last):
    """S12 on each weekday by week, 100 on the 4 days after, then last, ending yesterday."""
    out = []
    for d in _days(START, YESTERDAY):
        week = (d - START).days // 7
        if d > YESTERDAY - timedelta(days=len(last)):
            v = last[len(last) - 1 - (YESTERDAY - d).days]
        else:
            v = S12[week] if week < 12 else 100.0
        out.append(_dv(cid, d, v))
    return out


@pytest.mark.parametrize(
    ("sensitivity", "want"),
    [
        # 140 is outside the range of normal, 66.6 to 133.4, and 125 is inside it.
        ("normal", ("unusual", YESTERDAY, 1, False, True)),
        # high narrows the range to 77.8 to 122.2, so 125 is flagged too.
        ("high", ("unusual", _ago(2), 3, False, False)),
    ],
)
def test_a_streak_follows_the_sensitivity(tmp_path, sensitivity, want):
    cid = "metrics/event1"
    store, rid = _build(
        tmp_path, [_comp(cid, "E1", number=1)], _spread_then(cid, [140.0, 125.0, 140.0])
    )
    got = _since(store, rid, method="weekday-median", sensitivity=sensitivity)
    assert got == {cid: want}


def test_page_alerts_say_since_when(since_run):
    view = build_view(*since_run)
    day = YESTERDAY.isoformat()
    got = {a["component_id"]: a for a in view["alerts"] if a["day"] == day}
    # A drop to zero or a flag does not depend on the sliders, so the SQL counts its days.
    assert (got["metrics/event3"]["since_days"], got["metrics/event3"]["since_open"]) == (4, False)
    assert (got["metrics/event6"]["since_days"], got["metrics/event6"]["since_open"]) == (1, True)
    assert (got["metrics/event9"]["since_days"], got["metrics/event9"]["since_open"]) == (2, True)
    assert got["metrics/event14"]["since_days"] == 91 and got["metrics/event14"]["since_open"]
    assert all(got[c]["changes"] is None for c in ("metrics/event3", "metrics/event9"))
    # A large drop or spike depends on its slider. changes holds the change of each day back
    # to the first of the 7 days, so the page can count the days past the slider.
    # The list stops at the first day that no slider setting counts, which it gives as 0.
    assert got["metrics/event10"]["changes"] == pytest.approx([-0.4, -0.2, -0.5, 0.0])
    assert got["metrics/event10"]["since_days"] is None
    assert got["metrics/event13"]["changes"] == pytest.approx([0.8, 0.2, 0.5, 0.0])
    # Yesterday's weekday average holds the 50 of the Friday a week before: 1150 / 12.
    assert got["metrics/event11"]["changes"] == pytest.approx([50 * 12 / 1150 - 1] + [-0.5] * 6)
    # The day before yesterday counts back from its own day.
    before = {a["component_id"]: a for a in view["alerts"] if a["day"] == _ago(1).isoformat()}
    assert before["metrics/event10"]["changes"] == pytest.approx([-0.2, -0.5, 0.0])
    assert before["metrics/event2"]["since_days"] == 2


def test_a_day_without_a_value_is_empty_in_changes(tmp_path):
    cid = "metrics/event1"
    store, rid = _build(tmp_path, [_comp(cid, "E1", number=1)], _history(cid, [50.0, None, 50.0]))
    [a] = [a for a in build_view(store, rid)["alerts"] if a["day"] == YESTERDAY.isoformat()]
    # The list stops at the day without a value.
    assert a["changes"] == [-0.5, None]


def _page_since(a, threshold, spike):
    """(days, at_least) of a page alert, as report.js counts them for the sliders."""
    changes = a["changes"]
    if changes is None:
        return a["since_days"], a["since_open"]
    drop = a["pct_vs_usual"] < 0
    n = 0
    while n < len(changes) and changes[n] is not None:
        if not (changes[n] < -threshold if drop else changes[n] > spike):
            break
        n += 1
    return n, n == len(changes) or changes[n] is None


def _page_alerts(view, day, threshold, spike):
    """{component: (alert, days, at_least)} of the page on day, for the slider settings."""
    out = {}
    for a in view["alerts"]:
        if a["day"] != day.isoformat() or (a["zero"] and a["unused"]):
            continue
        if a["anomaly"]:
            kind = "unusual"
        elif a["zero"]:
            kind = "dropped to zero"
        elif a["usual"] >= 10 and a["pct_vs_usual"] < -threshold:
            kind = "large drop"
        elif a["usual"] >= 10 and a["pct_vs_usual"] > spike:
            kind = "large spike"
        else:
            continue
        out[a["component_id"]] = (kind, *_page_since(a, threshold, spike))
    return out


SETTINGS = [(0.10, 0.10), (0.15, 0.20), (0.30, 0.50), (0.45, 0.10), (0.60, 1.50)]


@pytest.mark.parametrize(("method", "sensitivity"), [("adobe", "normal"), *LOCAL_LEVELS])
@pytest.mark.parametrize("data", ["since", "random"])
def test_page_and_macro_agree_on_since(tmp_path, method, sensitivity, data):
    if data == "since":
        comps = [_comp(cid, cid, number=i) for i, cid in enumerate(SINCE_CASES, start=1)]
        vals = []
        for cid, last in SINCE_CASES.items():
            vals += _history(cid, last, 0.0 if cid == "metrics/event15" else 100.0)
        comps.append(_comp("metrics/event16", "E16", number=16))
        vals += _jump("metrics/event16")
        comps.append(_comp("metrics/event17", "E17", number=17))
        vals += _spread_then("metrics/event17", [140.0, 125.0, 140.0])
        store, rid = _build(tmp_path, comps, vals)
    else:
        store, rid = _random_run(tmp_path)
    view = build_view(store, rid, method=method, sensitivity=sensitivity)
    compared = 0
    for day in [YESTERDAY - timedelta(days=n) for n in range(7)]:
        for threshold, spike in SETTINGS:
            page = _page_alerts(view, day, threshold, spike)
            rows = store.con.execute(
                "SELECT component_id, alert, days, at_least, since, is_new FROM alerts(?,"
                " threshold := ?, spike_threshold := ?, for_day := ?, method := ?,"
                " sensitivity := ?)",
                [rid, threshold, spike, day, method, sensitivity],
            ).fetchall()
            macro = {r[0]: r[1:4] for r in rows}
            assert macro == page, (day, threshold, spike)
            for _cid, _alert, days, at_least, since, is_new in rows:
                assert since == day - timedelta(days=days - 1)
                assert is_new == (days == 1 and not at_least)
            compared += len(rows)
    assert compared > 50
