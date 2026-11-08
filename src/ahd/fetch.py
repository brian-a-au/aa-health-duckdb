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
from typing import Any

import requests

from ahd.api.client import ApiError
from ahd.api.reports import (
    Column,
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

# Plain names for the Individual requests, used in issue messages.
INDIVIDUAL_LABELS = {
    "current": "Top 100 values for yesterday",
    "historical": "Top 100 values for the previous 90 days",
    "browser": "Browser table",
    "device": "Device table",
    "trend": "Daily trend",
}


def pair_series_id(pair: int) -> str:
    """The component_id that holds a pair's daily trend in daily_values."""
    return f"{INDIVIDUAL_PREFIX}{pair}"


def _no_rows_message(kind: str, label: str) -> str:
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


def _tool_version() -> str:
    try:
        return metadata.version("ahd")
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
    return build_catalog(metrics, dimensions, load_solution()), suite or {}


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
def _pool(workers: int) -> Iterator[ThreadPoolExecutor]:
    """A worker pool that drops its waiting requests when the caller stops, e.g. on Ctrl-C.

    A plain `with ThreadPoolExecutor()` waits for every queued request before it lets an
    exception through, so Ctrl-C would still send the whole run. Requests that already
    started finish, because a thread cannot be stopped from outside.
    """
    pool = ThreadPoolExecutor(max_workers=max(1, workers))
    try:
        yield pool
    except BaseException:
        pool.shutdown(wait=False, cancel_futures=True)
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
    zone = suite.get("timezoneZoneinfo") or "UTC"
    today = opts.today or today_in_zone(zone, now())
    window = RunWindow.for_today(today)
    run_id = store.create_run(
        opts.rsid,
        suite.get("name") or opts.rsid,
        opts.segment_id,
        today,
        zone,
        _tool_version(),
        currency=suite.get("currency") if isinstance(suite.get("currency"), str) else None,
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
) -> str:
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
        except (ApiError, requests.RequestException) as e:
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

    with _pool(opts.workers) as pool:
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
                resp = fut.result()
            except ApiError as e:
                code = e.error_code or f"http_{e.status}"
                for col in batch:
                    issue("column", col.component_id, code, e.message)
                continue
            except requests.RequestException as e:
                for col in batch:
                    issue("column", col.component_id, NETWORK_ERROR, _network_message(e))
                continue
            values, errors = parse_trend(resp, batch)
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
) -> tuple[Column, dict[str, Future]]:
    """Start the 5 requests of one pair: top 100 twice, browser, device and the daily trend."""
    trend_col = Column(pair_series_id(pair), metric, exists_dimension=dimension)
    bodies = {
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
    futures: dict[str, Future],
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
        except requests.RequestException as e:
            issue("individual", cid, NETWORK_ERROR, f"{what}: {_network_message(e)}")
            continue
        if kind in ("current", "historical"):
            rows, _total = parse_ranked(resp)
            store.add_top_items(run_id, kind, rows, pair=pair)
            empty = not rows
            got_rows = bool(rows)
        elif kind in ("browser", "device"):
            rows, totals = parse_breakdown(resp)
            labels = BROWSER_LABELS if kind == "browser" else DEVICE_LABELS
            store.add_breakdown(run_id, kind, labels, rows, totals, pair=pair)
            empty = not rows
            got_rows = bool(rows)
        else:
            values, errors = parse_trend(resp, [trend_col])
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
    for p in store.list_pairs(run_id):
        if p["dimension"] == dimension and p["metric"] == metric:
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
        with _pool(5) as pool:
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
    if problems and run["status"] == "ok":
        store.set_status(run_id, "partial")
    elif stored and run["status"] == "failed":
        # The run now holds some data, so "Failed. No data could be read." is no longer true.
        store.set_status(run_id, "partial")
    return pair, problems
