"""Component catalog: which metrics/dimensions a run tracks and how each is counted."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib import resources
from typing import Literal

from ahd.api.reports import Column

SOLUTION_GROUPS = [
    "Reserved Traffic Events",
    "Reserved Purchase Events",
    "App Lifecycle Events",
    "Heartbeat Events",
    "Reserved eVars",
    "List Variables",
    "App Lifecycle Dimensions",
    "Heartbeat Dimensions",
    "Reserved Props",
    "Activity Map Instances",
    "Target Integration Instances",
    "AMO Integration Instances",
]
CUSTOM_GROUPS = ["custom_events", "custom_evars", "custom_props"]
GROUP_ORDER = CUSTOM_GROUPS + SOLUTION_GROUPS

_EVENT = re.compile(r"^metrics/event(\d+)$")
_EVAR = re.compile(r"^metrics/evar(\d+)instances$")
_PROP = re.compile(r"^variables/prop(\d+)$")
_LIST = re.compile(r"^variables/listvariable\d+$")


@dataclass(frozen=True)
class Component:
    id: str
    name: str
    kind: Literal["metric", "dimension"]
    group: str
    number: int | None
    column: Column
    allowed_for_reporting: bool | None
    # Adobe's type for the metric that counts it, e.g. "int", "decimal" or "currency".
    metric_type: str | None = None


def load_solution() -> dict[str, list[str]]:
    text = (
        resources.files("ahd").joinpath("data/solution_components.json").read_text(encoding="utf-8")
    )
    return json.loads(text)


def _exists_column(dim_id: str) -> Column:
    return Column(dim_id, "metrics/occurrences", dim_id)


def _with_id(rows: list[dict]) -> list[dict]:
    """The entries that are objects with a text ID. Any other entry cannot be checked."""
    return [r for r in rows if isinstance(r, dict) and isinstance(r.get("id"), str) and r["id"]]


def _name(row: dict, default: str) -> str:
    name = row.get("name")
    return name if isinstance(name, str) and name else default


def _allowed(row: dict) -> bool | None:
    allowed = row.get("allowedForReporting")
    return allowed if isinstance(allowed, bool) else None


def build_catalog(
    metrics: list[dict], dimensions: list[dict], solution: dict[str, list[str]]
) -> list[Component]:
    member_group = {cid: g for g in SOLUTION_GROUPS for cid in solution.get(g, [])}
    out: dict[str, Component] = {}

    # Solution members (except List Variables) are counted as /metrics columns, like the
    # workbook's events_X ranges: match metrics/X, else metrics/Xinstances.
    wanted: dict[str, str] = {}
    for cid, g in member_group.items():
        if cid.startswith("metrics/"):
            wanted[cid] = g
            wanted[cid + "instances"] = g
    metrics = _with_id(metrics)
    dimensions = _with_id(dimensions)
    exact = {m["id"] for m in metrics}

    for m in metrics:
        cid = m["id"]
        name = _name(m, cid)
        mtype = m.get("type") if isinstance(m.get("type"), str) else None
        if mt := _EVENT.match(cid):
            out[cid] = Component(
                cid, name, "metric", "custom_events", int(mt[1]), Column(cid, cid), None, mtype
            )
        elif mt := _EVAR.match(cid):
            name = name.removesuffix(" Instances")
            # An eVar is a dimension. It is counted with its instances metric.
            out[cid] = Component(
                cid, name, "dimension", "custom_evars", int(mt[1]), Column(cid, cid), None, mtype
            )
        elif cid in wanted:
            if (
                cid.endswith("instances")
                and cid.removesuffix("instances") in exact
                and cid.removesuffix("instances") in wanted
            ):
                continue  # exact id exists; it wins over the instances fallback
            kind: Literal["metric", "dimension"] = (
                "dimension" if wanted[cid] == "Reserved eVars" else "metric"
            )
            out[cid] = Component(cid, name, kind, wanted[cid], None, Column(cid, cid), None, mtype)

    for d in dimensions:
        cid = d["id"]
        name = _name(d, cid)
        allowed = _allowed(d)
        if mt := _PROP.match(cid):
            out[cid] = Component(
                cid, name, "dimension", "custom_props", int(mt[1]), _exists_column(cid), allowed
            )
        elif _LIST.match(cid):
            out[cid] = Component(
                cid, name, "dimension", "List Variables", None, _exists_column(cid), allowed
            )

    page = next((d for d in dimensions if d["id"] == "variables/page"), {})
    out["variables/page"] = Component(
        "variables/page",
        _name(page, "Page"),
        "dimension",
        "Reserved Props",
        None,
        _exists_column("variables/page"),
        _allowed(page),
    )

    def key(c: Component):
        return (GROUP_ORDER.index(c.group), c.number if c.number is not None else 0, c.id)

    return sorted(out.values(), key=key)
