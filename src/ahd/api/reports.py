"""Pure builders and parsers for Adobe Analytics 2.0 POST /reports. No I/O."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class Column:
    component_id: str
    metric_id: str
    exists_dimension: str | None = None


@dataclass
class DailyValue:
    component_id: str
    day: date
    value: float
    expected: float | None
    upper: float | None
    lower: float | None
    anomaly: bool | None


@dataclass
class ColumnError:
    component_id: str
    error_code: str
    message: str


def _global_filters(date_range: str, segment_id: str | None) -> list[dict]:
    filters: list[dict] = [{"type": "dateRange", "dateRange": date_range}]
    if segment_id:
        filters.append({"type": "segment", "segmentId": segment_id})
    return filters


def _exists_definition(dimension: str) -> dict:
    return {
        "func": "segment",
        "version": [1, 0, 0],
        "container": {
            "func": "container",
            "context": "hits",
            "pred": {"func": "exists", "val": {"func": "attr", "name": dimension}},
        },
    }


def trend_body(
    rsid: str,
    date_range: str,
    columns: list[Column],
    segment_id: str | None,
    anomaly: bool,
) -> dict:
    metrics: list[dict] = []
    metric_filters: list[dict] = []
    for i, col in enumerate(columns):
        metric: dict = {"columnId": str(i), "id": col.metric_id}
        if col.exists_dimension:
            fid = f"f{i}"
            metric["filters"] = [fid]
            metric_filters.append(
                {
                    "id": fid,
                    "type": "segment",
                    "segmentDefinition": _exists_definition(col.exists_dimension),
                }
            )
        metrics.append(metric)
    container: dict = {"metrics": metrics}
    if metric_filters:
        container["metricFilters"] = metric_filters
    return {
        "rsid": rsid,
        "globalFilters": _global_filters(date_range, segment_id),
        "metricContainer": container,
        "dimension": "variables/daterangeday",
        "settings": {"limit": 400, "dimensionSort": "asc", "includeAnomalyDetection": anomaly},
    }


def top_items_body(
    rsid: str,
    date_range: str,
    dimension: str,
    metric: str,
    segment_id: str | None,
    limit: int = 100,
) -> dict:
    return {
        "rsid": rsid,
        "globalFilters": _global_filters(date_range, segment_id),
        "metricContainer": {"metrics": [{"columnId": "0", "id": metric, "sort": "desc"}]},
        "dimension": dimension,
        "settings": {"countRepeatInstances": True, "limit": limit, "page": 0},
    }


def breakdown_body(
    rsid: str,
    date_range: str,
    dimension: str,
    metric: str,
    breakdown_dimension: str,
    item_ids: list[str],
    segment_id: str | None,
    limit: int = 10,
) -> dict:
    metrics: list[dict] = []
    filters: list[dict] = []
    for i, item_id in enumerate(item_ids):
        fid = f"f{i}"
        m: dict = {"columnId": str(i), "id": metric, "filters": [fid]}
        if i == 0:
            m["sort"] = "desc"
        metrics.append(m)
        filters.append(
            {"id": fid, "type": "breakdown", "dimension": breakdown_dimension, "itemId": item_id}
        )
    return {
        "rsid": rsid,
        "globalFilters": _global_filters(date_range, segment_id),
        "metricContainer": {"metrics": metrics, "metricFilters": filters},
        "dimension": dimension,
        "settings": {"countRepeatInstances": True, "limit": limit, "page": 0},
    }


def _parse_day(row: dict) -> date:
    try:
        return datetime.strptime(row["value"], "%b %d, %Y").date()
    except (KeyError, TypeError, ValueError):
        s = str(row["itemId"])  # 1YYMMDD
        return date(2000 + int(s[1:3]), int(s[3:5]), int(s[5:7]))


def _at(arr: list | None, i: int):
    if arr is None or i >= len(arr):
        return None
    return arr[i]


def parse_trend(resp: dict, columns: list[Column]) -> tuple[list[DailyValue], list[ColumnError]]:
    col_meta = resp.get("columns") or {}
    errors: list[ColumnError] = []
    failed: set[int] = set()
    for e in col_meta.get("columnErrors") or []:
        try:
            idx = int(e["columnId"])
        except (KeyError, TypeError, ValueError):
            continue
        if not 0 <= idx < len(columns):
            continue
        failed.add(idx)
        errors.append(
            ColumnError(
                columns[idx].component_id,
                e.get("errorCode", ""),
                e.get("errorDescription", ""),
            )
        )
    values: list[DailyValue] = []
    for row in resp.get("rows") or []:
        day = _parse_day(row)
        data = row.get("data") or []
        for i, col in enumerate(columns):
            if i in failed or i >= len(data):
                continue
            anomaly = _at(row.get("dataAnomalyDetected"), i)
            values.append(
                DailyValue(
                    col.component_id,
                    day,
                    float(data[i]),
                    _at(row.get("dataExpected"), i),
                    _at(row.get("dataUpperBound"), i),
                    _at(row.get("dataLowerBound"), i),
                    None if anomaly is None else bool(anomaly),
                )
            )
    return values, errors


def parse_ranked(resp: dict) -> tuple[list[tuple[int, str, float]], int]:
    rows = [
        (n, r["value"], float(r["data"][0])) for n, r in enumerate(resp.get("rows") or [], start=1)
    ]
    return rows, int(resp.get("totalElements", len(rows)))


def parse_breakdown(resp: dict) -> tuple[list[tuple[str, list[float]]], list[float]]:
    rows = [(r["value"], [float(x) for x in r["data"]]) for r in resp.get("rows") or []]
    totals = [float(x) for x in (resp.get("summaryData") or {}).get("totals") or []]
    return rows, totals
