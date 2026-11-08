from datetime import date

import duckdb
import pytest

from ahd.api.reports import Column, DailyValue
from ahd.catalog import Component
from ahd.store import SCHEMA_VERSION, SchemaVersionError, Store


def _comp(cid="metrics/pageviews"):
    return Component(cid, "Page Views", "metric", "Core", 1, Column(cid, cid, None), True)


def _fill(store, rsid="rs1"):
    rid = store.create_run(
        rsid,
        "Name",
        "seg",
        date(2026, 11, 7),
        "US/Pacific",
        "0.1",
        dimension="variables/page",
        metric="metrics/occurrences",
    )
    store.add_components(rid, [_comp()])
    store.add_daily_values(
        rid,
        [
            DailyValue("metrics/pageviews", date(2026, 11, 6), 5.0, 4.0, 6.0, 3.0, False),
            DailyValue("individual", date(2026, 11, 6), 2.0, None, None, None, None),
        ],
    )
    store.add_top_items(rid, "current", [(1, "home", 10.0), (2, "about", 4.0)])
    store.add_breakdown(
        rid, "browser", ["Chrome", "Safari"], [("a", [1.0, 2.0]), ("b", [3.0, 4.0])], [4.0, 6.0]
    )
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
    assert run["dimension"] == "variables/page" and run["metric"] == "metrics/occurrences"
    c = s.con
    assert c.execute("select metric_id, exists_dimension from components").fetchall() == [
        ("metrics/pageviews", None)
    ]
    assert (
        c.execute("select count(*) from daily_values where component_id='individual'").fetchone()[0]
        == 1
    )
    assert c.execute("select anomaly from daily_values where expected is not null").fetchone() == (
        False,
    )
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
    assert c.execute(
        "select col_index, label, total from breakdown_totals order by col_index"
    ).fetchall() == [
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
    cols = [
        r[0].lower()
        for r in s.con.execute("select column_name from information_schema.columns").fetchall()
    ]
    assert cols and not [c for c in cols if "secret" in c or "token" in c]


def test_new_file_gets_schema_version(tmp_path):
    s = Store.open(tmp_path / "a.duckdb")
    assert SCHEMA_VERSION == 1
    assert s.con.execute("select version from schema_version").fetchall() == [(1,)]
    s.con.close()
    s2 = Store.open(tmp_path / "a.duckdb")  # reopening does not add a second row
    assert s2.con.execute("select version from schema_version").fetchall() == [(1,)]


def test_newer_schema_version_is_refused(tmp_path):
    p = tmp_path / "a.duckdb"
    Store.open(p).con.close()
    con = duckdb.connect(str(p))
    con.execute("update schema_version set version = 99")
    con.close()
    with pytest.raises(SchemaVersionError) as e:
        Store.open(p)
    assert isinstance(e.value, ValueError)
    assert "99" in str(e.value) and "newer" in str(e.value)
    duckdb.connect(str(p)).close()  # the refused file was closed again


def test_old_file_without_version_is_upgraded(tmp_path):
    p = tmp_path / "old.duckdb"
    con = duckdb.connect(str(p))
    con.execute("""CREATE SEQUENCE run_seq START 1;
        CREATE TABLE runs (run_id INTEGER PRIMARY KEY, rsid VARCHAR, rsid_name VARCHAR,
            segment_id VARCHAR, today DATE, timezone VARCHAR, tool_version VARCHAR,
            status VARCHAR, started_at TIMESTAMP, finished_at TIMESTAMP);
        INSERT INTO runs VALUES (nextval('run_seq'), 'rs0', 'Old', NULL, DATE '2026-11-01',
            'UTC', '0.0', 'ok', NULL, NULL);""")
    con.close()
    s = Store.open(p)
    assert s.con.execute("select version from schema_version").fetchall() == [(1,)]
    old = s.get_run(1)
    assert old["rsid"] == "rs0" and old["dimension"] is None and old["metric"] is None
    assert _fill(s) == 2
