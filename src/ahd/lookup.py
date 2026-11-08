"""List report suites and segments, and turn a name into an ID."""

from __future__ import annotations

from typing import Any

from ahd.config import ConfigError

PAGE_SIZE = 1000


def _all_pages(client: Any, path: str, params: dict) -> list[dict]:
    rows: list[dict] = []
    page = 0
    while True:
        body = client.get(path, {**params, "limit": PAGE_SIZE, "page": page}) or {}
        rows.extend(r for r in body.get("content") or [] if isinstance(r, dict))
        if body.get("lastPage", True) or page + 1 >= (body.get("totalPages") or 0):
            return rows
        page += 1


def list_suites(client: Any) -> list[dict]:
    """Every report suite the credential can see, as {"id": rsid, "name": name}, by name."""
    rows = _all_pages(client, "/reportsuites/collections/suites", {})
    out = [{"id": r.get("rsid") or r.get("id"), "name": r.get("name") or ""} for r in rows]
    return sorted((r for r in out if r["id"]), key=lambda r: (r["name"].lower(), r["id"]))


def list_segments(client: Any, rsid: str) -> list[dict]:
    """The segments the credential can use with this suite, shared ones included, by name."""
    rows = _all_pages(client, "/segments", {"rsids": rsid, "includeType": "all"})
    out = [{"id": r.get("id"), "name": r.get("name") or ""} for r in rows]
    return sorted((r for r in out if r["id"]), key=lambda r: (r["name"].lower(), r["id"]))


def _pick(value: str, rows: list[dict], what: str, hint: str) -> str:
    """An exact ID wins. Else a name that matches in any case. Else a clear error."""
    for r in rows:
        if r["id"] == value:
            return value
    wanted = value.strip().lower()
    named = [r for r in rows if r["name"].strip().lower() == wanted]
    if len(named) == 1:
        return named[0]["id"]
    if len(named) > 1:
        ids = ", ".join(r["id"] for r in named)
        raise ConfigError(f"More than one {what} is named {value!r}: {ids}. Use the ID instead.")
    raise ConfigError(f"No {what} has the ID or name {value!r}. {hint}")


def resolve_suite(client: Any, value: str) -> str:
    return _pick(value, list_suites(client), "report suite", "Run ahd suites to list them.")


def resolve_segment(client: Any, rsid: str, value: str) -> str:
    hint = f"Run ahd segments --rsid {rsid} to list them."
    return _pick(value, list_segments(client, rsid), "segment", hint)


_PREFIX = {"dimension": "variables/", "metric": "metrics/"}


def list_components(client: Any, rsid: str, kind: str) -> list[dict]:
    """Every dimension or metric the API lists for the suite, as {"id", "name", "type"}."""
    path = "/dimensions" if kind == "dimension" else "/metrics"
    rows = client.get(path, {"rsid": rsid}) or []
    return [
        {"id": r["id"], "name": r.get("name") or r["id"], "type": r.get("type")}
        for r in rows
        if isinstance(r, dict) and r.get("id")
    ]


def resolve_component(client: Any, rsid: str, kind: str, value: str) -> dict:
    """A dimension or metric by full ID, short ID (evar17, cartadditions) or name.

    Returns {"id", "name", "type"}. An ID match wins over a name match.
    """
    rows = list_components(client, rsid, kind)
    text = value.strip()
    ids = {text.lower(), (_PREFIX[kind] + text).lower()}
    for r in rows:
        if r["id"].lower() in ids:
            return r
    named = [r for r in rows if r["name"].strip().lower() == text.lower()]
    if len(named) == 1:
        return named[0]
    if named:
        found = ", ".join(r["id"] for r in named)
        raise ConfigError(f"More than one {kind} is named {value!r}: {found}. Use the ID instead.")
    raise ConfigError(
        f"No {kind} in report suite {rsid} has the ID or name {value!r}. Use an ID such as "
        f"{'evar1 or page' if kind == 'dimension' else 'occurrences or event1'}, or the name "
        "shown in Analysis Workspace."
    )
