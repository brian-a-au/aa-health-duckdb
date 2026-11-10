"""The `ahd` command line."""

from __future__ import annotations

import argparse
import logging
import re
import sys
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from ahd.api.auth import NO_REPORT_SUITES, SignInError, SignInUnavailable
from ahd.api.client import AdobeClient, ApiError
from ahd.catalog import CUSTOM_GROUPS
from ahd.config import ConfigError, chosen_profile, load_credentials, profile_dir
from ahd.dates import RunWindow, today_in_zone
from ahd.fetch import (
    DEFAULT_WORKERS,
    RunOptions,
    add_pair,
    fetch_catalog,
    probe_anomaly,
    run_fetch,
)
from ahd.lookup import (
    list_segments,
    list_suites,
    resolve_component,
    resolve_segment,
    resolve_suite,
)
from ahd.render.html import write_report
from ahd.store import SchemaVersionError, Store, StoreError
from ahd.summary import (
    DEFAULT_CHANGE_ALERT,
    DEFAULT_SPIKE_ALERT,
    DROP_CAUSE,
    build_view,
    component_drops,
    duration_text,
)

DEFAULT_DB = "./data/ahd.duckdb"
PROFILE_DB = "ahd.duckdb"
DEFAULT_OUT = "./reports/"
EXIT_CODES = {"ok": 0, "failed": 1, "partial": 3}
# The usual exit code after Ctrl-C: 128 plus the signal number of SIGINT.
EXIT_STOPPED = 130
STOPPED = (
    "Stopped by Ctrl-C. A run that ahd run had started is marked failed. A pair that ahd "
    "individual was adding is removed."
)

OUT_HELP = f"The folder for the HTML report (default {DEFAULT_OUT})."
RSID_HELP = "A report suite ID or name. Run ahd suites to list them."
DIMENSION_HELP = (
    "The dimension for the Individual Component Summary: a full ID (variables/evar17), a "
    "short ID (evar17) or a name (Internal Search Term)."
)
METRIC_HELP = (
    "The metric for the Individual Component Summary: a full ID (metrics/cartadditions), a "
    "short ID (cartadditions) or a name (Cart Additions)."
)

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


def _run_date(text: str) -> date:
    """An argparse type for --date: a date as YYYY-MM-DD whose 92 trend days are real dates."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise argparse.ArgumentTypeError(f"{text!r} is not a date in the form YYYY-MM-DD.")
    try:
        day = date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not a date in the form YYYY-MM-DD."
        ) from None
    try:
        RunWindow.for_today(day)
    except OverflowError:
        raise argparse.ArgumentTypeError(
            f"{text!r} is too early or too late. A run reads the 91 days before the date."
        ) from None
    return day


def _whole_number(low: int, high: int) -> Callable[[str], int]:
    """An argparse type for a whole number from low to high."""

    def parse(text: str) -> int:
        try:
            value = int(text)
        except ValueError:
            value = low - 1
        if not low <= value <= high:
            raise argparse.ArgumentTypeError(
                f"{text!r} is not a whole number from {low} to {high}."
            )
        return value

    return parse


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

    def signin(sp: argparse.ArgumentParser) -> None:
        common(sp, db=False)
        sp.add_argument(
            "--company",
            metavar="ID_OR_NAME",
            help="The login company to use, by global company ID or name. Needed only when "
            "the credential sees more than one.",
        )

    def change_alert(sp: argparse.ArgumentParser) -> None:
        sp.add_argument(
            "--change-alert",
            type=int,
            default=round(DEFAULT_CHANGE_ALERT * 100),
            choices=range(10, 101, 5),
            metavar="PERCENT",
            help="Start the report's drop slider here: flag drops larger than this percent "
            "below the usual value, and show down arrows for them. 10 to 100, in steps of 5 "
            "(default 30).",
        )
        sp.add_argument(
            "--spike-alert",
            type=int,
            default=round(DEFAULT_SPIKE_ALERT * 100),
            choices=range(10, 201, 10),
            metavar="PERCENT",
            help="Start the report's spike slider here: flag rises larger than this percent "
            "above the usual value, and show up arrows for them. 10 to 200, in steps of 10 "
            "(default 50).",
        )

    check = sub.add_parser("check", help="Sign in and check what this credential can see.")
    signin(check)
    check.add_argument("--rsid", help=RSID_HELP)

    suites = sub.add_parser("suites", help="List the report suites this credential can see.")
    signin(suites)

    segments = sub.add_parser("segments", help="List the segments you can use with a suite.")
    signin(segments)
    segments.add_argument("--rsid", required=True, help=RSID_HELP)

    run = sub.add_parser("run", help="Fetch data and write the HTML report.")
    signin(run)
    run.add_argument(
        "--db",
        type=Path,
        help=f"DuckDB file (default {DEFAULT_DB}, or ahd.duckdb in the profile folder).",
    )
    which = run.add_mutually_exclusive_group(required=True)
    which.add_argument(
        "--rsid",
        action="append",
        help=RSID_HELP + " Give it more than once to check several suites.",
    )
    which.add_argument(
        "--all-suites",
        action="store_true",
        help="Check every report suite this credential can see.",
    )
    run.add_argument("--segment", help="A segment ID or name, to filter every request.")
    run.add_argument(
        "--date",
        type=_run_date,
        help="Use this date as today (YYYY-MM-DD). It cannot be after today in the suite's time zone.",
    )
    run.add_argument("--dimension", default="page", help=DIMENSION_HELP + " Default: Page.")
    run.add_argument("--metric", default="occurrences", help=METRIC_HELP + " Default: Occurrences.")
    run.add_argument(
        "--batch-size",
        type=_whole_number(1, 100),
        default=20,
        help="Columns per trend request, 1 to 100 (default 20).",
    )
    run.add_argument(
        "--workers",
        type=_whole_number(1, 16),
        default=DEFAULT_WORKERS,
        help=f"Requests that run at the same time, 1 to 16 (default {DEFAULT_WORKERS}).",
    )
    run.add_argument("--out", type=Path, default=Path(DEFAULT_OUT), help=OUT_HELP)
    change_alert(run)

    individual = sub.add_parser(
        "individual",
        help="Add a dimension by metric pair to a stored run's Individual Component Summary.",
    )
    signin(individual)
    individual.add_argument(
        "--db",
        type=Path,
        help=f"DuckDB file (default {DEFAULT_DB}, or ahd.duckdb in the profile folder).",
    )
    individual.add_argument("--run", type=int, required=True, help="The stored run to add to.")
    individual.add_argument("--dimension", required=True, help=DIMENSION_HELP)
    individual.add_argument("--metric", required=True, help=METRIC_HELP)
    individual.add_argument("--out", type=Path, default=Path(DEFAULT_OUT), help=OUT_HELP)
    change_alert(individual)

    render = sub.add_parser("render", help="Write the HTML from a stored run. No API calls.")
    common(render, config=False)
    render.add_argument("--run", type=int, required=True, help="The stored run to rebuild.")
    render.add_argument("--out", type=Path, default=Path(DEFAULT_OUT), help=OUT_HELP)
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
    profile = _profile(args)
    if path is not None and profile:
        # A profile always wins, so --config would be ignored without a word.
        raise ConfigError(
            f"Use a profile or --config, not both. The profile {profile!r} comes from --profile "
            "or the AA_PROFILE variable."
        )
    if path is None and Path("config.json").exists():
        path = Path("config.json")
    creds = load_credentials(path, profile)
    company = getattr(args, "company", None)
    client = factory(creds, company_id=company) if company else factory(creds)
    # Only the name of the source, never a value from it.
    print(f"Signed in using {creds.source}")
    count = getattr(client, "companies", 1)
    if count > 1 and not company:
        name = getattr(client, "company_name", "") or "(no name)"
        print(
            f"This credential sees {count} login companies. Using {name} "
            f"({client.company_id}). Choose another with --company."
        )
    return client


def _print_table(rows: list[dict], empty: str) -> None:
    if not rows:
        print(empty)
        return
    width = max(len(r["id"]) for r in rows)
    print(f"{'ID':<{width}}  Name")
    for r in rows:
        print(f"{r['id']:<{width}}  {r['name']}")


def _suites(args: argparse.Namespace, factory: Callable[..., Any]) -> int:
    client = _connect(args, factory)
    rows = list_suites(client)
    _print_table(rows, NO_REPORT_SUITES)
    return 0 if rows else 2


def _segments(args: argparse.Namespace, factory: Callable[..., Any]) -> int:
    client = _connect(args, factory)
    rsid = resolve_suite(client, args.rsid)
    _print_table(list_segments(client, rsid), "No segments are available for this report suite.")
    return 0


class _Progress:
    """Status lines on stderr. On a terminal each line replaces the one before."""

    def __init__(self, stream: Any = None) -> None:
        self.stream = stream or sys.stderr
        self.live = bool(getattr(self.stream, "isatty", lambda: False)())
        self.width = 0

    def __call__(self, message: str) -> None:
        if self.live:
            self.stream.write("\r" + message.ljust(self.width))
            self.width = len(message)
        else:
            self.stream.write(message + "\n")
        self.stream.flush()

    def done(self) -> None:
        if self.live and self.width:
            self.stream.write("\r" + " " * self.width + "\r")
            self.stream.flush()


def _check(args: argparse.Namespace, factory: Callable[..., Any]) -> int:
    client = _connect(args, factory)
    if args.rsid:
        args.rsid = resolve_suite(client, args.rsid)
    if not args.rsid:
        # list_suites reads every page, so a company with over 1,000 suites is counted in full.
        count = len(list_suites(client))
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
        except (ApiError, SignInUnavailable, requests.RequestException) as e:
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


# The order of the statuses from best to worst, for the exit code of a run of several suites.
WORST_LAST = ("ok", "partial", "failed")
ALERT_NAMES = (
    ("unusual", "unusual value", "unusual values"),
    ("large drop", "large drop", "large drops"),
    ("large spike", "large spike", "large spikes"),
    ("dropped to zero", "drop to zero", "drops to zero"),
)


def _suites_to_run(args: argparse.Namespace, client: Any) -> list[str]:
    """The suite IDs to check, in the order given, each once."""
    suites = list_suites(client)
    if args.all_suites:
        if not suites:
            raise ConfigError(NO_REPORT_SUITES)
        return [r["id"] for r in suites]
    out: list[str] = []
    for value in args.rsid:
        rsid = resolve_suite(client, value, suites)
        if rsid not in out:
            out.append(rsid)
    return out


def _error_text(e: BaseException) -> str:
    """The one line that ahd prints for an error. A network error shows only its type."""
    if isinstance(e, requests.RequestException):
        return f"the request failed with a network error ({type(e).__name__})."
    return str(e)


def _alert_line(store: Store, run_id: int, threshold: float, spike: float) -> str:
    """For example "Alerts for 2026-11-07: 2 unusual values, 1 large drop, 0 large spikes,
    0 drops to zero"."""
    counts = dict(
        store.con.execute(
            "SELECT alert, count(*) FROM alerts(?, threshold := ?, spike_threshold := ?) "
            "GROUP BY alert",
            [run_id, threshold, spike],
        ).fetchall()
    )
    day = date.fromisoformat(str(store.get_run(run_id)["today"])) - timedelta(days=1)
    parts = []
    for key, one, many in ALERT_NAMES:
        n = counts.get(key, 0)
        parts.append(f"{n} {one if n == 1 else many}")
    return f"Alerts for {day.isoformat()}: {', '.join(parts)}"


def _write(view: dict[str, Any], out: Path) -> Path:
    """Write the report. A file system error becomes a plain ConfigError that names the fix."""
    try:
        return write_report(view, out)
    except OSError as e:
        run_id = view["run"]["id"]
        raise ConfigError(
            f"The report could not be written to {out} ({e.strerror or type(e).__name__}). "
            "Choose another folder with --out, or fix this one. "
            f"Run {run_id} is stored. Then run ahd render --run {run_id} to write the report."
        ) from None


def _run_suite(args: argparse.Namespace, client: Any, store: Store, rsid: str) -> str:
    """Check one suite, write its report and print its result. Returns the run's status."""
    segment = resolve_segment(client, rsid, args.segment) if args.segment else None
    dim = resolve_component(client, rsid, "dimension", args.dimension)
    met = resolve_component(client, rsid, "metric", args.metric)
    opts = RunOptions(
        rsid=rsid,
        segment_id=segment,
        today=args.date,
        dimension=dim["id"],
        metric=met["id"],
        dimension_name=dim["name"],
        metric_name=met["name"],
        metric_type=met["type"],
        batch_size=args.batch_size,
        workers=args.workers,
    )
    progress = _Progress()
    try:
        run_id = run_fetch(client, store, opts, progress=progress)
    finally:
        progress.done()
    status = store.get_run(run_id)["status"]
    threshold = args.change_alert / 100
    spike = args.spike_alert / 100
    path = _write(build_view(store, run_id, threshold, spike), args.out)
    print(path)
    print(f"Run {run_id} for {rsid} finished with status: {status}")
    if status != "failed":
        print(_alert_line(store, run_id, threshold, spike))
    drops = component_drops(store, run_id)
    if drops:
        print("Warning: this run sees fewer components than the run before it.")
        for d in drops:
            print(f"  {d['message']}")
        print(DROP_CAUSE)
    return status


def _run(args: argparse.Namespace, factory: Callable[..., Any]) -> int:
    # Open the file first, so a locked file stops the run before any call to Adobe.
    store = Store.open(_db_path(args))
    try:
        return _run_suites(args, factory, store)
    finally:
        store.con.close()


def _run_suites(args: argparse.Namespace, factory: Callable[..., Any], store: Store) -> int:
    client = _connect(args, factory)
    rsids = _suites_to_run(args, client)
    if len(rsids) == 1:
        return EXIT_CODES.get(_run_suite(args, client, store, rsids[0]), 1)
    # Several suites: a suite that fails does not stop the others.
    statuses: dict[str, str] = {}
    for i, rsid in enumerate(rsids, start=1):
        print(f"Report suite {i} of {len(rsids)}: {rsid}")
        try:
            status = _run_suite(args, client, store, rsid)
            statuses[rsid] = status if status in WORST_LAST else "failed"
        except (ConfigError, ApiError, SignInUnavailable, requests.RequestException) as e:
            # A wrong credential (any other SignInError) stops every suite, as each would fail.
            print(f"Error for {rsid}: {_error_text(e)}", file=sys.stderr)
            statuses[rsid] = "failed"
    counts = {s: sum(1 for v in statuses.values() if v == s) for s in WORST_LAST}
    print(
        f"Checked {len(rsids)} report suites: {counts['ok']} ok, {counts['partial']} partial, "
        f"{counts['failed']} failed."
    )
    worst = max(statuses.values(), key=WORST_LAST.index)
    return EXIT_CODES[worst]


def _individual(args: argparse.Namespace, factory: Callable[..., Any]) -> int:
    store = Store.open(_db_path(args), create=False)
    try:
        run = store.get_run(args.run)
    except KeyError:
        raise ConfigError(f"There is no stored run {args.run}.") from None
    client = _connect(args, factory)
    dim = resolve_component(client, run["rsid"], "dimension", args.dimension)
    met = resolve_component(client, run["rsid"], "metric", args.metric)
    progress = _Progress()
    try:
        _pair, problems = add_pair(
            client,
            store,
            args.run,
            dim["id"],
            met["id"],
            dim["name"],
            met["name"],
            met["type"],
            progress=progress,
        )
    finally:
        progress.done()
    print(
        _write(
            build_view(store, args.run, args.change_alert / 100, args.spike_alert / 100),
            args.out,
        )
    )
    print(f"Added {dim['name']} by {met['name']} to run {args.run}.")
    if problems:
        print(f"{problems} of its requests had problems. The report's status section lists them.")
        return EXIT_CODES["partial"]
    return 0


def _render(args: argparse.Namespace) -> int:
    store = Store.open(_db_path(args), read_only=True)
    try:
        store.get_run(args.run)
    except KeyError:
        raise ConfigError(f"There is no stored run {args.run}.") from None
    print(
        _write(
            build_view(store, args.run, args.change_alert / 100, args.spike_alert / 100),
            args.out,
        )
    )
    return 0


def _print_runs(rows: list[dict]) -> None:
    print(f"{'run':>4}  {'report suite':<24} {'date':<10}  {'status':<8}  took")
    for r in rows:
        took = duration_text(r["started_at"], r["finished_at"]) or ""
        print(f"{r['run_id']:>4}  {r['rsid']!s:<24} {r['today']!s:<10}  {r['status']:<8}  {took}")


def _confirm(question: str) -> bool:
    try:
        return input(question).strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _prune(args: argparse.Namespace) -> int:
    if not args.run and args.before is None:
        raise ConfigError("Say which runs to delete with --run N or --before YYYY-MM-DD.")
    store = Store.open(_db_path(args), create=False)
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
    _print_runs(Store.open(_db_path(args), read_only=True).list_runs())
    return 0


def _replace_unknown_characters() -> None:
    """Print a character that the output cannot show as "?" instead of stopping with an error.

    On Windows, output sent to a file or a pipe uses an older code page, e.g., cp1252, which
    cannot show names such as "日本サイト".
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="replace")


def main(argv: list[str] | None = None, client_factory=AdobeClient.connect) -> int:
    _replace_unknown_characters()
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
        if args.command == "individual":
            return _individual(args, client_factory)
        if args.command == "suites":
            return _suites(args, client_factory)
        if args.command == "segments":
            return _segments(args, client_factory)
        return _runs(args)
    except (ConfigError, ApiError, SignInError, SchemaVersionError, StoreError) as e:
        print(f"Error: {e}", file=sys.stderr)
    except requests.RequestException as e:
        print(f"Error: {_error_text(e)}", file=sys.stderr)
    except KeyboardInterrupt:
        print(STOPPED, file=sys.stderr)
        return EXIT_STOPPED
    return 1


if __name__ == "__main__":
    sys.exit(main())
