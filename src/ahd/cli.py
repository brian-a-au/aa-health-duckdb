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

from ahd.api.auth import NO_REPORT_SUITES, SignInError
from ahd.api.client import AdobeClient, ApiError
from ahd.catalog import CUSTOM_GROUPS
from ahd.config import ConfigError, chosen_profile, load_credentials, profile_dir
from ahd.dates import RunWindow, today_in_zone
from ahd.fetch import RunOptions, fetch_catalog, probe_anomaly, run_fetch
from ahd.render.html import write_report
from ahd.store import SchemaVersionError, Store
from ahd.summary import DEFAULT_CHANGE_ALERT, DROP_CAUSE, build_view, component_drops

DEFAULT_DB = "./data/ahd.duckdb"
PROFILE_DB = "ahd.duckdb"
DEFAULT_OUT = "./reports/"
EXIT_CODES = {"ok": 0, "failed": 1, "partial": 3}

PROFILE_HELP = (
    "Use the credential in ~/.aa/orgs/NAME/config.json, the profile aa_auto_sdr uses. "
    "Defaults to the AA_PROFILE variable."
)

ACCESS_FIX = (
    "Fix: in the Adobe Admin Console, add the OAuth Server-to-Server credential's technical "
    "account to a product profile that includes this report suite's metrics and dimensions "
    'and the Report Suite Tools item "Anomaly Detection". Changes in the Admin Console can '
    "take a while to reach Analytics, so wait a little and run this check again."
)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ahd", description="Analytics Health Dashboard as one HTML file."
    )
    p.add_argument("--verbose", action="store_true", help="Log debug detail to stderr.")
    p.add_argument("--profile", dest="top_profile", metavar="NAME", help=PROFILE_HELP)
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp: argparse.ArgumentParser, config: bool = True, db: bool = True) -> None:
        sp.add_argument("--profile", metavar="NAME", help=PROFILE_HELP)
        if config:
            sp.add_argument(
                "--config", type=Path, help="Path to config.json (default ./config.json)."
            )
        if db:
            sp.add_argument(
                "--db",
                type=Path,
                help=f"DuckDB file (default {DEFAULT_DB}, or ahd.duckdb in the profile folder).",
            )

    def change_alert(sp: argparse.ArgumentParser) -> None:
        sp.add_argument(
            "--change-alert",
            type=int,
            default=round(DEFAULT_CHANGE_ALERT * 100),
            choices=range(10, 101, 5),
            metavar="PERCENT",
            help="Start the report's slider here: flag drops larger than this percent below "
            "the usual value, and show arrows for changes larger than it. 10 to 100, in "
            "steps of 5 (default 30).",
        )

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
    change_alert(run)

    render = sub.add_parser("render", help="Write the HTML from a stored run. No API calls.")
    common(render, config=False)
    render.add_argument("--run", type=int, required=True)
    render.add_argument("--out", type=Path, default=Path(DEFAULT_OUT))
    change_alert(render)

    runs = sub.add_parser("runs", help="List stored runs.")
    common(runs, config=False)

    prune = sub.add_parser("prune", help="Delete stored runs from the DuckDB file.")
    common(prune, config=False)
    prune.add_argument(
        "--run", type=int, action="append", default=[], metavar="N", help="A run number to delete."
    )
    prune.add_argument(
        "--before", type=date.fromisoformat, metavar="YYYY-MM-DD", help="Delete runs dated before."
    )
    prune.add_argument("--yes", action="store_true", help="Delete without asking first.")
    prune.add_argument(
        "--force", action="store_true", help='Also delete runs still marked "running".'
    )
    return p


def _profile(args: argparse.Namespace) -> str | None:
    return chosen_profile(getattr(args, "profile", None) or args.top_profile)


def _db_path(args: argparse.Namespace) -> Path:
    """--db wins. Else ahd.duckdb in the profile folder, else ./data/ahd.duckdb."""
    if args.db is not None:
        return args.db
    name = _profile(args)
    if not name:
        return Path(DEFAULT_DB)
    folder = profile_dir(name)
    if not folder.is_dir():
        # Never create a folder that aa_auto_sdr would then list as a profile.
        raise ConfigError(f"Profile {name!r} was not found. Expected the folder {folder}.")
    return folder / PROFILE_DB


def _connect(args: argparse.Namespace, factory: Callable[..., Any]) -> Any:
    path = args.config
    if path is None and Path("config.json").exists():
        path = Path("config.json")
    creds = load_credentials(path, _profile(args))
    client = factory(creds)
    # Only the name of the source, never a value from it.
    print(f"Signed in using {creds.source}")
    return client


def _check(args: argparse.Namespace, factory: Callable[..., Any]) -> int:
    client = _connect(args, factory)
    if not args.rsid:
        suites = client.get("/reportsuites/collections/suites", {"limit": 1000}) or {}
        count = len(suites.get("content") or [])
        print(f"Company ID: {client.company_id}")
        print(f"Report suites visible: {count}")
        if count == 0:
            print(NO_REPORT_SUITES)
            return 2
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
    db = _db_path(args)
    client = _connect(args, factory)
    store = Store.open(db)
    opts = RunOptions(
        rsid=args.rsid,
        segment_id=args.segment,
        today=args.date,
        dimension=args.dimension,
        metric=args.metric,
        batch_size=args.batch_size,
        workers=args.workers,
    )
    run_id = run_fetch(client, store, opts)
    status = store.get_run(run_id)["status"]
    path = write_report(build_view(store, run_id, args.change_alert / 100), args.out)
    print(path)
    print(f"Run {run_id} finished with status: {status}")
    drops = component_drops(store, run_id)
    if drops:
        print("Warning: this run sees fewer components than the run before it.")
        for d in drops:
            print(f"  {d['message']}")
        print(DROP_CAUSE)
    return EXIT_CODES.get(status, 1)


def _render(args: argparse.Namespace) -> int:
    store = Store.open(_db_path(args))
    try:
        store.get_run(args.run)
    except KeyError:
        raise ConfigError(f"There is no stored run {args.run}.") from None
    print(write_report(build_view(store, args.run, args.change_alert / 100), args.out))
    return 0


def _print_runs(rows: list[dict]) -> None:
    print(f"{'run':>4}  {'report suite':<24} {'date':<10}  {'status':<8}")
    for r in rows:
        print(f"{r['run_id']:>4}  {r['rsid']!s:<24} {r['today']!s:<10}  {r['status']:<8}")


def _confirm(question: str) -> bool:
    try:
        return input(question).strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _prune(args: argparse.Namespace) -> int:
    if not args.run and args.before is None:
        raise ConfigError("Say which runs to delete with --run N or --before YYYY-MM-DD.")
    store = Store.open(_db_path(args))
    found = store.runs_to_prune(args.run, args.before)
    for n in sorted(set(args.run) - {r["run_id"] for r in found}):
        print(f"There is no stored run {n}.")
    running = [r for r in found if r["status"] == "running"]
    if running and not args.force:
        for r in running:
            print(
                f'Run {r["run_id"]} is still marked "running", so it is kept. '
                "Add --force to delete it."
            )
        found = [r for r in found if r["status"] != "running"]
    if not found:
        print("No runs were deleted.")
        return 0
    print(
        f"These {len(found)} runs will be deleted:"
        if len(found) > 1
        else "This run will be deleted:"
    )
    _print_runs(found)
    if not args.yes and not _confirm(
        f"Delete {'them' if len(found) > 1 else 'it'}? Type y to confirm: "
    ):
        print("No runs were deleted.")
        return 1
    store.delete_runs([r["run_id"] for r in found])
    print(f"Deleted {len(found)} run{'s' if len(found) > 1 else ''}.")
    return 0


def _runs(args: argparse.Namespace) -> int:
    _print_runs(Store.open(_db_path(args)).list_runs())
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
        if args.command == "prune":
            return _prune(args)
        return _runs(args)
    except (ConfigError, ApiError, SignInError, SchemaVersionError) as e:
        print(f"Error: {e}", file=sys.stderr)
    except requests.RequestException as e:
        print(
            f"Error: the request failed with a network error ({type(e).__name__}).", file=sys.stderr
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
