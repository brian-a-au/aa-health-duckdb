"""A live check of `ahd run` on one report suite. Run it by hand, and never in CI.

It runs `ahd run` into a temporary folder outside the repository. It prints the order of the
GET requests that the run made, and checks key columns of the stored run for NULL. It wraps the
client after the sign-in, so it sees only the path and the parameters of each request. It never
sees a credential, a token or a header.

    uv run python scripts/live_check.py --profile NAME --rsid SUITE

It exits with 0 when it finds no problem, 1 when it finds one, and 2 when CI is set.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ahd.api.client import AdobeClient
from ahd.cli import main
from ahd.store import connect
from ahd.text import inert

# The columns of the components table that must hold a value for every component of a run.
KEY_COLUMNS = ("name", "kind", "allowed_for_reporting")


class Recorder:
    """Passes each call to the real client, and keeps the path, parameters and answer keys."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self._lock = threading.Lock()
        self.gets: list[tuple[str, dict, set[str]]] = []
        self.posts = 0

    def get(self, path: str, params: dict | None = None) -> Any:
        answer = self._client.get(path, params)
        keys: set[str] = set()
        if isinstance(answer, list):
            for row in answer:
                if isinstance(row, dict):
                    keys.update(row)
        with self._lock:
            self.gets.append((path, dict(params or {}), keys))
        return answer

    def post_report(self, body: dict) -> dict:
        # Worker threads call this.
        with self._lock:
            self.posts += 1
        return self._client.post_report(body)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


def print_requests(rec: Recorder) -> None:
    print("GET requests in the order of the run:")
    for path, params, keys in rec.gets:
        expansion = params.get("expansion") or "(none)"
        print(f"  {inert(path)}  expansion={inert(expansion)}")
        if path in ("/dimensions", "/metrics"):
            print(f"    allowedForReporting in the answer: {'allowedForReporting' in keys}")
    print(f"POST /reports: {rec.posts}")


def check_db(path: Path) -> list[str]:
    """One line for each problem in the last run of the file. No line means no problem."""
    con = connect(path, read_only=True)
    try:
        row = con.execute("SELECT run_id, status FROM runs ORDER BY run_id DESC LIMIT 1").fetchone()
        if row is None:
            return ["The file holds no run."]
        run_id, status = row
        problems = [] if status in ("ok", "partial") else [f"The run is {status}."]
        for column in KEY_COLUMNS:
            # The column names are the constants above, never input.
            rows = con.execute(
                f'SELECT "group", count(*) FILTER ({column} IS NULL), count(*)'
                " FROM components WHERE run_id = ? GROUP BY ALL ORDER BY 1",
                [run_id],
            ).fetchall()
            for group, nulls, total in rows:
                if nulls:
                    problems.append(f"{column} is NULL for {nulls} of {total} in {group}.")
        values = con.execute(
            "SELECT count(value) FROM daily_values WHERE run_id = ?", [run_id]
        ).fetchone()
        if not values or not values[0]:
            problems.append("The run stored no daily values.")
        return problems
    finally:
        con.close()


def _count(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def checked(path: Path) -> str:
    """What the check looked at in the last run of the file, as counts for a pull request."""
    con = connect(path, read_only=True)
    try:
        row = con.execute(
            'SELECT count(*), count(DISTINCT "group") FROM components'
            " WHERE run_id = (SELECT max(run_id) FROM runs)"
        ).fetchone()
    finally:
        con.close()
    components, groups = row if row else (0, 0)
    return f"Checked {_count(components, 'component')} in {_count(groups, 'group')}."


def run(argv: list[str], connect: Callable[..., Any] = AdobeClient.connect) -> int:
    """Run `ahd run` with argv into a temporary folder, print the requests and the problems."""
    recorders: list[Recorder] = []

    def factory(creds: Any, company_id: str | None = None) -> Recorder:
        recorders.append(Recorder(connect(creds, company_id)))
        return recorders[-1]

    with tempfile.TemporaryDirectory(prefix="ahd-live-") as tmp:
        db = Path(tmp) / "live.duckdb"
        code = main(
            ["run", *argv, "--db", str(db), "--out", str(Path(tmp) / "out")],
            client_factory=factory,
        )
        print(f"ahd run exited with {code}.")
        for rec in recorders:
            print_requests(rec)
        if db.exists():
            print(checked(db))
            problems = check_db(db)
        else:
            problems = ["The run stored nothing."]
    print("The script has deleted its temporary folder, with the database and the report.")
    for line in problems:
        print(line)
    if not problems:
        print("No problem found.")
    else:
        print("1 problem found." if len(problems) == 1 else f"{len(problems)} problems found.")
    return 1 if problems else 0


def cli(argv: list[str] | None = None) -> int:
    if os.environ.get("CI"):
        print("This script makes live requests to Adobe. It does not run in CI.", file=sys.stderr)
        return 2
    p = argparse.ArgumentParser(description="Run ahd run on one suite and check the stored run.")
    p.add_argument("--profile", metavar="NAME", help="The profile with the credential.")
    p.add_argument("--config", metavar="PATH", help="A config file with the credential.")
    p.add_argument("--rsid", required=True, help="One report suite ID.")
    p.add_argument("--date", help="The day to check, e.g., 2026-11-24. The default is today.")
    args = p.parse_args(argv)
    passed = ["--rsid", args.rsid]
    for option in ("profile", "config", "date"):
        if getattr(args, option):
            passed += [f"--{option}", getattr(args, option)]
    return run(passed)


if __name__ == "__main__":
    sys.exit(cli())
