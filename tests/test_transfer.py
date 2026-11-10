"""ahd export and ahd import: moving stored runs between files and across layout versions."""

import json
from datetime import date

import duckdb
import pytest

from ahd import store as store_module
from ahd import transfer
from ahd.api.reports import Column, DailyValue
from ahd.catalog import Component
from ahd.cli import main
from ahd.store import RUN_TABLES, SchemaVersionError, Store, StoreError
from ahd.transfer import export_runs, import_runs


def _fill(store, rsid="rs1", today=date(2026, 11, 7)):
    """One run with a row in every table."""
    rid = store.create_run(rsid, "Name", "seg", today, "US/Pacific", "0.1")
    store.add_pair(rid, "variables/page", "metrics/occurrences", "Page", "Occurrences", "int")
    cid = "metrics/pageviews"
    store.add_components(
        rid, [Component(cid, "Page Views", "metric", "Core", 1, Column(cid, cid), True)]
    )
    store.add_daily_values(
        rid,
        [
            DailyValue(cid, date(2026, 11, 6), 5.0, 4.0, 6.0, 3.0, False),
            DailyValue("individual/1", date(2026, 11, 6), 2.0, None, None, None, None),
        ],
    )
    store.add_top_items(rid, "current", [(1, "home", 10.0), (2, "about", 4.0)])
    store.add_breakdown(rid, "browser", ["Chrome", "Safari"], [("a", [1.0, 2.0])], [4.0, 6.0])
    store.add_issue(rid, "column", "metrics/x", "unauthorized_metric", "no access")
    store.finish_run(rid, "partial")
    return rid


def _rows(store):
    """Every row of every run table, in a fixed order."""
    return {
        t: sorted(store.con.execute(f'SELECT * FROM "{t}"').fetchall(), key=repr)
        for t in RUN_TABLES
    }


@pytest.fixture
def source(tmp_path):
    """A file with runs 1 and 3. Run 2 was pruned, so the numbers have a gap."""
    path = tmp_path / "old.duckdb"
    s = Store.open(path)
    for rsid in ("rs1", "rs2", "rs3"):
        _fill(s, rsid)
    s.delete_runs([2])
    rows = _rows(s)
    s.con.close()
    return path, rows


def _set_version(path, version):
    con = duckdb.connect(str(path))
    con.execute("UPDATE schema_version SET version = ?", [version])
    con.close()


def test_import_copies_every_table_and_keeps_the_run_numbers(tmp_path, source):
    path, rows = source
    new = Store.open(tmp_path / "new.duckdb")
    result = import_runs(new, path)
    assert result.numbers == [(1, 1), (3, 3)] and result.renumbered == []
    assert result.already_there == 0 and result.left_out == []
    assert _rows(new) == rows
    # The next run gets a number above the imported ones.
    assert _fill(new, "rs9") == 4
    # The source is free again, and it was not changed.
    old = Store.open(path)
    assert _rows(old) == rows
    old.con.close()
    new.con.close()


def test_export_then_import_gives_the_same_rows(tmp_path, source):
    path, rows = source
    out = tmp_path / "backup"
    counts = export_runs(path, out)
    assert counts["runs"] == 2 and counts["daily_values"] == 4
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest == {
        "format": 1,
        "schema_version": store_module.SCHEMA_VERSION,
        "tables": counts,
    }
    assert sorted(p.name for p in out.glob("*.parquet")) == sorted(
        f"{t}.parquet" for t in RUN_TABLES
    )
    new = Store.open(tmp_path / "new.duckdb")
    assert import_runs(new, out).numbers == [(1, 1), (3, 3)]
    assert _rows(new) == rows
    new.con.close()


def test_a_second_import_of_the_same_source_adds_nothing(tmp_path, source):
    path, rows = source
    new = Store.open(tmp_path / "new.duckdb")
    import_runs(new, path)
    again = import_runs(new, path)
    assert again.numbers == [] and again.already_there == 2
    assert _rows(new) == rows
    new.con.close()


def test_imported_runs_get_new_numbers_when_theirs_are_taken(tmp_path, source):
    path, _rows_before = source
    new = Store.open(tmp_path / "new.duckdb")
    _fill(new, "mine", date(2026, 11, 8))
    result = import_runs(new, path)
    assert result.numbers == [(1, 2), (3, 3)] and result.renumbered == [(1, 2)]
    got = new.con.execute("SELECT run_id, rsid FROM runs ORDER BY run_id").fetchall()
    assert got == [(1, "mine"), (2, "rs1"), (3, "rs3")]
    # Every row of an imported run moved to the run's new number.
    for table in RUN_TABLES:
        per_run = dict(
            new.con.execute(f'SELECT run_id, count(*) FROM "{table}" GROUP BY 1').fetchall()
        )
        assert per_run[1] == per_run[2] == per_run[3], table
    assert _fill(new, "rs9") == 4
    new.con.close()


@pytest.fixture
def next_layout(monkeypatch):
    """Pretend that this ahd has layout 8, which adds runs.note."""
    version = store_module.SCHEMA_VERSION + 1
    schema = store_module._SCHEMA.replace(
        "date_given BOOLEAN);", "date_given BOOLEAN, note VARCHAR);"
    )
    assert schema != store_module._SCHEMA
    monkeypatch.setattr(store_module, "_SCHEMA", schema)
    monkeypatch.setattr(store_module, "SCHEMA_VERSION", version)
    monkeypatch.setattr(transfer, "SCHEMA_VERSION", version)
    return version


def test_runs_of_an_older_layout_are_imported_by_column_name(tmp_path, source, next_layout):
    path, rows = source
    # The old layout also has a column that the new one dropped.
    con = duckdb.connect(str(path))
    con.execute("ALTER TABLE issues ADD COLUMN old_detail VARCHAR")
    con.close()
    # The new ahd refuses the old file, and its message says how to keep the runs.
    with pytest.raises(SchemaVersionError) as e:
        Store.open(path)
    assert f"ahd import {tmp_path / 'old-old.duckdb'} --db {path}" in str(e.value)

    new = Store.open(tmp_path / "new.duckdb")
    result = import_runs(new, path)
    assert result.version == next_layout - 1 and result.left_out == ["issues.old_detail"]
    got = _rows(new)
    # The new column is empty, and every other value is the same.
    assert [r[-1] for r in got["runs"]] == [None, None]
    assert [r[:-1] for r in got["runs"]] == rows["runs"]
    assert {t: v for t, v in got.items() if t != "runs"} == {
        t: v for t, v in rows.items() if t != "runs"
    }
    new.con.close()


def test_an_export_reads_a_file_that_this_version_refuses(tmp_path, source, next_layout):
    path, rows = source
    out = tmp_path / "backup"
    assert export_runs(path, out)["runs"] == 2
    new = Store.open(tmp_path / "new.duckdb")
    assert len(import_runs(new, out).numbers) == 2
    assert [r[:-1] for r in _rows(new)["runs"]] == rows["runs"]
    new.con.close()


@pytest.mark.parametrize(
    ("version", "words"), [(6, "too old to import"), (99, "newer version of ahd")]
)
def test_a_layout_that_is_too_old_or_too_new_is_refused(tmp_path, source, version, words):
    path, _ = source
    _set_version(path, version)
    out = tmp_path / "backup"
    export_runs(path, out)
    new = Store.open(tmp_path / "new.duckdb")
    for src in (path, out):
        with pytest.raises(StoreError, match=words):
            import_runs(new, src)
    assert _rows(new)["runs"] == []
    new.con.close()


def test_a_failed_import_changes_nothing(tmp_path, source):
    path, _ = source
    out = tmp_path / "backup"
    export_runs(path, out)
    (out / "issues.parquet").write_bytes(b"not a parquet file")
    new = Store.open(tmp_path / "new.duckdb")
    mine = _fill(new, "mine", date(2026, 11, 8))
    before = _rows(new)
    with pytest.raises(StoreError, match="Nothing was changed"):
        import_runs(new, out)
    assert _rows(new) == before and mine == 1
    new.con.close()


def test_sources_that_cannot_be_imported_get_a_plain_error(tmp_path, source):
    path, _ = source
    target = tmp_path / "new.duckdb"
    new = Store.open(target)
    with pytest.raises(StoreError, match="nothing at"):
        import_runs(new, tmp_path / "missing.duckdb")
    with pytest.raises(StoreError, match="is the database that the runs would go into"):
        import_runs(new, target)
    (tmp_path / "empty").mkdir()
    with pytest.raises(StoreError, match="not a folder that ahd export wrote"):
        import_runs(new, tmp_path / "empty")
    (tmp_path / "text.duckdb").write_text("hello")
    with pytest.raises(StoreError, match="could not be read as an ahd database"):
        import_runs(new, tmp_path / "text.duckdb")
    duckdb.connect(str(tmp_path / "other.duckdb")).close()
    with pytest.raises(StoreError, match="holds no ahd runs"):
        import_runs(new, tmp_path / "other.duckdb")
    # A folder whose manifest is there but whose runs file is gone.
    out = tmp_path / "backup"
    export_runs(path, out)
    (out / "runs.parquet").unlink()
    with pytest.raises(StoreError, match="holds no ahd runs"):
        import_runs(new, out)
    assert _rows(new)["runs"] == []
    # The store still works after each refusal.
    assert len(import_runs(new, path).numbers) == 2
    new.con.close()


def test_export_refuses_a_wrong_source_or_a_folder_with_files(tmp_path, source):
    path, _ = source
    with pytest.raises(StoreError, match="There is no database"):
        export_runs(tmp_path / "missing.duckdb", tmp_path / "a")
    duckdb.connect(str(tmp_path / "other.duckdb")).close()
    with pytest.raises(StoreError, match="holds no ahd runs"):
        export_runs(tmp_path / "other.duckdb", tmp_path / "b")
    (tmp_path / "text.duckdb").write_text("hello")
    with pytest.raises(StoreError, match="could not be read as an ahd database"):
        export_runs(tmp_path / "text.duckdb", tmp_path / "c")
    full = tmp_path / "full"
    full.mkdir()
    (full / "keep.txt").write_text("mine")
    with pytest.raises(StoreError, match="already holds files"):
        export_runs(path, full)
    assert [p.name for p in full.iterdir()] == ["keep.txt"]
    with pytest.raises(StoreError, match="could not be made"):
        export_runs(path, full / "keep.txt" / "sub")
    # An empty folder that is already there is fine.
    (tmp_path / "ready").mkdir()
    assert export_runs(path, tmp_path / "ready")["runs"] == 2


def test_the_commands_say_what_they_did(tmp_path, source, capsys):
    path, _ = source
    out, target = tmp_path / "backup", tmp_path / "new.duckdb"
    assert main(["export", "--db", str(path), "--out", str(out)]) == 0
    assert capsys.readouterr().out == f"Wrote 2 runs to {out}.\n"
    s = Store.open(target)
    _fill(s, "mine", date(2026, 11, 8))
    s.con.close()
    assert main(["import", str(out), "--db", str(target)]) == 0
    assert capsys.readouterr().out.splitlines() == [
        f"Imported 2 runs from {out}.",
        "Some run numbers were taken in this database, so the imported runs got new ones:",
        "  Run 1 is now run 2.",
    ]
    assert main(["import", str(path), "--db", str(target)]) == 0
    assert capsys.readouterr().out.splitlines() == [
        f"Imported 0 runs from {path}.",
        "Left out 2 runs that this database already holds.",
    ]
    # The imported runs can be rendered like any other.
    assert main(["render", "--run", "3", "--db", str(target), "--out", str(tmp_path / "r")]) == 0
    assert main(["export", "--db", str(path), "--out", str(out)]) == 1
    assert "already holds files" in capsys.readouterr().err


def test_a_failed_import_leaves_no_new_database_behind(tmp_path, source, capsys):
    path, _ = source
    target = tmp_path / "sub" / "new.duckdb"
    assert main(["import", str(tmp_path / "missing.duckdb"), "--db", str(target)]) == 1
    assert "There is nothing at" in capsys.readouterr().err and not target.exists()
    (tmp_path / "empty").mkdir()
    assert main(["import", str(tmp_path / "empty"), "--db", str(target)]) == 1
    assert "not a folder that ahd export wrote" in capsys.readouterr().err
    assert not target.exists()
    # A database that was there before a failed import is kept.
    assert main(["import", str(path), "--db", str(target)]) == 0
    assert main(["import", str(tmp_path / "empty"), "--db", str(target)]) == 1
    capsys.readouterr()
    assert main(["runs", "--db", str(target)]) == 0
    assert "rs3" in capsys.readouterr().out


def test_a_source_that_another_program_holds_open_is_named(tmp_path, source):
    path, _ = source
    new = Store.open(tmp_path / "new.duckdb")

    def locked(*a, **kw):
        raise duckdb.IOException("IO Error: Could not set lock on file")

    class Con:
        execute = staticmethod(locked)

    with pytest.raises(StoreError, match="in use by another program"):
        transfer._file_source(Con(), path)
    new.con.close()


def test_import_names_the_columns_it_left_out(tmp_path, source, capsys):
    path, _ = source
    con = duckdb.connect(str(path))
    con.execute("ALTER TABLE issues ADD COLUMN old_detail VARCHAR")
    con.close()
    assert main(["import", str(path), "--db", str(tmp_path / "new.duckdb")]) == 0
    assert "were left out: issues.old_detail." in capsys.readouterr().out


def test_an_imported_run_gives_the_same_report(tmp_path):
    """The report of a run is the same before and after the run moves to another file."""
    import importlib.util
    from pathlib import Path

    from ahd.render.html import render_html
    from ahd.summary import build_view

    script = Path(__file__).resolve().parents[1] / "scripts/make_showcase.py"
    spec = importlib.util.spec_from_file_location("make_showcase", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    old, rid = mod.build_store(tmp_path / "showcase.duckdb")
    before = render_html(build_view(old, rid))
    old.con.close()
    export_runs(tmp_path / "showcase.duckdb", tmp_path / "backup")
    for name, src in (("file", tmp_path / "showcase.duckdb"), ("folder", tmp_path / "backup")):
        new = Store.open(tmp_path / f"{name}.duckdb")
        import_runs(new, src)
        assert render_html(build_view(new, rid)) == before, name
        new.con.close()


def test_an_import_with_new_numbers_keeps_the_latest_run_and_the_run_before(tmp_path):
    """Old runs that got higher numbers are not the latest run, and not the run before."""
    from ahd.catalog import CUSTOM_GROUPS
    from ahd.summary import component_drops

    def run(store, today, events):
        rid = store.create_run("rs1", "Name", None, today, "UTC", "0.1")
        store.add_components(
            rid,
            [
                Component(
                    f"metrics/event{i}",
                    f"E{i}",
                    "metric",
                    CUSTOM_GROUPS[0],
                    i,
                    Column(f"metrics/event{i}", f"metrics/event{i}"),
                    None,
                )
                for i in range(1, events + 1)
            ],
        )
        store.finish_run(rid, "ok")
        return rid

    old = Store.open(tmp_path / "old.duckdb")
    run(old, date(2026, 10, 15), 10)
    run(old, date(2026, 10, 16), 10)
    old.con.close()
    new = Store.open(tmp_path / "new.duckdb")
    yesterday = run(new, date(2026, 11, 8), 5)
    result = import_runs(new, tmp_path / "old.duckdb")
    assert result.renumbered == [(1, 2), (2, 3)]
    assert new.con.execute("SELECT run_id FROM latest_runs").fetchall() == [(yesterday,)]
    today = run(new, date(2026, 11, 9), 5)
    assert new.con.execute("SELECT run_id FROM latest_runs").fetchall() == [(today,)]
    # The run of the day before saw the same 5 events, so there is nothing to warn about.
    assert new.previous_run(today) == yesterday
    assert component_drops(new, today) == []
    new.con.close()
