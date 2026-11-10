"""Test doubles for the Adobe client. Shared by the fetch, render and check tests."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from ahd.api.client import ApiError

Predicate = Callable[[dict], bool]
Responder = dict | Callable[[dict], dict] | ApiError


class FakeClient:
    """Routes GETs by path and POSTs by a predicate on the body.

    gets:  {path: response}; a response may be a value, a callable(params) or an ApiError.
    posts: [(predicate, response)]; the first matching predicate wins. A response may be a
           dict, a callable(body) -> dict, or an ApiError instance (raised).
    default_post: used when no predicate matches (default: auto_trend for trend bodies).
    Every call is recorded in `calls` as (method, path, body_or_params).
    """

    def __init__(
        self,
        gets: dict[str, Any] | None = None,
        posts: list[tuple[Predicate, Responder]] | None = None,
        default_post: Responder | None = None,
        company_id: str = "testco",
    ):
        self.gets = dict(gets or {})
        self.posts = list(posts or [])
        self.default_post = default_post if default_post is not None else auto_response
        self.company_id = company_id
        self.calls: list[tuple[str, str, Any]] = []
        self._lock = threading.Lock()

    def _record(self, method: str, path: str, body: Any) -> None:
        with self._lock:
            self.calls.append((method, path, body))

    @staticmethod
    def _answer(resp: Any, arg: Any) -> Any:
        if isinstance(resp, ApiError):
            raise resp
        if callable(resp):
            return resp(arg)
        return resp

    def get(self, path: str, params: dict | None = None) -> Any:
        self._record("GET", path, params)
        if path not in self.gets:
            raise ApiError(404, f"no fake route for GET {path}", "not_found")
        return self._answer(self.gets[path], params)

    def post_report(self, body: dict) -> dict:
        self._record("POST", "/reports", body)
        for pred, resp in self.posts:
            if pred(body):
                return self._answer(resp, body)
        return self._answer(self.default_post, body)

    # Helpers for assertions
    def posted(self) -> list[dict]:
        return [b for m, _, b in self.calls if m == "POST"]

    def trend_bodies(self) -> list[dict]:
        return [b for b in self.posted() if is_trend(b)]


# ---- body predicates ---------------------------------------------------------


def is_trend(body: dict) -> bool:
    return body.get("dimension") == "variables/daterangeday"


def is_anomaly_trend(body: dict) -> bool:
    return is_trend(body) and body["settings"].get("includeAnomalyDetection") is True


def metric_ids(body: dict) -> list[str]:
    return [m["id"] for m in body["metricContainer"]["metrics"]]


def exists_dims(body: dict) -> list[str | None]:
    """Per column: the exists-segment dimension, or None."""
    filters = {
        f["id"]: f["segmentDefinition"]["container"]["pred"]["val"]["name"]
        for f in body["metricContainer"].get("metricFilters", [])
        if f.get("type") == "segment"
    }
    return [
        filters.get((m.get("filters") or [None])[0]) for m in body["metricContainer"]["metrics"]
    ]


def breakdown_dimension(body: dict) -> str | None:
    for f in body["metricContainer"].get("metricFilters", []):
        if f.get("type") == "breakdown":
            return f["dimension"]
    return None


def date_range(body: dict) -> str:
    return next(f["dateRange"] for f in body["globalFilters"] if f["type"] == "dateRange")


# ---- response builders -------------------------------------------------------


def _days(range_str: str) -> list[date]:
    start_s, end_s = range_str.split("/")
    start = datetime.fromisoformat(start_s[:10]).date()
    end = datetime.fromisoformat(end_s[:10]).date()
    return [start + timedelta(days=n) for n in range((end - start).days)]


def trend_response(
    body: dict, value: float = 1.0, column_errors: dict[int, str] | None = None
) -> dict:
    """One row per day in the body's range; every column gets `value`."""
    n = len(body["metricContainer"]["metrics"])
    errors = [
        {"columnId": str(i), "errorCode": code, "errorDescription": f"{code} for column {i}"}
        for i, code in (column_errors or {}).items()
    ]
    rows = [
        {
            # As Adobe writes it: the years since 1900, the month counted from 0, and the day.
            "itemId": f"{d.year - 1900}{d.month - 1:02d}{d.day:02d}",
            "value": f"{d:%b} {d.day}, {d.year}",
            "data": [0.0 if i in (column_errors or {}) else value for i in range(n)],
        }
        for d in _days(date_range(body))
    ]
    return {
        "columns": {"columnIds": [str(i) for i in range(n)], "columnErrors": errors},
        "rows": rows,
        "totalElements": len(rows),
    }


def ranked_response(body: dict, items: list[tuple[str, float]] | None = None) -> dict:
    items = items if items is not None else [("Home Page", 10.0), ("Search", 4.0)]
    n = len(body["metricContainer"]["metrics"])
    rows = [{"itemId": str(i), "value": v, "data": [x] * n} for i, (v, x) in enumerate(items, 1)]
    return {
        "rows": rows,
        "totalElements": len(rows),
        "summaryData": {"totals": [sum(x for _, x in items)] * n},
    }


def auto_response(body: dict) -> dict:
    return trend_response(body) if is_trend(body) else ranked_response(body)


# ---- restricted-profile fixtures (shape of what live Adobe returned) ---------

RESTRICTED_METRIC_IDS = [
    "metrics/occurrences",
    "metrics/botoccurrences",
    "metrics/botpageviews",
    "metrics/pageviews",
    "metrics/visits",
    "metrics/visitors",
    "metrics/bouncerate",
    "metrics/bounces",
    "metrics/entries",
    "metrics/exits",
    "metrics/timespentvisit",
    "metrics/timespentvisitor",
    "metrics/averagepagedepth",
    "metrics/reloads",
    "metrics/singlepagevisits",
]

RESTRICTED_DIMENSION_IDS = [
    "variables/" + n
    for n in [
        "browser",
        "browsertype",
        "browserheight",
        "browserwidth",
        "colordepth",
        "connectiontype",
        "cookiesenabled",
        "javaenabled",
        "javascriptversion",
        "language",
        "monitorresolution",
        "operatingsystem",
        "operatingsystemgroup",
        "mobiledevicetype",
        "mobiledevicename",
        "mobilemanufacturer",
        "mobilescreenheight",
        "mobilescreenwidth",
        "mobilescreensize",
        "mobileaudiosupport",
        "mobilevideosupport",
        "mobilecolordepth",
        "mobilecookiesupport",
        "mobileimagesupport",
        "mobilejavavm",
        "mobilenetwork",
        "mobilemaxbookmarkurllength",
        "mobilemaxurllength",
        "mobileoperatingsystem",
        "mobiledrm",
        "mobilemaxbrowserwidth",
        "devicetype",
        "geocountry",
        "georegion",
        "geocity",
        "geodma",
        "zip",
        "referrer",
        "referrertype",
        "referringdomain",
        "searchengine",
        "searchenginekeyword",
        "searchenginenatural",
        "searchenginenaturalkeyword",
        "searchenginepaid",
        "searchenginepaidkeyword",
        "entrypage",
        "entrypageoriginal",
        "exitpage",
        "server",
        "sitesection",
        "hitdepth",
        "visitnumber",
        "returnfrequency",
        "daysbeforefirstpurchase",
        "dayssincelastpurchase",
        "dayssincelastvisit",
        "visitortype",
        "timespent",
        "timespentpervisit",
        "averagepagedepth",
        "daterangeday",
        "daterangeweek",
        "daterangemonth",
        "daterangequarter",
        "daterangeyear",
        "daterangehour",
        "daterangeminute",
        "timepartdayofweek",
        "timeparthourofday",
        "timepartdayofmonth",
        "timepartdayofyear",
        "timepartmonthofyear",
        "timepartquarterofyear",
        "timepartweekdayweekend",
        "timepartminuteofhour",
        "lasttouchchannel",
        "firsttouchchannel",
        "lasttouchchanneldetail",
        "firsttouchchanneldetail",
        "trackingcode",
        "campaign",
        "product",
        "category",
        "customerloyalty",
        "purchaseid",
        "transactionid",
    ]
]


def restricted_metrics() -> list[dict]:
    return [
        {"id": i, "name": i.split("/")[1], "allowedForReporting": True}
        for i in RESTRICTED_METRIC_IDS
    ]


def restricted_dimensions() -> list[dict]:
    return [
        {"id": i, "name": i.split("/")[1], "allowedForReporting": True}
        for i in RESTRICTED_DIMENSION_IDS
    ]


def suite(rsid: str = "rs1", name: str = "Test Suite", tz: str = "US/Pacific") -> dict:
    return {"rsid": rsid, "name": name, "timezoneZoneinfo": tz}


def fake_for(
    rsid: str,
    metrics: list[dict],
    dimensions: list[dict],
    posts: list[tuple[Predicate, Responder]] | None = None,
    tz: str = "US/Pacific",
    **kw,
) -> FakeClient:
    return FakeClient(
        gets={
            f"/reportsuites/collections/suites/{rsid}": suite(rsid, tz=tz),
            "/reportsuites/collections/suites": {
                "content": [{"rsid": rsid, "name": "Test Suite"}],
                "lastPage": True,
            },
            "/segments": {
                "content": [{"id": "s300_abc", "name": "Mobile visits"}],
                "lastPage": True,
            },
            "/metrics": metrics,
            "/dimensions": dimensions,
        },
        posts=posts,
        **kw,
    )
