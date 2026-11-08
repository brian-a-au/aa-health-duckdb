"""DuckDB store: every run is kept, keyed by run_id."""

from __future__ import annotations

from datetime import date, datetime
from importlib import resources
from pathlib import Path
from typing import Literal

import duckdb

from ahd.api.reports import DailyValue
from ahd.catalog import Component

# Bump when the tables change. ahd does not upgrade files: a file with another version is
# refused, and you start a new file.
SCHEMA_VERSION = 4

_SCHEMA = """
CREATE TABLE schema_version (version INTEGER);
CREATE SEQUENCE run_seq START 1;
CREATE TABLE runs (
    run_id INTEGER PRIMARY KEY, rsid VARCHAR, rsid_name VARCHAR, segment_id VARCHAR,
    today DATE, timezone VARCHAR, tool_version VARCHAR, status VARCHAR,
    started_at TIMESTAMP, finished_at TIMESTAMP, currency VARCHAR);
-- The Individual Component Summary pairs of a run: one dimension by one metric each.
-- Pair 1 is fetched by ahd run; ahd individual adds more.
CREATE TABLE pairs (
    run_id INTEGER, pair INTEGER, dimension VARCHAR, metric VARCHAR, dimension_name VARCHAR,
    metric_name VARCHAR, metric_type VARCHAR, added_at TIMESTAMP);
CREATE TABLE components (
    run_id INTEGER, component_id VARCHAR, name VARCHAR, kind VARCHAR, "group" VARCHAR,
    number INTEGER, metric_id VARCHAR, exists_dimension VARCHAR, allowed_for_reporting BOOLEAN,
    metric_type VARCHAR);
CREATE TABLE daily_values (
    run_id INTEGER, component_id VARCHAR, day DATE, value DOUBLE,
    expected DOUBLE, upper DOUBLE, lower DOUBLE, anomaly BOOLEAN);
CREATE TABLE top_items (
    run_id INTEGER, pair INTEGER, period VARCHAR, rank INTEGER, item VARCHAR, value DOUBLE);
CREATE TABLE breakdowns (
    run_id INTEGER, pair INTEGER, kind VARCHAR, col_index INTEGER, label VARCHAR,
    rank INTEGER, item VARCHAR, value DOUBLE);
CREATE TABLE breakdown_totals (
    run_id INTEGER, pair INTEGER, kind VARCHAR, col_index INTEGER, label VARCHAR, total DOUBLE);
CREATE TABLE issues (
    run_id INTEGER, scope VARCHAR, component_id VARCHAR, code VARCHAR, message VARCHAR);
"""


# Every table whose rows belong to a run. `runs` goes last.
RUN_TABLES = (
    "pairs",
    "components",
    "daily_values",
    "top_items",
    "breakdowns",
    "breakdown_totals",
    "issues",
    "runs",
)


def _views() -> str:
    """The views and the alerts macro from ahd/sql/views.sql."""
    return resources.files("ahd").joinpath("sql/views.sql").read_text()


class SchemaVersionError(ValueError):
    """The database file was written by a different version of ahd."""


class StoreError(Exception):
    """The database file could not be opened. The message says why in plain words."""


def _connect(path: Path, read_only: bool) -> duckdb.DuckDBPyConnection:
    if read_only and not path.exists():
        raise StoreError(
            f"There is no database at {path}. Make a run first, or choose the file with --db."
        )
    try:
        return duckdb.connect(str(path), read_only=read_only)
    except duckdb.Error as e:
        text = str(e)
        if "lock" in text.lower():
            raise StoreError(
                f"The database {path} is in use by another program, e.g., a DuckDB session or "
                "another ahd command that writes to it. Close that program or wait for it to "
                "finish, then try again."
            ) from None
        if "not a valid DuckDB database" in text:
            raise StoreError(
                f"The file {path} is not a DuckDB database. Choose another file with --db."
            ) from None
        raise StoreError(f"Could not open the database {path} ({type(e).__name__}).") from None


class Store:
    def __init__(self, con: duckdb.DuckDBPyConnection) -> None:
        self.con = con

    @classmethod
    def open(cls, path: Path, read_only: bool = False) -> Store:
        """Open or create the database. read_only opens an existing file without a write lock,
        so it can be read while a DuckDB session also has it open read-only."""
        path = Path(path)
        if not read_only:
            path.parent.mkdir(parents=True, exist_ok=True)
        con = _connect(path, read_only)
        try:
            tables = {
                r[0]
                for r in con.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
                ).fetchall()
            }
            if not tables and read_only:
                raise StoreError(f"The database {path} holds no ahd runs.")
            if not tables:
                con.execute(_SCHEMA)
                con.execute(_views())
                con.execute("INSERT INTO schema_version (version) VALUES (?)", [SCHEMA_VERSION])
            else:
                found = None
                if "schema_version" in tables:
                    found = con.execute("SELECT max(version) FROM schema_version").fetchone()[0]
                if found != SCHEMA_VERSION:
                    raise SchemaVersionError(
                        f"The database {path} was made by a different version of ahd. "
                        "Delete the file to start again, or choose another file with --db."
                    )
        except BaseException:
            con.close()
            raise
        return cls(con)

    def _many(self, sql: str, rows: list[tuple]) -> None:
        if rows:
            self.con.executemany(sql, rows)

    def create_run(
        self,
        rsid: str,
        rsid_name: str,
        segment_id: str | None,
        today: date,
        timezone: str,
        tool_version: str,
        currency: str | None = None,
    ) -> int:
        row = self.con.execute(
            "INSERT INTO runs (run_id, rsid, rsid_name, segment_id, today, timezone,"
            " tool_version, status, started_at, finished_at, currency)"
            " VALUES (nextval('run_seq'), ?, ?, ?, ?, ?, ?, 'running', ?, NULL, ?)"
            " RETURNING run_id",
            [
                rsid,
                rsid_name,
                segment_id,
                today,
                timezone,
                tool_version,
                datetime.now(),
                currency,
            ],
        ).fetchone()
        return row[0]

    def set_status(self, run_id: int, status: str) -> None:
        """Change only the status, e.g. when a pair added later had problems."""
        self.con.execute("UPDATE runs SET status = ? WHERE run_id = ?", [status, run_id])

    def add_pair(
        self,
        run_id: int,
        dimension: str,
        metric: str,
        dimension_name: str | None = None,
        metric_name: str | None = None,
        metric_type: str | None = None,
    ) -> int:
        """Store a pair for the run and return its number: 1 for the first, then 2, 3 and so on."""
        row = self.con.execute(
            "INSERT INTO pairs (run_id, pair, dimension, metric, dimension_name, metric_name,"
            " metric_type, added_at)"
            " SELECT ?, coalesce(max(pair), 0) + 1, ?, ?, ?, ?, ?, ? FROM pairs WHERE run_id = ?"
            " RETURNING pair",
            [
                run_id,
                dimension,
                metric,
                dimension_name,
                metric_name,
                metric_type,
                datetime.now(),
                run_id,
            ],
        ).fetchone()
        return row[0]

    def list_pairs(self, run_id: int) -> list[dict]:
        cur = self.con.execute("SELECT * FROM pairs WHERE run_id = ? ORDER BY pair", [run_id])
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def finish_run(self, run_id: int, status: Literal["ok", "partial", "failed"]) -> None:
        self.con.execute(
            "UPDATE runs SET status = ?, finished_at = ? WHERE run_id = ?",
            [status, datetime.now(), run_id],
        )

    def add_components(self, run_id: int, components: list[Component]) -> None:
        self._many(
            'INSERT INTO components (run_id, component_id, name, kind, "group", number,'
            " metric_id, exists_dimension, allowed_for_reporting, metric_type)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    run_id,
                    c.id,
                    c.name,
                    c.kind,
                    c.group,
                    c.number,
                    c.column.metric_id,
                    c.column.exists_dimension,
                    c.allowed_for_reporting,
                    c.metric_type,
                )
                for c in components
            ],
        )

    def add_daily_values(self, run_id: int, values: list[DailyValue]) -> None:
        self._many(
            "INSERT INTO daily_values (run_id, component_id, day, value, expected, upper,"
            " lower, anomaly) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (run_id, v.component_id, v.day, v.value, v.expected, v.upper, v.lower, v.anomaly)
                for v in values
            ],
        )

    def add_top_items(
        self,
        run_id: int,
        period: Literal["current", "historical"],
        rows: list[tuple[int, str, float]],
        pair: int = 1,
    ) -> None:
        self._many(
            "INSERT INTO top_items (run_id, pair, period, rank, item, value)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [(run_id, pair, period, r, item, v) for r, item, v in rows],
        )

    def add_breakdown(
        self,
        run_id: int,
        kind: Literal["browser", "device"],
        labels: list[str],
        rows: list[tuple[str, list[float]]],
        totals: list[float],
        pair: int = 1,
    ) -> None:
        self._many(
            "INSERT INTO breakdowns (run_id, pair, kind, col_index, label, rank, item, value)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (run_id, pair, kind, i, label, rank, item, vals[i])
                for rank, (item, vals) in enumerate(rows, start=1)
                for i, label in enumerate(labels)
                if i < len(vals)
            ],
        )
        self._many(
            "INSERT INTO breakdown_totals (run_id, pair, kind, col_index, label, total)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [
                (run_id, pair, kind, i, label, totals[i])
                for i, label in enumerate(labels)
                if i < len(totals)
            ],
        )

    def add_issue(
        self, run_id: int, scope: str, component_id: str | None, code: str, message: str
    ) -> None:
        self.con.execute(
            "INSERT INTO issues (run_id, scope, component_id, code, message)"
            " VALUES (?, ?, ?, ?, ?)",
            [run_id, scope, component_id, code, message],
        )

    def list_runs(self) -> list[dict]:
        cur = self.con.execute("SELECT * FROM runs ORDER BY run_id DESC")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def get_run(self, run_id: int) -> dict:
        cur = self.con.execute("SELECT * FROM runs WHERE run_id = ?", [run_id])
        cols = [d[0] for d in cur.description]
        row = cur.fetchone()
        if row is None:
            raise KeyError(f"no run {run_id}")
        return dict(zip(cols, row))

    def runs_to_prune(self, run_ids: list[int], before: date | None) -> list[dict]:
        """The stored runs that match any given run number, or whose run date is before `before`."""
        if not run_ids and before is None:
            return []
        cur = self.con.execute(
            "SELECT * FROM runs WHERE list_contains(?::INTEGER[], run_id)"
            " OR (?::DATE IS NOT NULL AND today < ?::DATE) ORDER BY run_id",
            [list(run_ids), before, before],
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def delete_runs(self, run_ids: list[int]) -> None:
        """Delete these runs from every table, all or nothing."""
        if not run_ids:
            return
        ids = list(run_ids)
        self.con.execute("BEGIN TRANSACTION")
        try:
            for table in RUN_TABLES:
                self.con.execute(
                    f"DELETE FROM {table} WHERE list_contains(?::INTEGER[], run_id)", [ids]
                )
        except BaseException:
            self.con.execute("ROLLBACK")
            raise
        self.con.execute("COMMIT")
        # Write the change to the file now, so the space can be reused.
        self.con.execute("CHECKPOINT")

    def delete_pair(self, run_id: int, pair: int, series_id: str) -> None:
        """Remove one pair and every row stored for it, all or nothing.

        Used only when ahd individual stops part way, so that the same pair can be tried again.
        series_id is the component_id of the pair's daily trend, e.g. "individual/2".
        """
        self.con.execute("BEGIN TRANSACTION")
        try:
            for table in ("pairs", "top_items", "breakdowns", "breakdown_totals"):
                self.con.execute(
                    f"DELETE FROM {table} WHERE run_id = ? AND pair = ?", [run_id, pair]
                )
            for table in ("daily_values", "issues"):
                self.con.execute(
                    f"DELETE FROM {table} WHERE run_id = ? AND component_id = ?",
                    [run_id, series_id],
                )
        except BaseException:
            self.con.execute("ROLLBACK")
            raise
        self.con.execute("COMMIT")

    def component_counts(self, run_id: int, groups: list[str]) -> dict[str, int]:
        """How many components of each group the run stored."""
        rows = self.con.execute(
            'SELECT "group", count(*) FROM components WHERE run_id = ?'
            ' AND list_contains(?::VARCHAR[], "group") GROUP BY "group"',
            [run_id, list(groups)],
        ).fetchall()
        found = dict(rows)
        return {g: found.get(g, 0) for g in groups}

    def previous_run(self, run_id: int) -> int | None:
        """The latest earlier run of the same report suite that stored components."""
        row = self.con.execute(
            "SELECT max(p.run_id) FROM runs p JOIN runs r ON r.run_id = ? AND p.rsid = r.rsid"
            " WHERE p.run_id < r.run_id"
            " AND EXISTS (SELECT 1 FROM components c WHERE c.run_id = p.run_id)",
            [run_id],
        ).fetchone()
        return row[0] if row else None
