"""DuckDB store: every run is kept, keyed by run_id."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Literal

import duckdb

from ahd.api.reports import DailyValue
from ahd.catalog import Component

_SCHEMA = """
CREATE SEQUENCE IF NOT EXISTS run_seq START 1;
CREATE TABLE IF NOT EXISTS runs (
    run_id INTEGER PRIMARY KEY, rsid VARCHAR, rsid_name VARCHAR, segment_id VARCHAR,
    today DATE, timezone VARCHAR, tool_version VARCHAR, status VARCHAR,
    started_at TIMESTAMP, finished_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS components (
    run_id INTEGER, component_id VARCHAR, name VARCHAR, kind VARCHAR, "group" VARCHAR,
    number INTEGER, metric_id VARCHAR, exists_dimension VARCHAR, allowed_for_reporting BOOLEAN);
CREATE TABLE IF NOT EXISTS daily_values (
    run_id INTEGER, component_id VARCHAR, day DATE, value DOUBLE,
    expected DOUBLE, upper DOUBLE, lower DOUBLE, anomaly BOOLEAN);
CREATE TABLE IF NOT EXISTS top_items (
    run_id INTEGER, period VARCHAR, rank INTEGER, item VARCHAR, value DOUBLE);
CREATE TABLE IF NOT EXISTS breakdowns (
    run_id INTEGER, kind VARCHAR, col_index INTEGER, label VARCHAR,
    rank INTEGER, item VARCHAR, value DOUBLE);
CREATE TABLE IF NOT EXISTS breakdown_totals (
    run_id INTEGER, kind VARCHAR, col_index INTEGER, label VARCHAR, total DOUBLE);
CREATE TABLE IF NOT EXISTS issues (
    run_id INTEGER, scope VARCHAR, component_id VARCHAR, code VARCHAR, message VARCHAR);
"""


class Store:
    def __init__(self, con: duckdb.DuckDBPyConnection) -> None:
        self.con = con

    @classmethod
    def open(cls, path: Path) -> Store:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        con = duckdb.connect(str(path))
        con.execute(_SCHEMA)
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
    ) -> int:
        row = self.con.execute(
            "INSERT INTO runs VALUES (nextval('run_seq'), ?, ?, ?, ?, ?, ?, 'running', ?, NULL)"
            " RETURNING run_id",
            [rsid, rsid_name, segment_id, today, timezone, tool_version, datetime.now()],
        ).fetchone()
        return row[0]

    def finish_run(self, run_id: int, status: Literal["ok", "partial", "failed"]) -> None:
        self.con.execute(
            "UPDATE runs SET status = ?, finished_at = ? WHERE run_id = ?",
            [status, datetime.now(), run_id],
        )

    def add_components(self, run_id: int, components: list[Component]) -> None:
        self._many(
            "INSERT INTO components VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (run_id, c.id, c.name, c.kind, c.group, c.number, c.column.metric_id,
                 c.column.exists_dimension, c.allowed_for_reporting)
                for c in components
            ],
        )

    def add_daily_values(self, run_id: int, values: list[DailyValue]) -> None:
        self._many(
            "INSERT INTO daily_values VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (run_id, v.component_id, v.day, v.value, v.expected, v.upper, v.lower, v.anomaly)
                for v in values
            ],
        )

    def add_top_items(
        self, run_id: int, period: Literal["current", "historical"],
        rows: list[tuple[int, str, float]],
    ) -> None:
        self._many(
            "INSERT INTO top_items VALUES (?, ?, ?, ?, ?)",
            [(run_id, period, r, item, v) for r, item, v in rows],
        )

    def add_breakdown(
        self, run_id: int, kind: Literal["browser", "device"], labels: list[str],
        rows: list[tuple[str, list[float]]], totals: list[float],
    ) -> None:
        self._many(
            "INSERT INTO breakdowns VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (run_id, kind, i, label, rank, item, vals[i])
                for rank, (item, vals) in enumerate(rows, start=1)
                for i, label in enumerate(labels)
                if i < len(vals)
            ],
        )
        self._many(
            "INSERT INTO breakdown_totals VALUES (?, ?, ?, ?, ?)",
            [(run_id, kind, i, label, totals[i]) for i, label in enumerate(labels) if i < len(totals)],
        )

    def add_issue(
        self, run_id: int, scope: str, component_id: str | None, code: str, message: str
    ) -> None:
        self.con.execute(
            "INSERT INTO issues VALUES (?, ?, ?, ?, ?)",
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
