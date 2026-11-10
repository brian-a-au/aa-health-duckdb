"""Move stored runs between database files: `ahd export` and `ahd import`.

This is how runs survive a change to the tables. ahd has no upgrade code for a file, on purpose.
A new layout starts a new file, and `ahd import` copies the runs of the old file into it.

Import copies each table by column name. A column that only the new layout has stays empty,
and a column that only the old layout had is left out. So a layout change that only adds or
removes columns needs no code here. Any other change, e.g., a column whose meaning changed,
needs a conversion in this module, or a higher IMPORT_FROM_VERSION in store.py.

A source is a database file of any layout from IMPORT_FROM_VERSION on, or a folder that
`ahd export` wrote: one Parquet file for each table and a manifest.json.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb

from ahd.store import (
    IMPORT_FROM_VERSION,
    LOCKED_WORDS,
    RUN_TABLES,
    SCHEMA_VERSION,
    Store,
    StoreError,
)

MANIFEST = "manifest.json"
# The version of the export folder's own form: the file names and the manifest's keys.
EXPORT_FORMAT = 1
# The columns that tell whether two files hold the same run.
SAME_RUN = ("rsid", "segment_id", "today", "started_at")


@dataclass
class ImportResult:
    """What an import did. numbers pairs each run's number in the source with its new number."""

    version: int
    numbers: list[tuple[int, int]] = field(default_factory=list)
    already_there: int = 0
    # Columns of the source that this layout no longer has, as "table.column".
    left_out: list[str] = field(default_factory=list)

    @property
    def renumbered(self) -> list[tuple[int, int]]:
        return [(old, new) for old, new in self.numbers if old != new]


def _text(value: str) -> str:
    """A SQL string. DuckDB takes no parameter for a file name in ATTACH and COPY."""
    return "'" + value.replace("'", "''") + "'"


def _name(value: str) -> str:
    """A SQL name in quotes, e.g., for the column "group"."""
    return '"' + value.replace('"', '""') + '"'


def _columns(con: duckdb.DuckDBPyConnection, relation: str) -> list[str]:
    return [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {relation}").fetchall()]


def _open_error(path: Path, e: duckdb.Error) -> StoreError:
    text = str(e).lower()
    if any(words in text for words in LOCKED_WORDS) or "file handle conflict" in text:
        return StoreError(
            f"The database {path} is in use by another program, or it is the file that --db "
            "names. Close that program, or choose another file, then try again."
        )
    return StoreError(f"The file {path} could not be read as an ahd database.")


def _check_version(found: Any, source: Path) -> int:
    if not isinstance(found, int):
        raise StoreError(f"{source} holds no ahd runs.")
    if found > SCHEMA_VERSION:
        raise StoreError(
            f"{source} was made by a newer version of ahd. Update ahd, then try again."
        )
    if found < IMPORT_FROM_VERSION:
        raise StoreError(
            f"{source} was made by a version of ahd that is too old to import. This version "
            f"reads layout {IMPORT_FROM_VERSION} and newer, and the file has layout {found}."
        )
    return found


def export_runs(db: Path, out_dir: Path) -> dict[str, int]:
    """Write every table of the file to out_dir as Parquet, and return the rows of each.

    It reads a file of any layout, also one that this version of ahd refuses to open, so that
    the runs of an old file can always be taken out.
    """
    db, out_dir = Path(db), Path(out_dir)
    if not db.is_file():
        raise StoreError(f"There is no database at {db}. Choose the file with --db.")
    if out_dir.exists() and (not out_dir.is_dir() or any(out_dir.iterdir())):
        raise StoreError(
            f"{out_dir} already holds files. Choose a new or an empty folder with --out."
        )
    try:
        con = duckdb.connect(str(db), read_only=True)
    except duckdb.Error as e:
        raise _open_error(db, e) from None
    try:
        tables = {
            r[0]
            for r in con.execute(
                "SELECT table_name FROM information_schema.tables"
                " WHERE table_schema = 'main' AND table_type = 'BASE TABLE'"
            ).fetchall()
        }
        if "schema_version" not in tables or "runs" not in tables:
            raise StoreError(f"{db} holds no ahd runs.")
        row = con.execute("SELECT max(version) FROM schema_version").fetchone()
        version = row[0] if row else None
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise StoreError(
                f"The folder {out_dir} could not be made ({e.strerror or type(e).__name__})."
            ) from None
        counts: dict[str, int] = {}
        for table in RUN_TABLES:
            if table not in tables:
                continue
            target = out_dir / f"{table}.parquet"
            con.execute(
                f"COPY (SELECT * FROM {_name(table)} ORDER BY run_id) TO {_text(str(target))}"
                " (FORMAT parquet)"
            )
            found = con.execute(f"SELECT count(*) FROM {_name(table)}").fetchone()
            counts[table] = found[0] if found else 0
    finally:
        con.close()
    manifest = {"format": EXPORT_FORMAT, "schema_version": version, "tables": counts}
    (out_dir / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return counts


def _folder_source(source: Path) -> tuple[int, dict[str, str]]:
    """The layout version of an export folder, and a relation for each table it holds."""
    try:
        manifest = json.loads((source / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = None
    if not isinstance(manifest, dict) or manifest.get("format") != EXPORT_FORMAT:
        raise StoreError(
            f"{source} is not a folder that ahd export wrote. It has no {MANIFEST} that this "
            "version of ahd can read."
        )
    version = _check_version(manifest.get("schema_version"), source)
    relations = {
        table: f"read_parquet({_text(str(source / f'{table}.parquet'))})"
        for table in RUN_TABLES
        if (source / f"{table}.parquet").is_file()
    }
    return version, relations


def _file_source(con: duckdb.DuckDBPyConnection, source: Path) -> tuple[int, dict[str, str]]:
    """Attach a database file as "src". The caller detaches it."""
    try:
        con.execute(f"ATTACH {_text(str(source))} AS src (READ_ONLY)")
    except duckdb.Error as e:
        raise _open_error(source, e) from None
    tables = {
        r[0]
        for r in con.execute(
            "SELECT table_name FROM information_schema.tables"
            " WHERE table_catalog = 'src' AND table_schema = 'main'"
            " AND table_type = 'BASE TABLE'"
        ).fetchall()
    }
    found = None
    if "schema_version" in tables:
        row = con.execute("SELECT max(version) FROM src.main.schema_version").fetchone()
        found = row[0] if row else None
    version = _check_version(found, source)
    return version, {t: f"src.main.{_name(t)}" for t in RUN_TABLES if t in tables}


def import_runs(store: Store, source: Path) -> ImportResult:
    """Copy the runs of another file, or of an export folder, into this store.

    A run that the store already holds is left out, so the same source can be imported twice.
    The runs keep their numbers when none of them is taken in this store. Else every imported
    run gets the next free number. It all happens in one transaction.
    """
    source = Path(source)
    if not source.exists():
        raise StoreError(f"There is nothing at {source} to import.")
    con = store.con
    attached = source.is_file()
    try:
        if attached:
            version, relations = _file_source(con, source)
        else:
            version, relations = _folder_source(source)
        if "runs" not in relations:
            raise StoreError(f"{source} holds no ahd runs.")
        return _copy(store, version, relations)
    except duckdb.Error as e:
        # e.g. a Parquet file that is cut off, or a column that cannot take the old value.
        first = (str(e).splitlines() or [""])[0]
        raise StoreError(
            f"{source} could not be imported ({type(e).__name__}). {first} Nothing was changed."
        ) from None
    finally:
        if attached:
            try:
                con.execute("DETACH src")
            except duckdb.Error:
                pass  # The attach itself failed, so there is nothing to detach.


def _copy(store: Store, version: int, relations: dict[str, str]) -> ImportResult:
    con = store.con
    result = ImportResult(version)
    same = ", ".join(SAME_RUN)
    have = {tuple(r) for r in con.execute(f"SELECT {same} FROM runs").fetchall()}
    taken = {r[0] for r in con.execute("SELECT run_id FROM runs").fetchall()}
    wanted = []
    for row in con.execute(
        f"SELECT run_id, {same} FROM {relations['runs']} ORDER BY run_id"
    ).fetchall():
        if tuple(row[1:]) in have:
            result.already_there += 1
        else:
            wanted.append(row[0])
    if not wanted:
        return result

    con.execute("BEGIN TRANSACTION")
    try:
        if taken.intersection(wanted):
            for old in wanted:
                new = con.execute("SELECT nextval('run_seq')").fetchone()
                result.numbers.append((old, new[0] if new else old))
        else:
            result.numbers = [(old, old) for old in wanted]
            _move_sequence_past(con, max(wanted))
        con.execute("CREATE OR REPLACE TEMP TABLE ahd_import_numbers (old INTEGER, new INTEGER)")
        con.execute(
            "INSERT INTO ahd_import_numbers SELECT unnest(?::INTEGER[]), unnest(?::INTEGER[])",
            [[old for old, _ in result.numbers], [new for _, new in result.numbers]],
        )
        for table in RUN_TABLES:
            if table not in relations:
                continue
            ours = _columns(con, _name(table))
            theirs = _columns(con, relations[table])
            result.left_out += [f"{table}.{c}" for c in theirs if c not in ours]
            shared = [c for c in ours if c in theirs and c != "run_id"]
            names = ", ".join(_name(c) for c in shared)
            picks = ", ".join(f"s.{_name(c)}" for c in shared)
            con.execute(
                f"INSERT INTO {_name(table)} (run_id{', ' if shared else ''}{names})"
                f" SELECT n.new{', ' if shared else ''}{picks}"
                f" FROM {relations[table]} s JOIN ahd_import_numbers n ON n.old = s.run_id"
            )
        con.execute("DROP TABLE ahd_import_numbers")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    con.execute("COMMIT")
    con.execute("CHECKPOINT")
    return result


def _move_sequence_past(con: duckdb.DuckDBPyConnection, number: int) -> None:
    """Make the next new run get a number above this one. A sequence only moves forward."""
    row = con.execute(
        "SELECT last_value FROM duckdb_sequences() WHERE sequence_name = 'run_seq'"
    ).fetchone()
    last = (row[0] if row else None) or 0
    if number > last:
        con.execute("SELECT max(nextval('run_seq')) FROM range(?)", [number - last])
