"""Summary view model: turn a stored run into the JSON-ready dict the HTML page renders.

Every number is computed in DuckDB SQL. Python only names columns and nests rows.
Rules follow the workbook (spec 9.1 and 9.3):

- Daily baseline = sum over the baseline window of the same weekday, divided by 12.
- Week value = sum over the 7 days ending yesterday. Week baseline = baseline sum / 12.
- pct = value / baseline - 1, empty when the baseline is 0 or missing.

Arrows and percent drop alerts use a threshold the reader sets with a slider in the page,
so the page decides them, not this module.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from ahd.catalog import CUSTOM_GROUPS, GROUP_ORDER
from ahd.dates import RunWindow
from ahd.store import Store

# The default for the page's slider, as a share. ahd run --change-alert sets it.
DEFAULT_CHANGE_ALERT = 0.30
# The slider's lowest setting. Alerts hold every row this far below usual, so the page can
# filter them for any setting.
MIN_CHANGE_ALERT = 0.10
# A percent drop alert needs a usual value of at least this much. Small numbers swing a lot.
MIN_USUAL = 10
# Warn when a run sees more than this share fewer custom components than the run before.
DROP_THRESHOLD = 0.2
GROUP_LABELS = {
    "custom_events": "custom events",
    "custom_evars": "eVars",
    "custom_props": "props",
}
DROP_CAUSE = (
    "The usual cause is a change to the product profile of the credential in the Adobe Admin "
    "Console, or a run made with a different credential. Data did not stop, but this run cannot "
    "see the missing components, so the report does not check them."
)
INDIVIDUAL_PREFIX = "individual/"

# Daily values for the run, with NaN and Infinity turned into NULL so the output stays JSON-safe.
_DV = """
dv AS (
    SELECT component_id, day,
        CASE WHEN isfinite(value) THEN value END AS value,
        CASE WHEN isfinite(expected) THEN expected END AS expected,
        CASE WHEN isfinite(upper) THEN upper END AS upper,
        CASE WHEN isfinite(lower) THEN lower END AS lower,
        anomaly
    FROM daily_values WHERE run_id = $run_id
)"""

# Catalog components that have at least one daily value, with their sort position.
# unused: the value was 0 or empty on every day through yesterday.
_COMPS = """
comps AS (
    SELECT c.component_id, c.name, c.kind, c."group", c.number, c.metric_type,
        coalesce(list_position($groups::VARCHAR[], c."group"), 1000000) AS gpos,
        NOT EXISTS (SELECT 1 FROM daily_values d
                    WHERE d.run_id = $run_id AND d.component_id = c.component_id
                      AND d.day BETWEEN $start AND $yesterday AND d.value <> 0) AS unused
    FROM components c
    WHERE c.run_id = $run_id AND NOT starts_with(c.component_id, 'individual/')
      AND EXISTS (SELECT 1 FROM daily_values d
                  WHERE d.run_id = $run_id AND d.component_id = c.component_id)
)"""

_ANOMALY = """CASE WHEN {a}.anomaly
    THEN CASE WHEN {a}.value > {a}.upper THEN 'high' ELSE 'low' END END"""

# A component that never collected data is not an alert, even when Adobe flags its 0.
# The alerts macro in views.sql leaves out the same rows with NOT (zero AND unused).
_ALERT_ANOMALY = (
    "CASE WHEN c.unused AND coalesce(d.value = 0, false) THEN NULL ELSE "
    + _ANOMALY.format(a="d")
    + " END"
)

_ORDER = "gpos, number NULLS LAST, component_id"

_DAY_SQL = f"""
WITH {_DV}, {_COMPS},
sel AS (SELECT unnest($days::DATE[]) AS sel_day),
base AS (
    SELECT s.sel_day, c.component_id, coalesce(sum(d.value), 0) / 12 AS baseline
    FROM sel s CROSS JOIN comps c
    LEFT JOIN dv d ON d.component_id = c.component_id
        AND d.day BETWEEN $baseline_start AND $baseline_end
        AND dayofweek(d.day) = dayofweek(s.sel_day)
    GROUP BY s.sel_day, c.component_id
),
r AS (
    SELECT s.sel_day, c.component_id, c.name, c.kind, c."group", c.number, c.metric_type,
        c.unused, c.gpos, d.value, b.baseline,
        CASE WHEN b.baseline <> 0 THEN d.value / b.baseline - 1 END AS pct,
        {_ALERT_ANOMALY} AS anomaly
    FROM sel s CROSS JOIN comps c
    JOIN base b ON b.sel_day = s.sel_day AND b.component_id = c.component_id
    LEFT JOIN dv d ON d.component_id = c.component_id AND d.day = s.sel_day
)
SELECT CAST(sel_day AS VARCHAR) AS sel_day, component_id, name, kind, "group", number,
    metric_type, value, baseline, pct, coalesce(value = 0, false) AS zero, unused, anomaly
FROM r
ORDER BY sel_day DESC, {_ORDER}
"""

_WEEK_SQL = f"""
WITH {_DV}, {_COMPS},
r AS (
    SELECT c.component_id, c.name, c.kind, c."group", c.number, c.metric_type, c.unused, c.gpos,
        sum(d.value) FILTER (WHERE d.day BETWEEN $week_start AND $week_end) AS value,
        coalesce(sum(d.value) FILTER (WHERE d.day BETWEEN $baseline_start AND $baseline_end), 0)
            / 12 AS baseline
    FROM comps c LEFT JOIN dv d ON d.component_id = c.component_id
    GROUP BY ALL
),
p AS (SELECT *, CASE WHEN baseline <> 0 THEN value / baseline - 1 END AS pct FROM r)
SELECT component_id, name, kind, "group", number, metric_type, value, baseline, pct,
    coalesce(value = 0, false) AS zero, unused, NULL AS anomaly
FROM p
ORDER BY {_ORDER}
"""

_SERIES_SQL = f"""
WITH {_DV}
SELECT component_id, CAST(day AS VARCHAR) AS day, value, expected, upper, lower,
    {_ANOMALY.format(a="dv")} AS anomaly
FROM dv
WHERE day BETWEEN $start AND $yesterday
ORDER BY component_id, day
"""

# Rows that may be an alert, for each day the reader can pick: Adobe flagged them, the value
# is 0, or the value is more than MIN_CHANGE_ALERT below the usual value for that weekday.
# The page picks from these with its day picker and slider.
_ALERTS_SQL = f"""
WITH {_DV}, {_COMPS},
sel AS (SELECT unnest($days::DATE[]) AS sel_day),
usual AS (
    SELECT s.sel_day, c.component_id, coalesce(sum(d.value), 0) / 12 AS usual
    FROM sel s CROSS JOIN comps c
    LEFT JOIN dv d ON d.component_id = c.component_id
        AND d.day BETWEEN $baseline_start AND $baseline_end
        AND dayofweek(d.day) = dayofweek(s.sel_day)
    GROUP BY s.sel_day, c.component_id
),
r AS (
    SELECT u.sel_day, c.name, c.component_id, c.metric_type, c.unused, c.gpos, c.number,
        d.expected, d.value AS actual, u.usual,
        CASE WHEN d.expected <> 0 THEN d.value / d.expected - 1 END AS pct_vs_expected,
        CASE WHEN u.usual <> 0 THEN d.value / u.usual - 1 END AS pct_vs_usual,
        {_ALERT_ANOMALY} AS anomaly, coalesce(d.value = 0, false) AS zero
    FROM usual u JOIN comps c ON c.component_id = u.component_id
    JOIN dv d ON d.component_id = c.component_id AND d.day = u.sel_day
)
SELECT CAST(sel_day AS VARCHAR) AS day, name, component_id, metric_type, expected, actual,
    usual, pct_vs_expected, pct_vs_usual, anomaly, zero, unused
FROM r
WHERE anomaly IS NOT NULL OR zero
    OR (usual >= {MIN_USUAL} AND pct_vs_usual < -{MIN_CHANGE_ALERT})
ORDER BY sel_day DESC, {_ORDER}
"""

_TOP_SQL = """
WITH hist AS (
    SELECT item, min(rank) AS rank FROM top_items
    WHERE run_id = $run_id AND pair = $pair AND period = 'historical' GROUP BY item
)
SELECT h.rank - c.rank AS movement, h.rank IS NULL AS is_new, c.rank, c.item,
    CASE WHEN isfinite(c.value) THEN c.value END AS value
FROM top_items c LEFT JOIN hist h ON h.item = c.item
WHERE c.run_id = $run_id AND c.pair = $pair AND c.period = 'current'
ORDER BY c.rank, c.item
"""

_LABELS_SQL = """
WITH lab AS (
    SELECT kind, col_index, first(label) AS label FROM (
        SELECT kind, col_index, label FROM breakdown_totals WHERE run_id = $run_id AND pair = $pair
        UNION ALL
        SELECT kind, col_index, label FROM breakdowns WHERE run_id = $run_id AND pair = $pair
    ) GROUP BY kind, col_index
)
SELECT l.kind, list(l.label ORDER BY l.col_index) AS labels,
    list(CASE WHEN isfinite(t.total) THEN t.total END ORDER BY l.col_index) AS totals
FROM lab l
LEFT JOIN breakdown_totals t
    ON t.run_id = $run_id AND t.pair = $pair AND t.kind = l.kind AND t.col_index = l.col_index
GROUP BY l.kind
"""

_BREAKDOWN_ROWS_SQL = """
WITH lab AS (
    SELECT DISTINCT kind, col_index FROM (
        SELECT kind, col_index FROM breakdown_totals WHERE run_id = $run_id AND pair = $pair
        UNION ALL
        SELECT kind, col_index FROM breakdowns WHERE run_id = $run_id AND pair = $pair
    )
),
items AS (SELECT DISTINCT kind, rank, item FROM breakdowns WHERE run_id = $run_id AND pair = $pair)
SELECT i.kind, i.rank, i.item,
    list(CASE WHEN isfinite(b.value) THEN b.value END ORDER BY l.col_index) AS "values"
FROM items i JOIN lab l ON l.kind = i.kind
LEFT JOIN breakdowns b ON b.run_id = $run_id AND b.pair = $pair AND b.kind = i.kind AND b.rank = i.rank
    AND b.item = i.item AND b.col_index = l.col_index
GROUP BY i.kind, i.rank, i.item
ORDER BY i.kind, i.rank, i.item
"""

_ISSUES_SQL = """
SELECT scope, component_id, code, message FROM issues WHERE run_id = $run_id ORDER BY rowid
"""


def _rows(store: Store, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    # DuckDB rejects named parameters that the statement does not use.
    used = {k: v for k, v in params.items() if f"${k}" in sql}
    cur = store.con.execute(sql, used)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _iso(v: Any) -> Any:
    return v.isoformat() if isinstance(v, (date, datetime)) else v


_NAME_SQL = """
SELECT name FROM components
WHERE run_id = $run_id AND (component_id = $id OR ($dims AND exists_dimension = $id))
ORDER BY component_id = $id DESC, component_id
LIMIT 1
"""


# Standard metrics that are not catalog components, so a run has no stored name for them.
STANDARD_NAMES = {
    "metrics/occurrences": "Occurrences",
    "metrics/pageviews": "Page Views",
    "metrics/visits": "Visits",
    "metrics/visitors": "Unique Visitors",
    "variables/page": "Page",
}


def _display_name(store: Store, run_id: int, cid: str | None, dimension: bool) -> str | None:
    """The component's name in this run, else its raw id. A dimension may be an exists column."""
    if not cid:
        return None
    row = store.con.execute(_NAME_SQL, {"run_id": run_id, "id": cid, "dims": dimension}).fetchone()
    return row[0] if row and row[0] else STANDARD_NAMES.get(cid, cid)


def _run_block(store: Store, run_id: int) -> dict[str, Any]:
    run = store.get_run(run_id)
    keys = [
        "rsid",
        "rsid_name",
        "segment_id",
        "today",
        "timezone",
        "status",
        "started_at",
        "finished_at",
        "tool_version",
        "currency",
    ]
    block = {"id": run["run_id"], **{k: _iso(run[k]) for k in keys}}
    block["duration"] = duration_text(run["started_at"], run["finished_at"])
    return block


def _pairs(store: Store, run_id: int, p: dict[str, Any]) -> list[dict[str, Any]]:
    """Each Individual Component Summary pair with its names, top 100 and breakdown tables."""
    out = []
    for pair in store.list_pairs(run_id):
        q = {**p, "pair": pair["pair"]}
        top100 = [
            {"movement": _movement(r), "rank": r["rank"], "item": r["item"], "value": r["value"]}
            for r in _rows(store, _TOP_SQL, q)
        ]
        tables = {k: {"labels": [], "rows": [], "totals": []} for k in ("browser", "device")}
        for r in _rows(store, _LABELS_SQL, q):
            if r["kind"] in tables:
                tables[r["kind"]]["labels"] = r["labels"]
                tables[r["kind"]]["totals"] = r["totals"]
        for r in _rows(store, _BREAKDOWN_ROWS_SQL, q):
            if r["kind"] in tables:
                tables[r["kind"]]["rows"].append(
                    {"rank": r["rank"], "item": r["item"], "values": r["values"]}
                )
        out.append(
            {
                "pair": pair["pair"],
                "series_id": f"{INDIVIDUAL_PREFIX}{pair['pair']}",
                "dimension": pair["dimension"],
                "metric": pair["metric"],
                "dimension_name": pair["dimension_name"]
                or _display_name(store, run_id, pair["dimension"], True),
                "metric_name": pair["metric_name"]
                or _display_name(store, run_id, pair["metric"], False),
                "metric_type": pair["metric_type"],
                "top100": top100,
                "browser": tables["browser"],
                "device": tables["device"],
            }
        )
    return out


def duration_text(started: datetime | None, finished: datetime | None) -> str | None:
    """How long a run took, e.g. "1 min 51 s". None when it has no end time."""
    if started is None or finished is None:
        return None
    seconds = max(0, round((finished - started).total_seconds()))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours} h {minutes} min"
    if minutes:
        return f"{minutes} min {secs} s"
    return f"{secs} s"


def _movement(row: dict[str, Any]) -> int | str:
    if row["is_new"]:
        return "*"
    return "-" if row["movement"] == 0 else row["movement"]


def component_drops(store: Store, run_id: int) -> list[dict[str, Any]]:
    """Custom groups with more than DROP_THRESHOLD fewer components than the previous run."""
    prev = store.previous_run(run_id)
    if prev is None:
        return []
    before = store.component_counts(prev, CUSTOM_GROUPS)
    now = store.component_counts(run_id, CUSTOM_GROUPS)
    drops = []
    for g in CUSTOM_GROUPS:
        if before[g] > 0 and now[g] < before[g] * (1 - DROP_THRESHOLD):
            label = GROUP_LABELS[g]
            drops.append(
                {
                    "group": g,
                    "previous_run": prev,
                    "before": before[g],
                    "now": now[g],
                    "message": (
                        f"This run sees {now[g]} {label}. Run {prev} for the same report suite "
                        f"saw {before[g]}."
                    ),
                }
            )
    return drops


def build_view(
    store: Store, run_id: int, change_alert: float = DEFAULT_CHANGE_ALERT
) -> dict[str, Any]:
    run = _run_block(store, run_id)
    w = RunWindow.for_today(date.fromisoformat(run["today"]))
    p: dict[str, Any] = {
        "run_id": run_id,
        "groups": GROUP_ORDER,
        "days": w.selectable_days,
        "yesterday": w.yesterday,
        "start": w.start,
        "week_start": w.week_start,
        "week_end": w.week_end,
        "baseline_start": w.baseline_start,
        "baseline_end": w.baseline_end,
    }

    views: dict[str, list[dict[str, Any]]] = {f"day:{d.isoformat()}": [] for d in w.selectable_days}
    for row in _rows(store, _DAY_SQL, p):
        views[f"day:{row.pop('sel_day')}"].append(row)
    views["week"] = _rows(store, _WEEK_SQL, p)

    series: dict[str, list[list[Any]]] = {}
    for row in _rows(store, _SERIES_SQL, p):
        series.setdefault(row["component_id"], []).append(
            [row["day"], row["value"], row["expected"], row["upper"], row["lower"], row["anomaly"]]
        )

    return {
        "run": run,
        "views": views,
        "series": series,
        "alerts": _rows(store, _ALERTS_SQL, p),
        "pairs": _pairs(store, run_id, p),
        "issues": _rows(store, _ISSUES_SQL, p),
        "component_drops": component_drops(store, run_id),
        "change_alert": change_alert,
        "min_usual": MIN_USUAL,
        "component_drop_cause": DROP_CAUSE,
    }
