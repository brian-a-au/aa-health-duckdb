"""Check that an installed ahd has its package data, without a credential or the network.

The release workflow runs it with the Python of the built wheel, from outside the repository:

    "$RUNNER_TEMP/try/bin/python" -I scripts/check_wheel.py "$RUNNER_TEMP/smoke"

It makes a database with one run of synthetic data in FOLDER, which reads sql/views.sql. Then it
runs the installed ahd program: ahd runs, ahd alerts, which uses the alerts macro, and
ahd render, which uses the template and the files in render/static. It stops with an error
when a step fails or when the page lacks the CSS or the JavaScript of the package.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import sysconfig
from datetime import date, timedelta
from importlib import resources
from pathlib import Path

from ahd.api.reports import Column, DailyValue
from ahd.catalog import Component
from ahd.store import Store

TODAY = date(2026, 11, 7)


def make_store(path: Path) -> int:
    """A database with one finished run, whose one metric drops to a fifth on the last day."""
    store = Store.open(path)
    rid = store.create_run("rs1", "Check suite", None, TODAY, "UTC", "0.1")
    cid = "metrics/event1"
    store.add_components(
        rid, [Component(cid, "Orders", "metric", "custom_events", 1, Column(cid, cid), None)]
    )
    start = TODAY - timedelta(days=91)
    values = [
        DailyValue(cid, start + timedelta(days=n), 1000.0, None, None, None, None)
        for n in range(90)
    ]
    values.append(DailyValue(cid, TODAY - timedelta(days=1), 200.0, None, None, None, None))
    store.add_daily_values(rid, values)
    store.finish_run(rid, "ok")
    store.con.close()
    return rid


def ahd(*args: str) -> str:
    """Runs the ahd program next to this Python and gives its output."""
    program = shutil.which("ahd", path=sysconfig.get_path("scripts"))
    if program is None:
        raise SystemExit("There is no ahd program next to this Python.")
    done = subprocess.run(
        [program, *args], capture_output=True, text=True, encoding="utf-8", check=False
    )
    if done.returncode != 0:
        raise SystemExit(f"ahd {args[0]} exited with {done.returncode}:\n{done.stderr}")
    return done.stdout


def main(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    db = folder / "check.duckdb"
    db.unlink(missing_ok=True)
    run = str(make_store(db))
    if "rs1" not in ahd("runs", f"--db={db}"):
        raise SystemExit("ahd runs did not list the run.")
    if "large drop: Orders" not in ahd("alerts", f"--db={db}", f"--run={run}"):
        raise SystemExit("ahd alerts did not find the large drop.")
    page = Path(ahd("render", f"--db={db}", f"--run={run}", f"--out={folder}").strip())
    html = page.read_text(encoding="utf-8")
    static = resources.files("ahd.render").joinpath("static")
    for name in ("report.css", "report.js"):
        if static.joinpath(name).read_text(encoding="utf-8") not in html:
            raise SystemExit(f"The page lacks {name}.")
    if "Orders" not in html:
        raise SystemExit("The page lacks the component of the run.")
    print(f"ahd made {page} from its package data.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python check_wheel.py FOLDER")
    main(Path(sys.argv[1]))
