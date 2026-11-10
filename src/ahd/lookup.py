"""List report suites and segments, and turn a name into an ID."""

from __future__ import annotations

from typing import Any

from ahd.api.client import unreadable
from ahd.config import ConfigError

PAGE_SIZE = 1000


def _text(value: Any) -> str:
    """The value when it is text, else "". Adobe's names and IDs are text."""
    return value if isinstance(value, str) else ""


def _all_pages(client: Any, path: str, params: dict, what: str) -> list[dict]:
    """Every row of a paged list. what names the list in an error, e.g., "the report suites"."""
    rows: list[dict] = []
    page = 0
    while True:
        body = client.get(path, {**params, "limit": PAGE_SIZE, "page": page}) or {}
        content = body.get("content") if isinstance(body, dict) else None
        if not isinstance(body, dict) or not isinstance(content, (list, type(None))):
            raise unreadable(what)
        rows.extend(r for r in content or [] if isinstance(r, dict))
        total = body.get("totalPages")
        if not isinstance(total, int):
            total = 0
        if body.get("lastPage", True) or page + 1 >= total:
            return rows
        page += 1


def list_suites(client: Any) -> list[dict]:
    """Every report suite the credential can see, as {"id": rsid, "name": name}, by name."""
    rows = _all_pages(client, "/reportsuites/collections/suites", {}, "the list of report suites")
    out = [
        {"id": _text(r.get("rsid")) or _text(r.get("id")), "name": _text(r.get("name"))}
        for r in rows
    ]
    return sorted((r for r in out if r["id"]), key=lambda r: (r["name"].lower(), r["id"]))


def list_segments(client: Any, rsid: str) -> list[dict]:
    """The segments the credential can use with this suite, shared ones included, by name."""
    rows = _all_pages(
        client, "/segments", {"rsids": rsid, "includeType": "all"}, "the list of segments"
    )
    out = [{"id": _text(r.get("id")), "name": _text(r.get("name"))} for r in rows]
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


def resolve_suite(client: Any, value: str, suites: list[dict] | None = None) -> str:
    """A report suite ID from an ID or a name. Pass the suites to save a request."""
    rows = list_suites(client) if suites is None else suites
    return _pick(value, rows, "report suite", "Run ahd suites to list them.")


def resolve_segment(client: Any, rsid: str, value: str) -> str:
    hint = f"Run ahd segments --rsid {rsid} to list them."
    return _pick(value, list_segments(client, rsid), "segment", hint)


_PREFIX = {"dimension": "variables/", "metric": "metrics/"}


def list_components(client: Any, rsid: str, kind: str) -> list[dict]:
    """Every dimension or metric the API lists for the suite, as {"id", "name", "type"}."""
    path = "/dimensions" if kind == "dimension" else "/metrics"
    rows = client.get(path, {"rsid": rsid}) or []
    if not isinstance(rows, list):
        raise unreadable(f"the list of {kind}s")
    return [
        {
            "id": r["id"],
            "name": _text(r.get("name")) or r["id"],
            "type": _text(r.get("type")) or None,
        }
        for r in rows
        if isinstance(r, dict) and _text(r.get("id"))
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
