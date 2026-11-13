"""ahd alerts: the alerts of stored runs as text, JSON or CSV.

The alerts come from the alerts() macro of sql/views.sql, so they follow the same rules as the
report and the alert line of ahd run. This module holds no rule of its own. The store must use
the current views, see Store.use_current_views().
"""

from __future__ import annotations

import csv
import json
import math
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, TextIO

from ahd.config import ConfigError
from ahd.store import Store
from ahd.summary import ANOMALY_LABELS, DEFAULT_ANOMALY_METHOD, DEFAULT_SENSITIVITY

FORMATS = ("text", "json", "csv")
# Raise only when a field of the JSON is removed or changes its meaning. New fields may be added
# without a new version.
FORMAT_VERSION = 1

# The alerts in the order of the alert line, with the words for one and for several.
ALERT_NAMES = (
    ("unusual", "unusual value", "unusual values"),
    ("large drop", "large drop", "large drops"),
    ("large spike", "large spike", "large spikes"),
    ("dropped to zero", "drop to zero", "drops to zero"),
)

# The fields of a run in the JSON, and its columns in the CSV, in this order.
RUN_FIELDS = (
    "run_id",
    "rsid",
    "rsid_name",
    "segment_id",
    "status",
    "day",
    "method",
    "method_name",
    "sensitivity",
    "threshold",
    "spike_threshold",
)
# The columns of the alerts() macro without run_id and day, which are those of the run. since,
# days, at_least and is_new say since when the alert has held. ahd 0.1.12 added them. event
# names the suite-wide event of the alert, or is None. ahd 0.1.13 added it.
ALERT_FIELDS = (
    "alert",
    "anomaly",
    "component_id",
    "name",
    "group",
    "metric_type",
    "value",
    "usual",
    "expected",
    "pct_vs_usual",
    "since",
    "days",
    "at_least",
    "is_new",
    "event",
)
# The columns of the suite_events() macro without run_id and day, which are those of the run.
# The JSON adds text, the sentence that the text output prints.
EVENT_FIELDS = (
    "event",
    "alerts",
    "components",
    "since",
    "at_least",
    "since_alerts",
    "new",
)
# The text output lists this many alerts of each event, unless --every-alert asks for all.
EVENT_SHOWN = 5

FINISHED = ("ok", "partial")


@dataclass(frozen=True)
class Rules:
    """The settings that the alerts() macro gets. The thresholds are shares, e.g., 0.30."""

    threshold: float
    spike_threshold: float
    method: str = DEFAULT_ANOMALY_METHOD
    sensitivity: str = DEFAULT_SENSITIVITY


@dataclass
class RunAlerts:
    """One run, with the fields of RUN_FIELDS, its alerts, with the fields of ALERT_FIELDS, and
    its suite-wide events, with the fields of EVENT_FIELDS and text."""

    run: dict[str, Any]
    alerts: list[dict[str, Any]]
    events: list[dict[str, Any]] = field(default_factory=list)


def counts_line(day: date, counts: dict[str, int]) -> str:
    """For example "Alerts for 2026-11-07: 2 unusual values, 1 large drop, 0 large spikes,
    0 drops to zero". counts gives the number of each alert."""
    parts = []
    for key, one, many in ALERT_NAMES:
        n = counts.get(key, 0)
        parts.append(f"{n} {one if n == 1 else many}")
    return f"Alerts for {day.isoformat()}: {', '.join(parts)}"


def alerts_day(run: dict[str, Any]) -> date:
    """The day that the alerts of a run are for: the day before its run date."""
    return date.fromisoformat(str(run["today"])) - timedelta(days=1)


def _rows(store: Store, sql: str, params: list[Any]) -> list[dict[str, Any]]:
    cur = store.con.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def choose_runs(
    store: Store,
    path: Path,
    run: int | None = None,
    rsids: list[str] | None = None,
    all_suites: bool = False,
    segment: str | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """The runs to print and a message for each report suite that has no finished run.

    run picks one stored run. rsids picks the latest finished run of each suite, in the order
    given and each once. all_suites picks the latest finished run of every suite, by name. Both
    use the runs with the segment ID segment, or the runs without a segment when it is None.
    The latest run comes from the view latest_runs. A problem that leaves nothing to print
    raises ConfigError.
    """
    if run is not None:
        found = _rows(store, "FROM runs WHERE run_id = ?", [run])
        if not found:
            raise ConfigError(f"There is no stored run {run}.")
        status = found[0]["status"]
        if status == "running":
            raise ConfigError(
                f'Run {run} is still marked "running", so its alerts are not complete. Wait for '
                "it to finish, or choose another run."
            )
        if status not in FINISHED:
            raise ConfigError(
                f'Run {run} ended "{status}", so it has no alerts. Choose a run that ended "ok" '
                'or "partial".'
            )
        return found, []
    which = f"with the segment {segment}" if segment is not None else "without a segment"
    if all_suites:
        found = _rows(
            store,
            "FROM latest_runs WHERE segment_id IS NOT DISTINCT FROM ? ORDER BY lower(rsid_name), "
            "rsid",
            [segment],
        )
        if not found:
            raise ConfigError(f"{path} holds no finished run {which}.")
        return found, []
    runs: list[dict[str, Any]] = []
    missing: list[str] = []
    for rsid in dict.fromkeys(rsids or []):
        found = _rows(
            store,
            "FROM latest_runs WHERE rsid = ? AND segment_id IS NOT DISTINCT FROM ?",
            [rsid, segment],
        )
        if found:
            runs.append(found[0])
        else:
            missing.append(
                f"{path} holds no finished run of {rsid} {which}. Check the report suite ID, "
                "or run ahd runs to list the runs."
            )
    return runs, missing


def read_alerts(store: Store, run: dict[str, Any], rules: Rules) -> RunAlerts:
    """The alerts of one run by the alerts() macro, and its events by the suite_events() macro,
    with the fields of the run."""
    options = (
        "(?, threshold := ?, spike_threshold := ?, method := ?, sensitivity := ?)",
        [run["run_id"], rules.threshold, rules.spike_threshold, rules.method, rules.sensitivity],
    )
    rows = _rows(store, "FROM alerts" + options[0], options[1])
    events = _rows(store, "FROM suite_events" + options[0], options[1])
    fields = {
        "run_id": run["run_id"],
        "rsid": run["rsid"],
        "rsid_name": run["rsid_name"],
        "segment_id": run["segment_id"],
        "status": run["status"],
        "day": alerts_day(run),
        "method": rules.method,
        "method_name": ANOMALY_LABELS[rules.method],
        "sensitivity": rules.sensitivity,
        "threshold": rules.threshold,
        "spike_threshold": rules.spike_threshold,
    }
    # The new alerts come first. Each part keeps the order of the macro.
    rows = [r for r in rows if r["is_new"]] + [r for r in rows if not r["is_new"]]
    return RunAlerts(
        fields,
        [{k: r[k] for k in ALERT_FIELDS} for r in rows],
        [
            {**{k: e[k] for k in EVENT_FIELDS}, "text": event_text(e, fields["day"], rules)}
            for e in events
        ],
    )


def json_value(value: Any) -> Any:
    """A value as JSON has it: a day as YYYY-MM-DD, and a number that is not finite as null."""
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def write_json(results: list[RunAlerts], out: TextIO) -> None:
    """Every character outside ASCII is escaped, so the output reads the same in any encoding."""
    data = {
        "format_version": FORMAT_VERSION,
        "runs": [
            {
                **{k: json_value(v) for k, v in r.run.items()},
                "events": [{k: json_value(v) for k, v in e.items()} for e in r.events],
                "alerts": [{k: json_value(v) for k, v in a.items()} for a in r.alerts],
            }
            for r in results
        ],
    }
    out.write(json.dumps(data, indent=2, allow_nan=False) + "\n")


def csv_value(value: Any) -> Any:
    """A value as the CSV has it: as in the JSON, and true or false as JSON writes them."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return json_value(value)


def write_csv(results: list[RunAlerts], out: TextIO) -> None:
    """One header row, then one row for each alert, with the fields of its run first."""
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow([*RUN_FIELDS, *ALERT_FIELDS])
    for r in results:
        start = [csv_value(r.run[k]) for k in RUN_FIELDS]
        for a in r.alerts:
            writer.writerow([*start, *(csv_value(a[k]) for k in ALERT_FIELDS)])


# Characters that would move the cursor, start a new line or turn the text around in a terminal:
# controls, line and paragraph separators, and the marks that change the direction of text.
_UNSAFE_CATEGORIES = ("Cc", "Zl", "Zp")
_DIRECTION_MARKS = set("\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")


def inert(text: Any) -> str:
    """The text with each character that could change how a terminal shows it as "?"."""
    return "".join(
        "?" if unicodedata.category(c) in _UNSAFE_CATEGORIES or c in _DIRECTION_MARKS else c
        for c in str(text)
    )


def _amount(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return "no data"
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"


def _percent(share: float) -> str:
    return f"{share * 100:g}%"


def flagged_by(method: str, sensitivity: str) -> str:
    """The sentences that say which method flags the unusual values."""
    if method == DEFAULT_ANOMALY_METHOD:
        return "Adobe's anomaly detection flags the unusual values."
    level = "" if sensitivity == DEFAULT_SENSITIVITY else f" with {sensitivity} sensitivity"
    return (
        f"The {ANOMALY_LABELS[method]} method{level} flags the unusual values. ahd computes "
        "this method from the daily values of the run."
    )


def since_text(a: dict[str, Any]) -> str:
    """Since when an alert has held, e.g., "since 2026-11-04, 8 days", "new on 2026-11-11" or
    "since 2026-11-05 or earlier, at least 7 days"."""
    since = a["since"].isoformat()
    if a["is_new"]:
        return f"new on {since}"
    days = "1 day" if a["days"] == 1 else f"{a['days']} days"
    if a["at_least"]:
        return f"since {since} or earlier, at least {days}"
    return f"since {since}, {days}"


def event_text(e: dict[str, Any], day: date, rules: Rules) -> str:
    """The sentences of a suite-wide event, e.g., "356 of the 356 components that collected data
    had a value of 0 on 2026-11-11. For 330 of them, this started on 2026-11-05. For the other
    26, it started on other days." e has the fields of EVENT_FIELDS."""
    what = {
        "zero": "a value of 0",
        "low": "a value below the expected range",
        "high": "a value above the expected range",
        "drop": f"a value more than {_percent(rules.threshold)} below the weekday average",
        "spike": f"a value more than {_percent(rules.spike_threshold)} above the weekday average",
    }[e["event"]]
    n, k = e["alerts"], e["since_alerts"]
    head = f"{n} of the {e['components']} components that collected data had {what} on {day}."
    start = e["since"].isoformat()
    if e["at_least"]:
        start += " or earlier"
    elif e["since"] == day:
        start += ", so it is new"
    if k == n:
        return f"{head} For all of them, this started on {start}."
    rest = n - k
    if rest == 1:
        others = "the other one, it started on another day"
    else:
        others = f"the other {rest}, it started on other days"
    return f"{head} For {k} of them, this started on {start}. For {others}."


def new_line(day: date, new: int) -> str:
    """For example "3 of these alerts are new on 2026-11-07"."""
    verb = "is" if new == 1 else "are"
    return f"{new} of these alerts {verb} new on {day.isoformat()}."


def _alert_text(a: dict[str, Any]) -> str:
    parts = [
        f"- {a['alert']}: {inert(a['name'])} ({inert(a['component_id'])})",
        f"weekday average {_amount(a['usual'])}",
        f"actual {_amount(a['value'])}",
    ]
    if a["alert"] == "unusual":
        parts.append(f"expected {_amount(a['expected'])}")
    pct = a["pct_vs_usual"]
    if pct is not None and math.isfinite(pct):
        parts.append(f"change {pct * 100:+.1f}%")
    parts.append(since_text(a))
    return ", ".join(parts)


def write_text(
    results: list[RunAlerts], rules: Rules, out: TextIO, every_alert: bool = False
) -> None:
    """The rules, then for each run a heading, the alert line, how many alerts are new and one
    line for each alert, new alerts first.

    A run with suite-wide events gives each event its sentence and its first EVENT_SHOWN
    alerts, or all of them with every_alert, and then lists the other alerts.
    """
    out.write(
        f"A large drop is more than {_percent(rules.threshold)} below the weekday average, and "
        f"a large spike is more than {_percent(rules.spike_threshold)} above it. The weekday "
        "average is the average of the same weekday in the 12 weeks before. "
        f"{flagged_by(rules.method, rules.sensitivity)}\n"
    )
    for r in results:
        run = r.run
        suite = inert(run["rsid"])
        if run["rsid_name"]:
            suite = f"{inert(run['rsid_name'])} ({suite})"
        segment = "" if run["segment_id"] is None else f", segment {inert(run['segment_id'])}"
        counts: dict[str, int] = {}
        for a in r.alerts:
            counts[a["alert"]] = counts.get(a["alert"], 0) + 1
        out.write(f"\nRun {run['run_id']}, {suite}{segment}, status {run['status']}\n")
        out.write(counts_line(run["day"], counts) + "\n")
        if r.alerts:
            out.write(new_line(run["day"], sum(1 for a in r.alerts if a["is_new"])) + "\n")
        for e in r.events:
            out.write(f"Suite-wide event: {e['text']}\n")
            mine = [a for a in r.alerts if a["event"] == e["event"]]
            shown = mine if every_alert else mine[:EVENT_SHOWN]
            for a in shown:
                out.write(_alert_text(a) + "\n")
            # An event has at least 10 alerts, so at least 5 are left out.
            if len(mine) > len(shown):
                more = len(mine) - len(shown)
                out.write(
                    f"{more} more alerts of this event are not listed. Add --every-alert to "
                    "list them.\n"
                )
        others = [a for a in r.alerts if a["event"] is None]
        if r.events and others:
            out.write("Other alerts:\n")
        for a in others:
            out.write(_alert_text(a) + "\n")


def write(
    results: list[RunAlerts], rules: Rules, fmt: str, out: TextIO, every_alert: bool = False
) -> None:
    """every_alert lists every alert of a suite-wide event in the text. JSON and CSV always
    hold every alert."""
    if fmt == "json":
        write_json(results, out)
    elif fmt == "csv":
        write_csv(results, out)
    else:
        write_text(results, rules, out, every_alert)
