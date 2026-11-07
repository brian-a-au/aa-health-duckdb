from datetime import date

from ahd.api.reports import Column, DailyValue
from ahd.catalog import Component
from ahd.store import Store


def _comp(cid="metrics/pageviews"):
    return Component(cid, "Page Views", "metric", "Core", 1, Column(cid, cid, None), True)


def _fill(store, rsid="rs1"):
    rid = store.create_run(rsid, "Name", "seg", date(2026, 11, 7), "US/Pacific", "0.1")
    store.add_components(rid, [_comp()])
    store.add_daily_values(
        rid,
        [
            DailyValue("metrics/pageviews", date(2026, 11, 6), 5.0, 4.0, 6.0, 3.0, False),
            DailyValue("individual", date(2026, 11, 6), 2.0, None, None, None, None),
        ],
    )
    store.add_top_items(rid, "current", [(1, "home", 10.0), (2, "about", 4.0)])
    store.add_breakdown(rid, "browser", ["Chrome", "Safari"], [("a", [1.0, 2.0]), ("b", [3.0, 4.0])], [4.0, 6.0])
    store.add_issue(rid, "column", "metrics/x", "unauthorized_metric", "no access")
    store.finish_run(rid, "partial")
    return rid


def test_roundtrip_run(tmp_path):
    s = Store.open(tmp_path / "sub" / "a.duckdb")
    rid = _fill(s)
    run = s.get_run(rid)
    assert run["status"] == "partial" and run["rsid"] == "rs1"
    assert run["today"] == date(2026, 11, 7)
    assert run["started_at"] is not None and run["finished_at"] is not None
    c = s.con
    assert c.execute("select metric_id, exists_dimension from components").fetchall() == [
        ("metrics/pageviews", None)
    ]
    assert c.execute("select count(*) from daily_values where component_id='individual'").fetchone()[0] == 1
    assert c.execute("select anomaly from daily_values where expected is not null").fetchone() == (False,)
    assert c.execute("select rank, item, value from top_items order by rank").fetchall() == [
        (1, "home", 10.0),
        (2, "about", 4.0),
    ]
    assert c.execute(
        "select col_index, label, rank, item, value from breakdowns order by col_index, rank"
    ).fetchall() == [
        (0, "Chrome", 1, "a", 1.0),
        (0, "Chrome", 2, "b", 3.0),
        (1, "Safari", 1, "a", 2.0),
        (1, "Safari", 2, "b", 4.0),
    ]
    assert c.execute("select col_index, label, total from breakdown_totals order by col_index").fetchall() == [
        (0, "Chrome", 4.0),
        (1, "Safari", 6.0),
    ]
    assert c.execute("select code from issues").fetchall() == [("unauthorized_metric",)]


def test_runs_are_kept_separately(tmp_path):
    s = Store.open(tmp_path / "a.duckdb")
    r1, r2 = _fill(s, "a"), _fill(s, "b")
    assert r1 != r2
    assert [r["run_id"] for r in s.list_runs()] == [r2, r1]
    for t in ("components", "daily_values", "top_items", "breakdowns", "issues"):
        counts = s.con.execute(f"select run_id, count(*) from {t} group by 1 order by 1").fetchall()
        assert [x[0] for x in counts] == [r1, r2] and counts[0][1] == counts[1][1]


def test_reopen_existing_file(tmp_path):
    p = tmp_path / "a.duckdb"
    s = Store.open(p)
    r1 = _fill(s)
    s.con.close()
    s2 = Store.open(p)
    assert s2.get_run(r1)["rsid"] == "rs1"
    assert _fill(s2) > r1


def test_no_credentials_column(tmp_path):
    s = Store.open(tmp_path / "a.duckdb")
    cols = [r[0].lower() for r in s.con.execute("select column_name from information_schema.columns").fetchall()]
    assert cols and not [c for c in cols if "secret" in c or "token" in c]
