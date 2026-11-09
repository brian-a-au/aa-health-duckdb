"""Turn the `build_view` dict into one HTML file that loads nothing from the network.

Safety rules (Review Focus 2):
- Jinja runs with autoescape, so every value placed in the markup is escaped.
- The view goes into a `<script type="application/json">` block as JSON, with `<`, `>`
  and `&` written as JSON unicode escapes, so website data can never close the tag.
- The page script only sets text with `textContent` and builds elements with DOM calls.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from importlib import resources
from pathlib import Path
from typing import Any

from jinja2 import Environment, PackageLoader
from markupsafe import Markup

NO_BANDS_NOTE = "Anomaly bands are not available. See the status section."
WEEK_NOTE = "This week is the 7 days ending yesterday. The Excel version used 8 to 2 days ago."

ISSUE_HEADINGS = {
    "anomaly_not_allowed": (
        "Anomaly bands are not available. The API credential's product profile does not "
        "include the Anomaly Detection permission (Report Suite Tools)."
    ),
    "no_custom_components": (
        "No custom eVars, props, or events were returned. The API credential's product "
        "profile probably hides them."
    ),
    "anomaly_probe_failed": (
        "Anomaly detection could not be tested, because the test request failed. Anomaly "
        "bands are not shown. Running again may fix this."
    ),
    "unauthorized_metric": "Some metrics or dimensions are not visible to this credential.",
    "unauthorized_dimension": "Some metrics or dimensions are not visible to this credential.",
    "network_error": (
        "Some requests failed because of network problems. Running again may fix this."
    ),
    "invalid_response": (
        "Adobe sent an answer that could not be read for some requests. Running again may fix this."
    ),
    "no_rows": (
        "Some parts of the Individual Component Summary came back empty. The credential's "
        "product profile may hide the chosen dimension, or there was no data."
    ),
}
# The fetch step records an Adobe error without its own code as http_<status>.
HTTP_HEADING = "Adobe returned an error for some requests."

CSP = "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:"

BREAKDOWN_NOTES = {
    "browser": "Rows are the top 10 values in the first column (Google), as in the Excel version.",
    "device": "Rows are the top 10 values in the first column (Other), as in the Excel version.",
}
NO_INDIVIDUAL_NAME = "the chosen dimension and metric"

STATUS_LABELS = {
    "ok": "Complete",
    "partial": "Complete, with some gaps. See the status section.",
    "failed": "Failed. No data could be read.",
    "running": "Not finished",
}

STATUS_BADGES = {
    "ok": ("good", "✓"),
    "partial": ("flag", "!"),
    "failed": ("zero", "✕"),
}

RANGES = [(n * 100 + 1, n * 100 + 100) for n in range(10)]

_env = Environment(
    loader=PackageLoader("ahd.render", "templates"),
    autoescape=True,
    trim_blocks=True,
    lstrip_blocks=True,
)


def _static(name: str) -> str:
    return resources.files("ahd.render").joinpath("static", name).read_text(encoding="utf-8")


def _inline(text: str, tag: str) -> Markup:
    # The static files are ours, but make sure nothing in them can end the element early.
    if re.search(rf"</{tag}", text, re.IGNORECASE):
        raise ValueError(f"static {tag} file must not contain a closing {tag} tag")
    return Markup(text)


# The page data is packed to keep the file small. report.js unpacks it with unpackView into
# the shape build_view returns. Keep both in step: the field order below is the order there.
COMPONENT_FIELDS = ("name", "kind", "group", "number", "metric_type", "unused")
# An alert carries only these fields of its component.
ALERT_COMPONENT_FIELDS = ("name", "metric_type", "unused")
ROW_FIELDS = ("component_id", "value", "baseline", "pct", "zero", "anomaly")
ALERT_FIELDS = (
    "day",
    "component_id",
    "expected",
    "actual",
    "usual",
    "pct_vs_expected",
    "pct_vs_usual",
    "anomaly",
    "zero",
    "days_in_row",
    "drop_run",
    "streak_open",
)
SERIES_FIELDS = ("value", "expected", "upper", "lower", "anomaly")
CALENDAR_FIELDS = ("day", "high", "low", "zero", "alerts")
# The calendar's lists of components, by place in "components".
CALENDAR_LISTS = ("high", "low", "zero")


def _number(v: Any) -> Any:
    """1234.0 as 1234, to save the ".0" on every whole number."""
    if isinstance(v, float) and v.is_integer() and abs(v) < 2**53:
        return int(v)
    return v


def _pack_series(points: list[list[Any]]) -> dict[str, Any]:
    """One list per field, one entry per day from the first day.

    A day without a point is null in every list, and its place is in "gaps". A day with a
    point may also be null in every list, so the gaps tell the two apart.
    """
    first = date.fromisoformat(points[0][0])
    days = (date.fromisoformat(points[-1][0]) - first).days + 1
    places = [(date.fromisoformat(p[0]) - first).days for p in points]
    out: dict[str, Any] = {"start": points[0][0]}
    for i, field in enumerate(SERIES_FIELDS, start=1):
        column: list[Any] = [None] * days
        for at, p in zip(places, points, strict=True):
            column[at] = _number(p[i])
        out[field] = column
    gaps = sorted(set(range(days)) - set(places))
    if gaps:
        out["gaps"] = gaps
    return out


def pack_view(view: dict[str, Any]) -> dict[str, Any]:
    """The view with each component's name and group stored once, and rows as lists.

    The day and week tables repeated every component's name, kind and group 8 times, and
    every chart point repeated its date. For 410 components, the page went from 4.1 MB to
    1.9 MB.
    """
    views = view.get("views", {})
    components: dict[str, list[Any]] = {}
    for rows in views.values():
        for r in rows:
            components.setdefault(r["component_id"], [r[f] for f in COMPONENT_FIELDS])
    packed = dict(view)
    packed["components"] = components
    packed["views"] = {
        k: [[_number(r[f]) for f in ROW_FIELDS] for r in rows] for k, rows in views.items()
    }
    packed["alerts"] = [[_number(a[f]) for f in ALERT_FIELDS] for a in view.get("alerts", [])]
    # Each calendar day names its components by their place in "components", which keeps the
    # order it was built in. A component that is not there keeps its id.
    place = {cid: i for i, cid in enumerate(components)}
    packed["calendar"] = [
        [
            [place.get(cid, cid) for cid in c[f]] if f in CALENDAR_LISTS else _number(c[f])
            for f in CALENDAR_FIELDS
        ]
        for c in view.get("calendar", [])
    ]
    packed["series"] = {
        cid: _pack_series(points) for cid, points in view.get("series", {}).items() if points
    }
    packed["packed"] = {
        "component": COMPONENT_FIELDS,
        "alert_component": ALERT_COMPONENT_FIELDS,
        "row": ROW_FIELDS,
        "alert": ALERT_FIELDS,
        "series": SERIES_FIELDS,
        "calendar": CALENDAR_FIELDS,
    }
    return packed


def _json_for_script(view: dict[str, Any]) -> Markup:
    text = json.dumps(pack_view(view), allow_nan=False, ensure_ascii=False, separators=(",", ":"))
    text = text.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    # U+2028 and U+2029 are valid JSON but are line breaks to some parsers.
    text = text.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return Markup(text)


def issue_heading(code: str) -> str | None:
    """A plain heading for an issue code, or None when there is none."""
    if code in ISSUE_HEADINGS:
        return ISSUE_HEADINGS[code]
    if re.fullmatch(r"http_\d+", code):
        return HTTP_HEADING
    return None


def pair_label(pair: dict[str, Any]) -> str:
    """For example "Page by Occurrences"."""
    dim = pair.get("dimension_name") or pair.get("dimension")
    metric = pair.get("metric_name") or pair.get("metric")
    if dim and metric:
        return f"{dim} by {metric}"
    return NO_INDIVIDUAL_NAME


def individual_label(pairs: list[dict[str, Any]]) -> str:
    """The first pair, and how many more the run holds."""
    if not pairs:
        return NO_INDIVIDUAL_NAME
    more = len(pairs) - 1
    if not more:
        return pair_label(pairs[0])
    return f"{pair_label(pairs[0])}, and {more} more pair{'s' if more > 1 else ''}"


def _issue_groups(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One group per issue code, in first-seen order, with a plain heading."""
    groups: dict[str, dict[str, Any]] = {}
    for it in issues:
        code = it.get("code") or "unknown"
        g = groups.get(code)
        if g is None:
            g = groups[code] = {
                "code": code,
                "heading": issue_heading(code),
                "entries": [],
            }
        g["entries"].append(
            {"component_id": it.get("component_id"), "message": it.get("message") or ""}
        )
    return list(groups.values())


def _last_day(run: dict[str, Any]) -> str | None:
    """The day before the run date, e.g. "2026-11-07", or None without a run date."""
    today = run.get("today")
    return (date.fromisoformat(today) - timedelta(days=1)).isoformat() if today else None


def render_html(view: dict[str, Any]) -> str:
    run = view.get("run", {})
    status = run.get("status") or "unknown"
    # A run made with --date is about the days before that date, not before the run time.
    last_day = _last_day(run) if run.get("date_given") else None
    day_words = f"on {last_day}" if last_day else "yesterday"
    template = _env.get_template("report.html.j2")
    return template.render(
        run=run,
        status_label=STATUS_LABELS.get(status, status),
        status=status,
        status_kind=STATUS_BADGES.get(status, ("plain", "•"))[0],
        status_icon=STATUS_BADGES.get(status, ("plain", "•"))[1],
        csp=Markup(CSP),
        individual_label=individual_label(view.get("pairs", [])),
        breakdown_notes=BREAKDOWN_NOTES,
        issue_groups=_issue_groups(view.get("issues", [])),
        component_drops=view.get("component_drops", []),
        change_alert_pct=round(view.get("change_alert", 0.3) * 100),
        min_usual=view.get("min_usual", 10),
        component_drop_cause=view.get("component_drop_cause", ""),
        ranges=RANGES,
        no_bands_note=NO_BANDS_NOTE,
        week_note=WEEK_NOTE.replace("ending yesterday", f"ending {day_words}"),
        last_day=last_day,
        day_words=day_words,
        css=_inline(_static("report.css"), "style"),
        js=_inline(_static("report.js"), "script"),
        data=_json_for_script(view),
    )


def _safe_part(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value))
    text = re.sub(r"\.{2,}", ".", text).strip("._")
    return text or "unknown"


def write_report(view: dict[str, Any], out_dir: Path) -> Path:
    run = view["run"]
    name = f"ahd_{_safe_part(run['rsid'])}_{_safe_part(run['today'])}_run{int(run['id'])}.html"
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    path.write_text(render_html(view), encoding="utf-8")
    return path
