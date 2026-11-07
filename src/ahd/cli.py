"""The `ahd` command line."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import requests

from ahd.api.client import AdobeClient, ApiError
from ahd.catalog import CUSTOM_GROUPS
from ahd.config import ConfigError, load_credentials
from ahd.dates import RunWindow, today_in_zone
from ahd.fetch import RunOptions, fetch_catalog, probe_anomaly, run_fetch
from ahd.render.html import write_report
from ahd.store import SchemaVersionError, Store
from ahd.summary import build_view

DEFAULT_DB = "./data/ahd.duckdb"
DEFAULT_OUT = "./reports/"
EXIT_CODES = {"ok": 0, "failed": 1, "partial": 3}

ACCESS_FIX = (
    "Fix: in the Adobe Admin Console, add the OAuth Server-to-Server credential's technical "
    "account to a product profile that includes this report suite's metrics and dimensions "
    "and the Report Suite Tools item \"Anomaly Detection\". Changes in the Admin Console can "
    "take a while to reach Analytics, so wait a little and run this check again."
)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ahd", description="Analytics Health Dashboard as one HTML file."
    )
    p.add_argument("--verbose", action="store_true", help="Log debug detail to stderr.")
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp: argparse.ArgumentParser, config: bool = True, db: bool = True) -> None:
        if config:
            sp.add_argument("--config", type=Path, help="Path to config.json (default ./config.json).")
        if db:
            sp.add_argument("--db", type=Path, default=Path(DEFAULT_DB), help="DuckDB file.")

    check = sub.add_parser("check", help="Sign in and check what this credential can see.")
    common(check, db=False)
    check.add_argument("--rsid")

    run = sub.add_parser("run", help="Fetch data and write the HTML report.")
    common(run)
    run.add_argument("--rsid", required=True)
    run.add_argument("--segment")
    run.add_argument("--date", type=date.fromisoformat, help="Use this date as today (YYYY-MM-DD).")
    run.add_argument("--dimension", default="variables/page")
    run.add_argument("--metric", default="metrics/occurrences")
    run.add_argument("--batch-size", type=int, default=20)
    run.add_argument("--workers", type=int, default=4)
    run.add_argument("--out", type=Path, default=Path(DEFAULT_OUT))

    render = sub.add_parser("render", help="Write the HTML from a stored run. No API calls.")
    common(render, config=False)
    render.add_argument("--run", type=int, required=True)
    render.add_argument("--out", type=Path, default=Path(DEFAULT_OUT))

    runs = sub.add_parser("runs", help="List stored runs.")
    common(runs, config=False)
    return p


def _connect(args: argparse.Namespace, factory: Callable[..., Any]) -> Any:
    path = args.config
    if path is None and Path("config.json").exists():
        path = Path("config.json")
    return factory(load_credentials(path))


def _check(args: argparse.Namespace, factory: Callable[..., Any]) -> int:
    client = _connect(args, factory)
    if not args.rsid:
        suites = client.get("/reportsuites/collections/suites", {"limit": 1000}) or {}
        print(f"Signed in. Company ID: {client.company_id}")
        print(f"Report suites visible: {len(suites.get('content') or [])}")
        return 0
    catalog, suite = fetch_catalog(client, args.rsid)
    counts = {g: sum(1 for c in catalog if c.group == g) for g in CUSTOM_GROUPS}
    print(f"Report suite: {suite.get('name') or args.rsid}")
    print(f"Custom events: {counts['custom_events']}")
    print(f"Custom eVars: {counts['custom_evars']}")
    print(f"Custom props: {counts['custom_props']}")
    ok = True
    untested = False
    if not any(counts.values()):
        ok = False
        print("No custom events, eVars or props are visible to this credential.")
    window = RunWindow.for_today(
        today_in_zone(suite.get("timezoneZoneinfo") or "UTC", datetime.now(UTC))
    )
    if catalog:
        try:
            allowed = probe_anomaly(client, args.rsid, window, catalog[0].column)
        except (ApiError, requests.RequestException) as e:
            # Only the exception type: messages can carry URLs.
            print(f"Anomaly detection: could not be tested ({type(e).__name__})")
            untested = True
        else:
            print(f"Anomaly detection: {'allowed' if allowed else 'not allowed'}")
            if not allowed:
                ok = False
    else:
        print("Anomaly detection: not tested (no components to test with)")
    if not ok:
        print(ACCESS_FIX)
        return 2
    if untested:
        print("Not every check could be run. Wait a little and run this check again.")
        return 2
    print("All checks passed.")
    return 0


def _run(args: argparse.Namespace, factory: Callable[..., Any]) -> int:
    client = _connect(args, factory)
    store = Store.open(args.db)
    opts = RunOptions(
        rsid=args.rsid, segment_id=args.segment, today=args.date, dimension=args.dimension,
        metric=args.metric, batch_size=args.batch_size, workers=args.workers,
    )
    run_id = run_fetch(client, store, opts)
    status = store.get_run(run_id)["status"]
    path = write_report(build_view(store, run_id), args.out)
    print(path)
    print(f"Run {run_id} finished with status: {status}")
    return EXIT_CODES.get(status, 1)


def _render(args: argparse.Namespace) -> int:
    store = Store.open(args.db)
    try:
        store.get_run(args.run)
    except KeyError:
        raise ConfigError(f"There is no stored run {args.run}.") from None
    print(write_report(build_view(store, args.run), args.out))
    return 0


def _runs(args: argparse.Namespace) -> int:
    rows = Store.open(args.db).list_runs()
    print(f"{'run':>4}  {'report suite':<24} {'date':<10}  {'status':<8}")
    for r in rows:
        print(f"{r['run_id']:>4}  {r['rsid']!s:<24} {r['today']!s:<10}  {r['status']:<8}")
    return 0


def main(argv: list[str] | None = None, client_factory=AdobeClient.connect) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        stream=sys.stderr,
        format="%(levelname)s %(name)s: %(message)s",
        force=True,
    )
    try:
        if args.command == "check":
            return _check(args, client_factory)
        if args.command == "run":
            return _run(args, client_factory)
        if args.command == "render":
            return _render(args)
        return _runs(args)
    except (ConfigError, ApiError, SchemaVersionError) as e:
        print(f"Error: {e}", file=sys.stderr)
    except requests.RequestException as e:
        print(f"Error: the request failed with a network error ({type(e).__name__}).",
              file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
