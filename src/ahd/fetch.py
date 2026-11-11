"""Fetch orchestration: one run = catalog, anomaly probe, batched trends, and the first
Individual Component Summary pair. add_pair adds another pair to a stored run.

All store writes happen on the calling thread; worker threads only call the API.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from importlib import metadata
from typing import Any, Literal

import requests

from ahd.api.auth import SignInUnavailable
from ahd.api.client import ApiError, unreadable
from ahd.api.reports import (
    MALFORMED,
    Column,
    MalformedResponse,
    breakdown_body,
    parse_breakdown,
    parse_ranked,
    parse_trend,
    top_items_body,
    trend_body,
)
from ahd.catalog import CUSTOM_GROUPS, Component, build_catalog, load_solution
from ahd.config import ConfigError
from ahd.dates import RunWindow, today_in_zone
from ahd.store import Store

log = logging.getLogger(__name__)

NO_CUSTOM_MESSAGE = (
    "The API returned no custom eVars, props, or events. "
    "The credential's product profile probably hides them."
)
ANOMALY_MESSAGE = (
    "This credential is not allowed to use anomaly detection. "
    "The run continued without expected values or anomaly flags."
)

BROWSER_DIMENSION = "variables/browsertype"
BROWSER_IDS = ["8", "2", "6", "7", "1"]
BROWSER_LABELS = ["Google", "Microsoft", "Apple", "Mozilla", "Other"]
DEVICE_DIMENSION = "variables/mobiledevicetype"
DEVICE_IDS = ["0", "2163986270", "1728229488", "3464263970", "2355485956"]
DEVICE_LABELS = ["Other", "Mobile Phone", "Tablet", "Media Player", "Television"]

INDIVIDUAL_PREFIX = "individual/"
# Adobe flags anomalies only when the credential is allowed to; these issue codes say it was not.
NO_ANOMALY_CODES = ("anomaly_not_allowed", "anomaly_probe_failed")
NETWORK_ERROR = "network_error"
SIGN_IN_UNAVAILABLE = "sign_in_unavailable"
INVALID_RESPONSE = "invalid_response"

# The 5 requests of one Individual Component Summary pair.
PairKind = Literal["current", "historical", "browser", "device", "trend"]
# Plain names for the Individual requests, used in issue messages.
INDIVIDUAL_LABELS: dict[PairKind, str] = {
    "current": "Top 100 values for yesterday",
    "historical": "Top 100 values for the previous 90 days",
    "browser": "Browser table",
    "device": "Device table",
    "trend": "Daily trend",
}


def pair_series_id(pair: int) -> str:
    """The component_id that holds a pair's daily trend in daily_values."""
    return f"{INDIVIDUAL_PREFIX}{pair}"


def _no_rows_message(kind: PairKind, label: str) -> str:
    return (
        f"{INDIVIDUAL_LABELS[kind]} ({label}): no rows came back. The credential's product profile "
        "may hide this dimension, or it had no data."
    )


def _probe_failed_message(e: Exception) -> str:
    # Only the exception type: messages can carry URLs; never headers.
    return (
        f"Anomaly detection could not be tested because the test request failed "
        f"({type(e).__name__}). The run continued without expected values or anomaly flags."
    )


def _network_message(e: requests.RequestException) -> str:
    # Only the exception type: requests messages can carry URLs; never headers.
    return f"The request failed with a network error ({type(e).__name__})."


# Requests that run at the same time. In live timing runs on 410 components, a run took about 1
# minute with 8 workers, 2 minutes with 4 and 3 minutes with 2. Adobe sent no rate limit errors.
# The client's Pacer keeps any number of workers within Adobe's limit of 12 requests in 6 s.
DEFAULT_WORKERS = 8


@dataclass
class RunOptions:
    rsid: str
    segment_id: str | None = None
    today: date | None = None
    dimension: str = "variables/page"
    metric: str = "metrics/occurrences"
    dimension_name: str | None = "Page"
    metric_name: str | None = "Occurrences"
    metric_type: str | None = "int"
    batch_size: int = 20
    workers: int = DEFAULT_WORKERS


# The tool's name on PyPI. The Python package is "ahd", but "ahd" on PyPI is another project.
DIST_NAME = "aa-health-duckdb"


def _tool_version() -> str:
    try:
        return metadata.version(DIST_NAME)
    except metadata.PackageNotFoundError:
        return "unknown"


def fetch_catalog(client: Any, rsid: str) -> tuple[list[Component], dict]:
    """Return the run catalog and the report suite dict (name, timezoneZoneinfo)."""
    suite = client.get(
        f"/reportsuites/collections/suites/{rsid}", {"expansion": "name,timezoneZoneinfo,currency"}
    )
    params = {"rsid": rsid, "expansion": "allowedForReporting"}
    metrics = client.get("/metrics", params) or []
    dimensions = client.get("/dimensions", dict(params)) or []
    suite = suite or {}
    # An answer of another form, e.g., an object where a list belongs, is not read at all. A
    # single entry of a list that cannot be read is left out by build_catalog.
    if not isinstance(suite, dict):
        raise unreadable("the report suite")
    if not isinstance(metrics, list):
        raise unreadable("the list of metrics")
    if not isinstance(dimensions, list):
        raise unreadable("the list of dimensions")
    return build_catalog(metrics, dimensions, load_solution()), suite


def suite_zone(suite: dict) -> str:
    """The report suite's time zone name, or "UTC" when Adobe sent none."""
    zone = suite.get("timezoneZoneinfo")
    if zone is None or zone == "":
        return "UTC"
    if not isinstance(zone, str):
        # A guess here would move the run to another day.
        raise unreadable("the report suite's time zone")
    return zone


def suite_name(suite: dict, rsid: str) -> str:
    name = suite.get("name")
    return name if isinstance(name, str) and name else rsid


def probe_anomaly(
    client: Any,
    rsid: str,
    window: RunWindow,
    column: Column,
    segment_id: str | None = None,
) -> bool:
    """One trend request with anomaly detection. False only on invalid_access.

    Any other ApiError or network error is raised; callers decide how to report it.
    """
    body = trend_body(rsid, window.trend_range(), [column], segment_id, True)
    try:
        client.post_report(body)
    except ApiError as e:
        if e.error_code == "invalid_access":
            return False
        raise
    return True


def _batches(columns: list[Column], size: int) -> list[list[Column]]:
    size = max(1, size)
    return [columns[i : i + size] for i in range(0, len(columns), size)]


@contextmanager
def _pool(workers: int, client: Any = None) -> Iterator[ThreadPoolExecutor]:
    """A worker pool that drops its waiting requests when the caller stops, e.g. on Ctrl-C.

    A plain `with ThreadPoolExecutor()` waits for every queued request before it lets an
    exception through, so Ctrl-C would still send the whole run. Requests that already
    started finish, because a thread cannot be stopped from outside.

    On Ctrl-C the client is also told to stop. Without that, a worker that was waiting to try
    a request again would keep trying, and the command would not end until it gave up.
    """
    pool = ThreadPoolExecutor(max_workers=max(1, workers))
    try:
        yield pool
    except BaseException as e:
        pool.shutdown(wait=False, cancel_futures=True)
        stop = getattr(client, "stop", None)
        # An ordinary error keeps the client usable, e.g., for the next suite of the run.
        if stop is not None and not isinstance(e, Exception):
            stop()
        raise
    pool.shutdown(wait=True)


def _quiet(message: str) -> None:
    """The default progress callback: say nothing."""


def run_fetch(
    client: Any,
    store: Store,
    opts: RunOptions,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    progress: Callable[[str], None] = _quiet,
) -> int:
    """progress gets a short plain message after each step, e.g. for a status line."""
    progress("Reading the report suite and its components.")
    catalog, suite = fetch_catalog(client, opts.rsid)
    progress(f"Found {len(catalog)} components to check.")
    zone = suite_zone(suite)
    try:
        suite_today = today_in_zone(zone, now())
    except ConfigError:
        # This computer does not know the time zone. A date that was given needs no time
        # zone, so the run goes on without the check that the date is not after today.
        if opts.today is None:
            raise
        suite_today = opts.today
    if opts.today is not None and opts.today > suite_today:
        # The last full day would be today or later, which has no full data yet.
        raise ConfigError(
            f"The date {opts.today.isoformat()} is after today in the report suite's time zone "
            f"({zone}), which is {suite_today.isoformat()}. Give a date up to "
            f"{suite_today.isoformat()}."
        )
    today = opts.today or suite_today
    window = RunWindow.for_today(today)
    run_id = store.create_run(
        opts.rsid,
        suite_name(suite, opts.rsid),
        opts.segment_id,
        today,
        zone,
        _tool_version(),
        currency=suite.get("currency") if isinstance(suite.get("currency"), str) else None,
        date_given=opts.today is not None,
    )
    try:
        status = _run(client, store, opts, run_id, catalog, window, progress)
    except BaseException:
        store.finish_run(run_id, "failed")
        raise
    store.finish_run(run_id, status)
    return run_id


def _run(
    client: Any,
    store: Store,
    opts: RunOptions,
    run_id: int,
    catalog: list[Component],
    window: RunWindow,
    progress: Callable[[str], None] = _quiet,
) -> Literal["ok", "partial", "failed"]:
    issue_count = 0
    value_count = 0

    def issue(scope: str, component_id: str | None, code: str, message: str) -> None:
        nonlocal issue_count
        issue_count += 1
        store.add_issue(run_id, scope, component_id, code, message)

    store.add_components(run_id, catalog)
    if not any(c.group in CUSTOM_GROUPS for c in catalog):
        issue("permissions", None, "no_custom_components", NO_CUSTOM_MESSAGE)

    columns = [c.column for c in catalog]
    anomaly = True
    if columns:
        try:
            allowed = probe_anomaly(client, opts.rsid, window, columns[0], opts.segment_id)
        except (ApiError, SignInUnavailable, requests.RequestException) as e:
            anomaly = False
            issue("permissions", None, "anomaly_probe_failed", _probe_failed_message(e))
        else:
            if not allowed:
                anomaly = False
                issue("permissions", None, "anomaly_not_allowed", ANOMALY_MESSAGE)

    progress("Anomaly detection is allowed." if anomaly else "Anomaly detection is not available.")
    rsid, seg = opts.rsid, opts.segment_id
    pair = store.add_pair(
        run_id,
        opts.dimension,
        opts.metric,
        opts.dimension_name,
        opts.metric_name,
        opts.metric_type,
    )

    with _pool(opts.workers, client) as pool:
        batch_futures: list[tuple[list[Column], Future]] = [
            (
                batch,
                pool.submit(
                    client.post_report, trend_body(rsid, window.trend_range(), batch, seg, anomaly)
                ),
            )
            for batch in _batches(columns, opts.batch_size)
        ]
        trend_col, ind_futures = _submit_pair(
            pool, client, rsid, seg, window, anomaly, opts.dimension, opts.metric, pair
        )

        # Results are written here, on the calling thread, in submission order.
        total = len(batch_futures)
        for done, (batch, fut) in enumerate(batch_futures, start=1):
            wait([fut])
            progress(f"Daily trends: {done} of {total} requests done.")
            try:
                values, errors = parse_trend(fut.result(), batch)
            except ApiError as e:
                code = e.error_code or f"http_{e.status}"
                for col in batch:
                    issue("column", col.component_id, code, e.message)
                continue
            except MalformedResponse:
                for col in batch:
                    issue("column", col.component_id, INVALID_RESPONSE, MALFORMED)
                continue
            except SignInUnavailable as e:
                # Signing in again failed for a while. Later requests may still work.
                for col in batch:
                    issue("column", col.component_id, SIGN_IN_UNAVAILABLE, str(e))
                continue
            except requests.RequestException as e:
                for col in batch:
                    issue("column", col.component_id, NETWORK_ERROR, _network_message(e))
                continue
            store.add_daily_values(run_id, values)
            value_count += len(values)
            for err in errors:
                issue("column", err.component_id, err.error_code, err.message)

        label = _pair_label(opts.dimension_name or opts.dimension, opts.metric_name or opts.metric)
        pair_values, _stored = _store_pair(
            store, run_id, pair, trend_col, ind_futures, label, issue, progress
        )
        value_count += pair_values

    if value_count == 0:
        return "failed"
    return "partial" if issue_count else "ok"


def _pair_label(dimension: str, metric: str) -> str:
    return f"{dimension} by {metric}"


def _submit_pair(
    pool: ThreadPoolExecutor,
    client: Any,
    rsid: str,
    seg: str | None,
    window: RunWindow,
    anomaly: bool,
    dimension: str,
    metric: str,
    pair: int,
) -> tuple[Column, dict[PairKind, Future]]:
    """Start the 5 requests of one pair: top 100 twice, browser, device and the daily trend."""
    trend_col = Column(pair_series_id(pair), metric, exists_dimension=dimension)
    bodies: dict[PairKind, dict] = {
        "current": top_items_body(rsid, window.yesterday_range(), dimension, metric, seg),
        "historical": top_items_body(rsid, window.historical_range(), dimension, metric, seg),
        "browser": breakdown_body(
            rsid,
            window.yesterday_range(),
            dimension,
            metric,
            BROWSER_DIMENSION,
            BROWSER_IDS,
            seg,
        ),
        "device": breakdown_body(
            rsid,
            window.yesterday_range(),
            dimension,
            metric,
            DEVICE_DIMENSION,
            DEVICE_IDS,
            seg,
        ),
        "trend": trend_body(rsid, window.trend_range(), [trend_col], seg, anomaly),
    }
    return trend_col, {k: pool.submit(client.post_report, b) for k, b in bodies.items()}


def _store_pair(
    store: Store,
    run_id: int,
    pair: int,
    trend_col: Column,
    futures: dict[PairKind, Future],
    label: str,
    issue: Callable[[str, str | None, str, str], None],
    progress: Callable[[str], None],
) -> tuple[int, int]:
    """Write one pair's results on the calling thread.

    Returns the number of daily values, and how many of the 5 requests stored any rows.
    """
    cid = trend_col.component_id
    value_count = 0
    stored = 0
    for done, (kind, fut) in enumerate(futures.items(), start=1):
        wait([fut])
        progress(f"Individual Component Summary: {done} of {len(futures)} requests done.")
        what = f"{INDIVIDUAL_LABELS[kind]} ({label})"
        try:
            resp = fut.result()
        except ApiError as e:
            issue("individual", cid, e.error_code or f"http_{e.status}", f"{what}: {e.message}")
            continue
        except SignInUnavailable as e:
            issue("individual", cid, SIGN_IN_UNAVAILABLE, f"{what}: {e}")
            continue
        except requests.RequestException as e:
            issue("individual", cid, NETWORK_ERROR, f"{what}: {_network_message(e)}")
            continue
        try:
            if kind in ("current", "historical"):
                ranked, _total = parse_ranked(resp)
            elif kind in ("browser", "device"):
                table, totals = parse_breakdown(resp)
            else:
                values, errors = parse_trend(resp, [trend_col])
        except MalformedResponse:
            issue("individual", cid, INVALID_RESPONSE, f"{what}: {MALFORMED}")
            continue
        if kind == "current" or kind == "historical":
            store.add_top_items(run_id, kind, ranked, pair=pair)
            empty = not ranked
            got_rows = bool(ranked)
        elif kind == "browser" or kind == "device":
            labels = BROWSER_LABELS if kind == "browser" else DEVICE_LABELS
            store.add_breakdown(run_id, kind, labels, table, totals, pair=pair)
            empty = not table
            got_rows = bool(table)
        else:
            store.add_daily_values(run_id, values)
            value_count += len(values)
            for err in errors:
                issue("individual", cid, err.error_code, f"{what}: {err.message}")
            empty = not values and not errors
            got_rows = bool(values)
        if empty:
            # Spec section 7: zero rows may be a permission gap, not "no data".
            issue("individual", cid, "no_rows", _no_rows_message(kind, label))
        stored += got_rows
    return value_count, stored


def add_pair(
    client: Any,
    store: Store,
    run_id: int,
    dimension: str,
    metric: str,
    dimension_name: str | None = None,
    metric_name: str | None = None,
    metric_type: str | None = None,
    progress: Callable[[str], None] = _quiet,
) -> tuple[int, int]:
    """Fetch one more pair for a stored run, for the same suite, segment and dates.

    Returns the pair number and how many problems it had. A run that was "ok" becomes
    "partial" when the new pair had problems. A run that was "failed" becomes "partial" when
    the new pair stored some data.
    """
    run = store.get_run(run_id)
    if run["status"] == "running":
        raise ConfigError(f'Run {run_id} is still marked "running". Choose a finished run.')
    # A pair with the same dimension and metric that stored nothing, e.g., the first pair of a
    # failed run, is tried again. It is removed once the new pair is stored.
    empty: int | None = None
    for p in store.list_pairs(run_id):
        if p["dimension"] == dimension and p["metric"] == metric:
            if not store.pair_has_data(run_id, p["pair"], pair_series_id(p["pair"])):
                empty = p["pair"]
                continue
            raise ConfigError(
                f"Run {run_id} already holds {_pair_label(p['dimension_name'] or dimension, p['metric_name'] or metric)}."
            )
    codes = {
        r[0]
        for r in store.con.execute("SELECT code FROM issues WHERE run_id = ?", [run_id]).fetchall()
    }
    anomaly = not codes.intersection(NO_ANOMALY_CODES)
    window = RunWindow.for_today(run["today"])
    pair = store.add_pair(run_id, dimension, metric, dimension_name, metric_name, metric_type)
    problems = 0

    def issue(scope: str, component_id: str | None, code: str, message: str) -> None:
        nonlocal problems
        problems += 1
        store.add_issue(run_id, scope, component_id, code, message)

    label = _pair_label(dimension_name or dimension, metric_name or metric)
    try:
        with _pool(5, client) as pool:
            trend_col, futures = _submit_pair(
                pool,
                client,
                run["rsid"],
                run["segment_id"],
                window,
                anomaly,
                dimension,
                metric,
                pair,
            )
            _values, stored = _store_pair(
                store, run_id, pair, trend_col, futures, label, issue, progress
            )
    except BaseException:
        # A pair that stopped part way, e.g. on Ctrl-C or a failed sign-in, would block a retry
        # of the same dimension and metric. Remove it, so the run looks as it did before.
        store.delete_pair(run_id, pair, pair_series_id(pair))
        raise
    if empty is not None:
        store.delete_pair(run_id, empty, pair_series_id(empty))
    if problems and run["status"] == "ok":
        store.set_status(run_id, "partial")
    elif stored and run["status"] == "failed":
        # The run now holds some data, so "Failed. No data could be read." is no longer true.
        store.set_status(run_id, "partial")
    return pair, problems
