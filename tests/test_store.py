from datetime import date

import duckdb
import pytest

from ahd.api.reports import Column, DailyValue
from ahd.catalog import Component
from ahd.store import SCHEMA_VERSION, VIEWS_VERSION, SchemaVersionError, Store


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
    )
    store.add_pair(rid, "variables/page", "metrics/occurrences", "Page", "Occurrences", "int")
    store.add_components(rid, [_comp()])
    store.add_daily_values(
        rid,
        [
            DailyValue("metrics/pageviews", date(2026, 11, 6), 5.0, 4.0, 6.0, 3.0, False),
            DailyValue("individual/1", date(2026, 11, 6), 2.0, None, None, None, None),
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
    [pair] = s.list_pairs(rid)
    assert (pair["dimension"], pair["metric"], pair["dimension_name"]) == (
        "variables/page",
        "metrics/occurrences",
        "Page",
    )
    c = s.con
    assert c.execute("select metric_id, exists_dimension from components").fetchall() == [
        ("metrics/pageviews", None)
    ]
    assert (
        c.execute("select count(*) from daily_values where component_id='individual/1'").fetchone()[
            0
        ]
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
    assert SCHEMA_VERSION == 7
    assert s.con.execute("select version from schema_version").fetchall() == [(7,)]
    s.con.close()
    s2 = Store.open(tmp_path / "a.duckdb")  # reopening does not add a second row
    assert s2.con.execute("select version from schema_version").fetchall() == [(7,)]


def test_a_run_records_whether_date_set_its_run_date(tmp_path):
    s = Store.open(tmp_path / "a.duckdb")
    plain = s.create_run("rs1", "S", None, date(2026, 11, 8), "UTC", "0.1")
    given = s.create_run("rs1", "S", None, date(2026, 9, 16), "UTC", "0.1", date_given=True)
    assert s.get_run(plain)["date_given"] is False
    assert s.get_run(given)["date_given"] is True


@pytest.mark.parametrize(
    ("setup", "advice"),
    [
        # A newer layout needs a newer ahd. A layout too old to import can only be started again.
        ("update schema_version set version = 99", "That version is newer. Update ahd"),
        ("update schema_version set version = 2", "Delete the file"),
        ("drop table schema_version", "Delete the file"),
    ],
)
def test_other_version_is_refused(tmp_path, setup, advice):
    p = tmp_path / "a.duckdb"
    Store.open(p).con.close()
    con = duckdb.connect(str(p))
    con.execute(setup)
    con.close()
    with pytest.raises(SchemaVersionError) as e:
        Store.open(p)
    assert isinstance(e.value, ValueError)
    assert "different version of ahd" in str(e.value) and advice in str(e.value)
    duckdb.connect(str(p)).close()  # the refused file was closed again


def test_currency_and_metric_type_are_stored(tmp_path):
    s = Store.open(tmp_path / "a.duckdb")
    rid = s.create_run("rs1", "N", None, date(2026, 11, 7), "UTC", "0.1", currency="EUR")
    c = Component(
        "metrics/event2",
        "Rev",
        "metric",
        "custom_events",
        2,
        Column("metrics/event2", "metrics/event2", None),
        None,
        "currency",
    )
    s.add_components(rid, [c, _comp()])
    assert s.get_run(rid)["currency"] == "EUR"
    rows = s.con.execute(
        "select component_id, metric_type from components order by component_id"
    ).fetchall()
    assert rows == [("metrics/event2", "currency"), ("metrics/pageviews", None)]


def _custom(group, n, kind="metric"):
    prefix = {"custom_events": "metrics/event", "custom_evars": "variables/evar"}.get(
        group, "variables/prop"
    )
    return [
        Component(f"{prefix}{i}", f"C{i}", kind, group, i, Column(f"{prefix}{i}", "m", None), True)
        for i in range(1, n + 1)
    ]


def _counts():
    return {"custom_events": 0, "custom_evars": 0, "custom_props": 0}


def test_prune_finds_by_number_and_date(tmp_path):
    s = Store.open(tmp_path / "a.duckdb")
    a = _fill(s)
    s.con.execute("UPDATE runs SET today = DATE '2026-09-01' WHERE run_id = ?", [a])
    b = _fill(s)
    c = _fill(s)
    assert [r["run_id"] for r in s.runs_to_prune([c], None)] == [c]
    assert [r["run_id"] for r in s.runs_to_prune([], date(2026, 10, 1))] == [a]
    assert [r["run_id"] for r in s.runs_to_prune([c, 99], date(2026, 10, 1))] == [a, c]
    assert s.runs_to_prune([], None) == []
    assert b not in [r["run_id"] for r in s.runs_to_prune([c], date(2026, 10, 1))]


def test_delete_runs_clears_every_table(tmp_path):
    from ahd.store import RUN_TABLES

    s = Store.open(tmp_path / "a.duckdb")
    a = _fill(s)
    b = _fill(s)
    s.delete_runs([a])
    for table in RUN_TABLES:
        ids = {r[0] for r in s.con.execute(f"SELECT DISTINCT run_id FROM {table}").fetchall()}
        assert ids == {b}, table
    s.delete_runs([])
    # Run numbers are never reused, so report file names stay unique.
    assert _fill(s) == b + 1


def test_delete_runs_is_all_or_nothing(tmp_path, monkeypatch):
    s = Store.open(tmp_path / "a.duckdb")
    a = _fill(s)
    monkeypatch.setattr("ahd.store.RUN_TABLES", ("components", "no_such_table", "runs"))
    with pytest.raises(duckdb.Error):
        s.delete_runs([a])
    assert s.con.execute("SELECT count(*) FROM components WHERE run_id = ?", [a]).fetchone()[0]
    assert s.get_run(a)["run_id"] == a


def test_component_counts_and_previous_run(tmp_path):
    from ahd.catalog import CUSTOM_GROUPS

    s = Store.open(tmp_path / "a.duckdb")
    a = _fill(s, "rs1")
    s.add_components(a, _custom("custom_events", 3) + _custom("custom_props", 2, "dimension"))
    other = _fill(s, "rs2")
    b = _fill(s, "rs1")
    empty = s.create_run("rs1", "N", None, date(2026, 11, 7), "UTC", "0.1")
    c = _fill(s, "rs1")
    assert s.component_counts(a, CUSTOM_GROUPS) == {
        **_counts(),
        "custom_events": 3,
        "custom_props": 2,
    }
    assert s.component_counts(b, CUSTOM_GROUPS) == _counts()
    assert s.previous_run(a) is None
    assert s.previous_run(b) == a
    assert s.previous_run(other) is None
    assert s.previous_run(c) == b  # the run with no components is skipped
    assert s.previous_run(empty) == b


def test_pairs_are_numbered_per_run(tmp_path):
    s = Store.open(tmp_path / "a.duckdb")
    a = _fill(s)
    b = _fill(s)
    assert s.add_pair(a, "variables/evar1", "metrics/orders") == 2
    assert s.add_pair(b, "variables/evar2", "metrics/orders") == 2
    assert [p["pair"] for p in s.list_pairs(a)] == [1, 2]
    s.add_top_items(a, "current", [(1, "x", 1.0)], pair=2)
    rows = s.con.execute(
        "select pair, count(*) from top_items where run_id = ? group by pair order by pair", [a]
    ).fetchall()
    assert rows == [(1, 2), (2, 1)]
    s.set_status(a, "ok")
    assert s.get_run(a)["status"] == "ok"


def test_latest_runs_view(tmp_path):
    s = Store.open(tmp_path / "a.duckdb")
    a = _fill(s, "rs1")
    b = _fill(s, "rs1")
    c = _fill(s, "rs2")
    running = s.create_run("rs2", "N", "seg", date(2026, 11, 7), "UTC", "0.1")
    rows = s.con.execute("select run_id from latest_runs order by run_id").fetchall()
    # One per suite and segment; a run still marked running is never the latest.
    assert rows == [(b,), (c,)] and a not in rows and running not in rows


def _run_on(store, today, rsid="rs1"):
    """A finished run for this run date."""
    rid = store.create_run(rsid, "Name", None, today, "UTC", "0.1")
    store.add_components(rid, [_comp()])
    store.finish_run(rid, "ok")
    return rid


def _latest(store):
    return store.con.execute("select run_id from latest_runs order by run_id").fetchall()


def test_latest_runs_goes_by_the_run_date_not_the_run_number(tmp_path):
    s = Store.open(tmp_path / "a.duckdb")
    newest = _run_on(s, date(2026, 11, 9))
    # Made later with --date for an old date, so it has the higher number.
    _run_on(s, date(2026, 10, 15))
    assert _latest(s) == [(newest,)]


def test_latest_runs_of_one_date_is_the_run_made_last(tmp_path):
    s = Store.open(tmp_path / "a.duckdb")
    first = _run_on(s, date(2026, 11, 9))
    second = _run_on(s, date(2026, 11, 9))
    assert _latest(s) == [(second,)]
    # The numbers do not decide it, e.g., after an import gave the runs new numbers.
    s.con.execute(
        "update runs set started_at = started_at + interval 1 hour where run_id = ?", [first]
    )
    assert _latest(s) == [(first,)]
    # Two runs that started at the same time go by their number.
    s.con.execute("update runs set started_at = timestamp '2026-11-09 08:00:00'")
    assert _latest(s) == [(second,)]


def test_previous_run_goes_by_the_start_time_not_the_run_number(tmp_path):
    s = Store.open(tmp_path / "a.duckdb")
    yesterday = _run_on(s, date(2026, 11, 8))
    # Two old runs with higher numbers, as ahd import leaves them in a file that held a run.
    old_a = _run_on(s, date(2026, 10, 15))
    old_b = _run_on(s, date(2026, 10, 16))
    for run, started in ((old_a, "2026-10-15 06:00:00"), (old_b, "2026-10-16 06:00:00")):
        s.con.execute("update runs set started_at = ?::timestamp where run_id = ?", [started, run])
    s.con.execute(
        "update runs set started_at = timestamp '2026-11-08 06:00:00' where run_id = ?", [yesterday]
    )
    today = _run_on(s, date(2026, 11, 9))
    assert s.previous_run(today) == yesterday
    assert s.previous_run(yesterday) == old_b
    assert s.previous_run(old_b) == old_a
    assert s.previous_run(old_a) is None
    # Runs that started at the same time go by their number.
    s.con.execute("update runs set started_at = timestamp '2026-11-08 06:00:00'")
    assert s.previous_run(today) == old_b and s.previous_run(old_a) == yesterday
    assert s.previous_run(yesterday) is None


def test_bulk_insert_keeps_every_value(tmp_path):
    import math

    s = Store.open(tmp_path / "t.duckdb")
    rows = [
        DailyValue("metrics/event1", date(2026, 11, 1), 1.5, None, None, None, None),
        DailyValue("metrics/event1", date(2026, 11, 2), float("nan"), 2.0, 3.0, 1.0, True),
        DailyValue("metrics/event2", date(2026, 11, 2), float("inf"), -1.0, 0.0, -2.0, False),
    ]
    s.add_daily_values(7, rows)
    got = s.con.execute(
        "select component_id, day, value, expected, upper, lower, anomaly from daily_values"
        " where run_id = 7 order by component_id, day"
    ).fetchall()
    assert got[0] == ("metrics/event1", date(2026, 11, 1), 1.5, None, None, None, None)
    assert math.isnan(got[1][2]) and got[1][3:] == (2.0, 3.0, 1.0, True)
    assert got[2][2] == float("inf") and got[2][6] is False
    s.add_top_items(7, "current", [(1, 'a\'b "c"', 3.0), (2, "<x>", 1.0)], pair=2)
    assert s.con.execute(
        "select rank, item, value, pair from top_items order by rank"
    ).fetchall() == [
        (1, 'a\'b "c"', 3.0, 2),
        (2, "<x>", 1.0, 2),
    ]


# A version of the alerts macro from an earlier ahd, with the same parameters but other rules.
OLD_MACRO = (
    "CREATE OR REPLACE MACRO alerts(run_number, threshold := 0.30, spike_threshold := 0.50,"
    " min_usual := 10, for_day := NULL) AS TABLE SELECT 'old rule' AS alert"
)


def _fill_zero_day(store):
    """A run whose component dropped to 0 yesterday, which the alerts macro reports."""
    rid = store.create_run("rs1", "Name", None, date(2026, 11, 7), "UTC", "0.1")
    store.add_components(rid, [_comp("metrics/event1")])
    store.add_daily_values(
        rid,
        [
            DailyValue("metrics/event1", date(2026, 11, 1), 3.0, None, None, None, None),
            DailyValue("metrics/event1", date(2026, 11, 6), 0.0, None, None, None, None),
        ],
    )
    store.finish_run(rid, "ok")
    return rid


def _edit(path, *statements):
    con = duckdb.connect(str(path))
    for sql in statements:
        con.execute(sql)
    con.close()


def _alerts(con, rid):
    return con.execute("select alert from alerts(?)", [rid]).fetchall()


def test_new_file_gets_views_version(tmp_path):
    p = tmp_path / "a.duckdb"
    Store.open(p).con.close()
    s = Store.open(p)  # reopening does not add a second row
    assert s.con.execute("select version from views_version").fetchall() == [(VIEWS_VERSION,)]


@pytest.mark.parametrize(
    "setup",
    [
        "drop table views_version",  # a file made by 0.1.2 or earlier
        "update views_version set version = 0",
    ],
)
def test_older_views_are_replaced_on_a_write_open(tmp_path, setup):
    p = tmp_path / "a.duckdb"
    s = Store.open(p)
    rid = _fill_zero_day(s)
    other = _fill(s)
    s.con.close()
    _edit(p, setup, OLD_MACRO, "CREATE OR REPLACE VIEW latest_runs AS SELECT 1 AS z")
    s = Store.open(p)
    # The stored runs are kept, and they now follow the new rules.
    assert [r["run_id"] for r in s.list_runs()] == [other, rid]
    assert _alerts(s.con, rid) == [("dropped to zero",)]
    # Each run is the latest of its own segment.
    assert s.con.execute("select run_id from latest_runs order by run_id").fetchall() == [
        (rid,),
        (other,),
    ]
    assert s.con.execute("select version from views_version").fetchall() == [(VIEWS_VERSION,)]


def test_newer_views_are_replaced(tmp_path):
    # After a newer ahd, an older one puts back its own views, so that the alert line of ahd run
    # follows the same rules as its report.
    p = tmp_path / "a.duckdb"
    s = Store.open(p)
    rid = _fill_zero_day(s)
    s.con.close()
    _edit(p, "update views_version set version = 99", OLD_MACRO)
    s = Store.open(p)
    assert _alerts(s.con, rid) == [("dropped to zero",)]
    assert s.con.execute("select version from views_version").fetchall() == [(VIEWS_VERSION,)]


def test_read_only_open_keeps_older_views(tmp_path):
    # DuckDB refuses to replace a view in a read-only file, so ahd does not try.
    p = tmp_path / "a.duckdb"
    s = Store.open(p)
    rid = _fill_zero_day(s)
    s.con.close()
    _edit(p, "drop table views_version", OLD_MACRO)
    s = Store.open(p, read_only=True)
    assert _alerts(s.con, rid) == [("old rule",)]
    assert [r["run_id"] for r in s.list_runs()] == [rid]
    s.con.close()
    con = duckdb.connect(str(p), read_only=True)
    assert not con.execute(
        "select count(*) from duckdb_tables() where table_name = 'views_version'"
    ).fetchone()[0]


def test_failed_views_update_keeps_the_old_views(tmp_path, monkeypatch):
    p = tmp_path / "a.duckdb"
    s = Store.open(p)
    rid = _fill_zero_day(s)
    s.con.close()
    _edit(p, "update views_version set version = 0", OLD_MACRO)
    broken = "CREATE OR REPLACE VIEW latest_runs AS SELECT 1 AS z; SELECT no_such_column FROM runs;"
    monkeypatch.setattr("ahd.store._views", lambda: broken)
    with pytest.raises(duckdb.Error):
        Store.open(p)
    con = duckdb.connect(str(p))  # the file was closed again
    assert _alerts(con, rid) == [("old rule",)]
    assert len(con.execute("from latest_runs").description) > 1
    assert con.execute("select version from views_version").fetchall() == [(0,)]


# The SHA-256 of sql/views.sql. When you change views.sql, raise VIEWS_VERSION in store.py and
# put the new hash here.
VIEWS_SHA256 = "e71a88e3826ff352af5a026c7c6eeee7e16ef41ae779feebcec5224ca96d4186"


def test_views_version_is_raised_when_views_sql_changes():
    import hashlib

    from ahd.store import _views

    found = hashlib.sha256(_views().encode("utf-8")).hexdigest()
    assert VIEWS_VERSION == 4 and found == VIEWS_SHA256, (
        "sql/views.sql changed. Raise VIEWS_VERSION in store.py, and put the new version and hash"
        " in this test."
    )
