"""Fetch orchestration: one run = catalog, anomaly probe, batched trends, individual component.

All store writes happen on the calling thread; worker threads only call the API.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
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

INDIVIDUAL_ID = "individual"
NETWORK_ERROR = "network_error"

# Plain names for the Individual requests, used in issue messages.
INDIVIDUAL_LABELS = {
    "current": "Top 100 values for yesterday",
    "historical": "Top 100 values for the previous 90 days",
    "browser": "Browser table",
    "device": "Device table",
    "trend": "Daily trend",
}


def _no_rows_message(kind: str) -> str:
    return (
        f"{INDIVIDUAL_LABELS[kind]}: no rows came back. The credential's product profile "
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


@dataclass
class RunOptions:
    rsid: str
    segment_id: str | None = None
    today: date | None = None
    dimension: str = "variables/page"
    metric: str = "metrics/occurrences"
    batch_size: int = 20
    workers: int = 4


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
        dimension=opts.dimension,
        metric=opts.metric,
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
    individual = Column(INDIVIDUAL_ID, opts.metric, exists_dimension=opts.dimension)

    with ThreadPoolExecutor(max_workers=max(1, opts.workers)) as pool:
        batch_futures: list[tuple[list[Column], Future]] = [
            (
                batch,
                pool.submit(
                    client.post_report, trend_body(rsid, window.trend_range(), batch, seg, anomaly)
                ),
            )
            for batch in _batches(columns, opts.batch_size)
        ]
        ind_bodies = {
            "current": top_items_body(
                rsid, window.yesterday_range(), opts.dimension, opts.metric, seg
            ),
            "historical": top_items_body(
                rsid, window.historical_range(), opts.dimension, opts.metric, seg
            ),
            "browser": breakdown_body(
                rsid,
                window.yesterday_range(),
                opts.dimension,
                opts.metric,
                BROWSER_DIMENSION,
                BROWSER_IDS,
                seg,
            ),
            "device": breakdown_body(
                rsid,
                window.yesterday_range(),
                opts.dimension,
                opts.metric,
                DEVICE_DIMENSION,
                DEVICE_IDS,
                seg,
            ),
            "trend": trend_body(rsid, window.trend_range(), [individual], seg, anomaly),
        }
        ind_futures = {k: pool.submit(client.post_report, b) for k, b in ind_bodies.items()}

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

        for done, (kind, fut) in enumerate(ind_futures.items(), start=1):
            wait([fut])
            progress(f"Individual Component Summary: {done} of {len(ind_futures)} requests done.")
            try:
                resp = fut.result()
            except ApiError as e:
                issue(
                    "individual",
                    INDIVIDUAL_ID,
                    e.error_code or f"http_{e.status}",
                    f"{INDIVIDUAL_LABELS[kind]}: {e.message}",
                )
                continue
            except requests.RequestException as e:
                issue(
                    "individual",
                    INDIVIDUAL_ID,
                    NETWORK_ERROR,
                    f"{INDIVIDUAL_LABELS[kind]}: {_network_message(e)}",
                )
                continue
            if kind in ("current", "historical"):
                rows, _total = parse_ranked(resp)
                store.add_top_items(run_id, kind, rows)
                empty = not rows
            elif kind in ("browser", "device"):
                rows, totals = parse_breakdown(resp)
                labels = BROWSER_LABELS if kind == "browser" else DEVICE_LABELS
                store.add_breakdown(run_id, kind, labels, rows, totals)
                empty = not rows
            else:
                values, errors = parse_trend(resp, [individual])
                store.add_daily_values(run_id, values)
                value_count += len(values)
                for err in errors:
                    issue(
                        "individual",
                        INDIVIDUAL_ID,
                        err.error_code,
                        f"{INDIVIDUAL_LABELS[kind]}: {err.message}",
                    )
                empty = not values and not errors
            if empty:
                # Spec section 7: zero rows may be a permission gap, not "no data".
                issue("individual", INDIVIDUAL_ID, "no_rows", _no_rows_message(kind))

    if value_count == 0:
        return "failed"
    return "partial" if issue_count else "ok"
