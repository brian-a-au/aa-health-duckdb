"""Summary view model: turn a stored run into the JSON-ready dict the HTML page renders.

Every number is computed in DuckDB SQL. Python only names columns and nests rows.
Rules follow the workbook (spec 9.1 and 9.3):

- Daily baseline = sum over the baseline window of the same weekday, divided by 12.
- Week value = sum over the 7 days ending yesterday. Week baseline = baseline sum / 12.
- pct = value / baseline - 1, rounded to 9 decimals, empty when the baseline is 0 or missing.

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
# The same two settings for the large spike slider. ahd run --spike-alert sets the default.
DEFAULT_SPIKE_ALERT = 0.50
MIN_SPIKE_ALERT = 0.10
# A percent drop alert needs a usual value of at least this much. Small numbers swing a lot.
MIN_USUAL = 10
# The earlier runs that a report lists, newest first.
HISTORY_RUNS = 30
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
# unused: the value was 0 or empty on every day through yesterday. It reads dv, so NaN and
# Infinity count as empty, as in the component_days view.
_COMPS = """
comps AS (
    SELECT c.component_id, c.name, c.kind, c."group", c.number, c.metric_type,
        coalesce(list_position($groups::VARCHAR[], c."group"), 1000000) AS gpos,
        NOT EXISTS (SELECT 1 FROM dv d
                    WHERE d.component_id = c.component_id
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


def _pct(value: str, base: str) -> str:
    """value / base - 1, rounded to 9 decimals. Empty when base is 0 or missing.

    The rounding removes floating point noise, e.g. 7 / 10 - 1 is -0.30000000000000004. Without
    it, a drop of exactly 30 percent counts as "more than 30 percent". views.sql rounds the same.
    """
    return f"CASE WHEN {base} <> 0 THEN round({value} / {base} - 1, 9) END"


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
        {_pct("d.value", "b.baseline")} AS pct,
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
p AS (SELECT *, {_pct("value", "baseline")} AS pct FROM r)
SELECT component_id, name, kind, "group", number, metric_type, value, baseline, pct,
    coalesce(value = 0, false) AS zero, unused, NULL AS anomaly
FROM p
ORDER BY {_ORDER}
"""

# The expected value and range only draw the chart, so 4 decimals are plenty. Adobe sends 16
# digits, which made them most of the page's size.
_SERIES_SQL = f"""
WITH {_DV}
SELECT component_id, CAST(day AS VARCHAR) AS day, value, round(expected, 4) AS expected,
    round(upper, 4) AS upper, round(lower, 4) AS lower,
    {_ANOMALY.format(a="dv")} AS anomaly
FROM dv
WHERE day BETWEEN $start AND $yesterday
ORDER BY component_id, day
"""

# Rows that may be an alert, for each day the reader can pick: Adobe flagged them, the value
# is 0, or the value is more than MIN_CHANGE_ALERT below or more than MIN_SPIKE_ALERT above the
# usual value for that weekday. The page picks from these with its day picker and sliders.
#
# days_in_row counts the days, up to the row's day, that had the same alert: the same Adobe
# flag direction, a 0, a drop or a spike. Only the days the reader can pick count, so
# streak_open says that the row reaches the first of them and may be longer. For a drop,
# drop_run lets the page count the days for the slider's setting, and spike_run does the same
# for a spike.
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
        {_pct("d.value", "d.expected")} AS pct_vs_expected,
        {_pct("d.value", "u.usual")} AS pct_vs_usual,
        {_ALERT_ANOMALY} AS anomaly, coalesce(d.value = 0, false) AS zero
    FROM usual u JOIN comps c ON c.component_id = u.component_id
    JOIN dv d ON d.component_id = c.component_id AND d.day = u.sel_day
),
-- The alert of each day, to count days in a row: the direction of Adobe's flag, a value of 0,
-- or a drop or spike past the slider's lowest setting. A day without an alert ends a row.
k AS (
    SELECT sel_day, component_id, pct_vs_usual,
        CASE WHEN anomaly IS NOT NULL THEN anomaly
            WHEN zero THEN 'zero'
            WHEN usual >= {MIN_USUAL} AND pct_vs_usual < -{MIN_CHANGE_ALERT} THEN 'drop'
            WHEN usual >= {MIN_USUAL} AND pct_vs_usual > {MIN_SPIKE_ALERT} THEN 'spike'
        END AS kind
    FROM r
),
-- Days in a row with the same alert get the same grp, because each day and its place in the
-- list grow by one together.
g AS (
    SELECT *, sel_day - CAST(row_number() OVER (
        PARTITION BY component_id, kind ORDER BY sel_day) AS INTEGER) AS grp
    FROM k WHERE kind IS NOT NULL
),
streaks AS (
    SELECT *, min(sel_day) OVER (PARTITION BY component_id, kind, grp) AS first_day FROM g
),
-- A large drop or spike depends on its slider. For each day back from one, this keeps the
-- smallest change so far: the drop closest to 0, or the smallest spike. The page counts the
-- entries that are past the slider.
back AS (
    SELECT a.sel_day, a.component_id, a.kind, b.sel_day AS back_day,
        CASE a.kind WHEN 'drop' THEN max(b.pct_vs_usual) OVER w
            ELSE min(b.pct_vs_usual) OVER w END AS smallest
    FROM streaks a JOIN streaks b ON b.component_id = a.component_id AND b.kind = a.kind
        AND b.grp = a.grp AND b.sel_day <= a.sel_day
    WHERE a.kind IN ('drop', 'spike')
    WINDOW w AS (PARTITION BY a.sel_day, a.component_id ORDER BY b.sel_day DESC)
),
change_runs AS (
    SELECT sel_day, component_id, kind, list(smallest ORDER BY back_day DESC) AS change_run
    FROM back GROUP BY ALL
)
SELECT CAST(r.sel_day AS VARCHAR) AS day, name, r.component_id, metric_type, expected, actual,
    usual, pct_vs_expected, r.pct_vs_usual, anomaly, zero, unused,
    s.sel_day - s.first_day + 1 AS days_in_row,
    CASE WHEN cr.kind = 'drop' THEN cr.change_run END AS drop_run,
    CASE WHEN cr.kind = 'spike' THEN cr.change_run END AS spike_run,
    s.first_day = $first_day AS streak_open
FROM r
LEFT JOIN streaks s ON s.sel_day = r.sel_day AND s.component_id = r.component_id
LEFT JOIN change_runs cr ON cr.sel_day = r.sel_day AND cr.component_id = r.component_id
WHERE anomaly IS NOT NULL OR zero
    OR (usual >= {MIN_USUAL} AND r.pct_vs_usual < -{MIN_CHANGE_ALERT})
    OR (usual >= {MIN_USUAL} AND r.pct_vs_usual > {MIN_SPIKE_ALERT})
ORDER BY r.sel_day DESC, {_ORDER}
"""

# The weekday average of an Individual Component Summary pair for each day the reader can
# pick, by the same rule as a component's: the same weekday in the baseline weeks, over 12.
_PAIR_AVERAGE_SQL = f"""
WITH {_DV},
sel AS (SELECT unnest($days::DATE[]) AS sel_day)
SELECT CAST(s.sel_day AS VARCHAR) AS day, coalesce(sum(d.value), 0) / 12 AS average
FROM sel s
LEFT JOIN dv d ON d.component_id = $series_id
    AND d.day BETWEEN $baseline_start AND $baseline_end
    AND dayofweek(d.day) = dayofweek(s.sel_day)
GROUP BY s.sel_day
ORDER BY s.sel_day
"""

# Every day of the trend, with the components that had an alert by the rules that hold for
# any day: Adobe flagged the value, or it dropped to 0 and the component had data on other
# days. Large drops need a weekday average, which only the last 7 days have, so they are not
# here. A day with no stored value at all is left out, so the page can show it as missing.
_CALENDAR_SQL = f"""
WITH {_DV}, {_COMPS},
v AS (
    SELECT d.day, c.component_id, c.gpos, c.number,
        {_ALERT_ANOMALY} AS anomaly,
        coalesce(d.value = 0, false) AND NOT c.unused AS zero
    FROM dv d JOIN comps c ON c.component_id = d.component_id
    WHERE d.day BETWEEN $start AND $yesterday
)
SELECT CAST(day AS VARCHAR) AS day,
    coalesce(list(component_id ORDER BY {_ORDER}) FILTER (WHERE anomaly = 'high'), [])
        AS high,
    coalesce(list(component_id ORDER BY {_ORDER}) FILTER (WHERE anomaly = 'low'), [])
        AS low,
    coalesce(list(component_id ORDER BY {_ORDER}) FILTER (WHERE zero AND anomaly IS NULL), [])
        AS zero,
    count(*) FILTER (WHERE anomaly IS NOT NULL OR zero) AS alerts
FROM v
GROUP BY day
ORDER BY day
"""

# The alerts on the last day of each run of the same report suite and segment, by the same
# rules as the tiles: one query over every run's stored values. Each run uses its own dates.
# The runs are this one and the runs for earlier run dates, newest date first, and for this
# run's date, the runs made up to this one. A run made later with --date for an earlier date
# is listed too. drops lists the change of every candidate for a large drop, so the page can
# count them for the slider's setting. A failed or unfinished run is left out, unless it is
# this one.
_HISTORY_SQL = f"""
WITH cur AS (SELECT rsid, segment_id, today FROM runs WHERE run_id = $run_id),
r AS (
    SELECT r.run_id, r.today, r.status
    FROM runs r, cur
    WHERE r.rsid = cur.rsid AND r.segment_id IS NOT DISTINCT FROM cur.segment_id
        AND (r.today < cur.today OR (r.today = cur.today AND r.run_id <= $run_id))
        AND (r.status IN ('ok', 'partial') OR r.run_id = $run_id)
    ORDER BY r.today DESC, r.run_id DESC
    LIMIT {HISTORY_RUNS}
),
dv AS (
    SELECT d.run_id, d.component_id, d.day, r.today,
        CASE WHEN isfinite(d.value) THEN d.value END AS value,
        CASE WHEN isfinite(d.upper) THEN d.upper END AS upper,
        d.anomaly
    FROM daily_values d JOIN r ON r.run_id = d.run_id
),
c AS (
    SELECT k.run_id, k.component_id,
        NOT coalesce(bool_or(d.value <> 0)
            FILTER (WHERE d.day BETWEEN d.today - 91 AND d.today - 1), false) AS unused,
        coalesce(sum(d.value) FILTER (WHERE d.day BETWEEN d.today - 91 AND d.today - 8
            AND dayofweek(d.day) = dayofweek(d.today - 1)), 0) / 12 AS usual
    FROM components k
    JOIN dv d ON d.run_id = k.run_id AND d.component_id = k.component_id
    WHERE NOT starts_with(k.component_id, 'individual/')
    GROUP BY ALL
),
last AS (
    SELECT c.run_id, c.component_id, c.unused, c.usual,
        {_pct("d.value", "c.usual")} AS pct,
        {_ALERT_ANOMALY} AS anomaly,
        coalesce(d.value = 0, false) AS zero
    FROM c JOIN r ON r.run_id = c.run_id
    LEFT JOIN dv d ON d.run_id = c.run_id AND d.component_id = c.component_id
        AND d.day = r.today - 1
)
SELECT r.run_id, CAST(r.today AS VARCHAR) AS today, CAST(r.today - 1 AS VARCHAR) AS day,
    r.status,
    count(l.component_id) AS checked,
    count(*) FILTER (WHERE l.anomaly = 'high') AS high,
    count(*) FILTER (WHERE l.anomaly = 'low') AS low,
    count(*) FILTER (WHERE l.zero AND NOT l.unused AND l.anomaly IS NULL) AS zero,
    count(*) FILTER (WHERE l.zero AND l.unused) AS unused,
    coalesce(list(l.pct ORDER BY l.pct) FILTER (WHERE l.anomaly IS NULL AND NOT l.zero
        AND l.usual >= {MIN_USUAL} AND l.pct < -{MIN_CHANGE_ALERT}), []) AS drops,
    coalesce(list(l.pct ORDER BY l.pct) FILTER (WHERE l.anomaly IS NULL AND NOT l.zero
        AND l.usual >= {MIN_USUAL} AND l.pct > {MIN_SPIKE_ALERT}), []) AS spikes,
    (SELECT count(*) FROM issues i WHERE i.run_id = r.run_id) AS problems
FROM r LEFT JOIN last l ON l.run_id = r.run_id
GROUP BY r.run_id, r.today, r.status
ORDER BY r.today DESC, r.run_id DESC
"""

# Slow declines: components whose weekly totals fell a little each week for DECLINE_WEEKS weeks.
# No single day falls far enough for an alert, so the alert lists miss them. This list is not an
# alert and does not count in the tiles or the alerts macro.
# Each week is 7 days and ends on yesterday's weekday, so each week has every weekday once. A
# week with a day without a value is left out, and a component needs all its weeks.
# regr_slope() fits a straight line through the log of the weekly totals, so exp(slope) - 1 is
# the change per week. regr_r2() says how close the weeks are to that line, from 0 to 1.
DECLINE_WEEKS = 12
DECLINE_PER_WEEK = -0.03
DECLINE_MIN_FIT = 0.7
_DECLINE_SQL = f"""
WITH {_DV}, {_COMPS},
w AS (
    SELECT d.component_id, ($yesterday - d.day) // 7 AS back, sum(d.value) AS total
    FROM dv d JOIN comps c ON c.component_id = d.component_id
    WHERE d.day BETWEEN $yesterday - {DECLINE_WEEKS * 7 - 1} AND $yesterday AND NOT c.unused
    GROUP BY ALL
    HAVING count(d.value) = 7 AND sum(d.value) > 0
),
f AS (
    SELECT component_id, count(*) AS weeks,
        regr_slope(ln(total), -back) AS slope, regr_r2(ln(total), -back) AS fit,
        arg_max(total, back) AS first_week, arg_min(total, back) AS last_week,
        list(total ORDER BY back DESC) AS totals
    FROM w GROUP BY component_id
)
SELECT f.component_id,
    round(exp(slope) - 1, 4) AS per_week,
    round(last_week / first_week - 1, 4) AS overall,
    round(fit, 2) AS fit,
    totals
FROM f JOIN comps c ON c.component_id = f.component_id
WHERE weeks = {DECLINE_WEEKS} AND isfinite(slope) AND isfinite(fit)
    AND exp(slope) - 1 <= {DECLINE_PER_WEEK} AND fit >= {DECLINE_MIN_FIT}
    AND first_week >= 7 * {MIN_USUAL}
ORDER BY per_week, {_ORDER}
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
    block["date_given"] = bool(run["date_given"])
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
        series_id = f"{INDIVIDUAL_PREFIX}{pair['pair']}"
        averages = {
            r["day"]: r["average"]
            for r in _rows(store, _PAIR_AVERAGE_SQL, {**q, "series_id": series_id})
        }
        out.append(
            {
                "pair": pair["pair"],
                "series_id": series_id,
                "weekday_average": averages,
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
    store: Store,
    run_id: int,
    change_alert: float = DEFAULT_CHANGE_ALERT,
    spike_alert: float = DEFAULT_SPIKE_ALERT,
) -> dict[str, Any]:
    run = _run_block(store, run_id)
    w = RunWindow.for_today(date.fromisoformat(run["today"]))
    p: dict[str, Any] = {
        "run_id": run_id,
        "groups": GROUP_ORDER,
        "days": w.selectable_days,
        "first_day": min(w.selectable_days),
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
        "calendar": _rows(store, _CALENDAR_SQL, p),
        "history": _rows(store, _HISTORY_SQL, p),
        "declines": _rows(store, _DECLINE_SQL, p),
        "pairs": _pairs(store, run_id, p),
        "issues": _rows(store, _ISSUES_SQL, p),
        "component_drops": component_drops(store, run_id),
        "change_alert": change_alert,
        "spike_alert": spike_alert,
        "min_usual": MIN_USUAL,
        "component_drop_cause": DROP_CAUSE,
    }
