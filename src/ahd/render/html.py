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
}

STATUS_LABELS = {
    "ok": "Complete",
    "partial": "Complete, with some gaps. See the status section.",
    "failed": "Failed. No data could be read.",
    "running": "Not finished",
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


def _json_for_script(view: dict[str, Any]) -> Markup:
    text = json.dumps(view, allow_nan=False, ensure_ascii=False, separators=(",", ":"))
    text = text.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    # U+2028 and U+2029 are valid JSON but are line breaks to some parsers.
    text = text.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return Markup(text)


def _issue_groups(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One group per issue code, in first-seen order, with a plain heading."""
    groups: dict[str, dict[str, Any]] = {}
    for it in issues:
        code = it.get("code") or "unknown"
        g = groups.get(code)
        if g is None:
            g = groups[code] = {
                "code": code,
                "heading": ISSUE_HEADINGS.get(code),
                "entries": [],
            }
        g["entries"].append({"component_id": it.get("component_id"),
                           "message": it.get("message") or ""})
    return list(groups.values())


def render_html(view: dict[str, Any]) -> str:
    run = view.get("run", {})
    status = run.get("status") or "unknown"
    template = _env.get_template("report.html.j2")
    return template.render(
        run=run,
        status_label=STATUS_LABELS.get(status, status),
        status=status,
        issue_groups=_issue_groups(view.get("issues", [])),
        ranges=RANGES,
        no_bands_note=NO_BANDS_NOTE,
        week_note=WEEK_NOTE,
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
