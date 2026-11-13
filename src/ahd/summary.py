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

from dataclasses import dataclass
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
# A local anomaly method gives a day a range only when its sample has at least this many
# values, e.g., earlier values of its weekday. The anomaly_days macro in views.sql cannot read
# this constant and writes the number itself. The two numbers must match, and
# test_a_range_needs_8_earlier_values in tests/test_summary.py fails when they do not.
MIN_SAMPLE = 8
# A suite-wide event is a group of alerts of the same condition, e.g., a value of 0, that holds
# at least EVENT_MIN alerts and at least EVENT_SHARE of the components that collected data in
# the run. The page reads both numbers from its data. The alerts and suite_events macros in
# views.sql cannot read them and write the numbers themselves. test_the_limits_are_half_and_10
# and the tests at the limits in tests/test_events.py fail when they differ.
EVENT_SHARE = 0.5
EVENT_MIN = 10
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
# expected, upper, lower and anomaly are Adobe's. With a local anomaly method, the queries that
# read them use _LOCAL_RUN_DV in its place.
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


# How wide the range of a local anomaly method is, from the widest to the narrowest. A higher
# sensitivity gives a narrower range, so it flags more values.
SENSITIVITIES = ("low", "normal", "high")
DEFAULT_SENSITIVITY = "normal"


@dataclass(frozen=True)
class AnomalyMethod:
    """A way to find the unusual values of a run. rule says it in plain words for the page.

    sample names the sample of a day for the page, after a number, e.g., "at least 8 earlier
    values of its weekday". Adobe's method has none.

    factors holds the width factor of the range for each of SENSITIVITIES, in that order: low
    is 4/3 of normal and high is 2/3 of it. Each number is written as it goes into the SQL, so
    normal gives the same results as 0.1.8. rule names the factor in use with {times}, which
    becomes e.g. "2 times " and nothing for a factor of 1, so that the rule reads "more than the
    width". Adobe's method has none, because Adobe returns its range.
    """

    key: str
    label: str
    rule: str
    sample: str | None = None
    factors: tuple[str, str, str] | None = None

    def factor(self, sensitivity: str) -> str:
        """The factor of the sensitivity as SQL, e.g., "1.5"."""
        if self.factors is None:
            raise ValueError(f"The anomaly method {self.key!r} has no sensitivity.")
        return self.factors[SENSITIVITIES.index(sensitivity)]


# The anomaly methods, in the order the command line lists them. Adobe's values are stored with
# the run. The others are computed from the run's daily values: each is one branch of the CASE
# in _local_dv(), and one branch in the anomaly_days macro of views.sql. The factors of a local
# method go into its branch in _local_dv() through _factor_sql(). views.sql writes them itself.
_METHODS = (
    AnomalyMethod(
        "adobe",
        "Adobe",
        "A value is unusual when it is outside the range that Adobe's anomaly detection expects "
        "for that day.",
    ),
    AnomalyMethod(
        "weekday-median",
        "Weekday median",
        "A value is unusual when it is far from the median of the earlier values of the same "
        "weekday in this run. Far means more than {times}the usual spread of those values.",
        "earlier values of its weekday",
        ("4", "3", "2"),
    ),
    AnomalyMethod(
        "weekday-iqr",
        "Weekday quartile range",
        "A value is unusual when it is far outside the middle half of the earlier values of the "
        "same weekday in this run. Far means more than {times}the width of the middle half "
        "past its edge.",
        "earlier values of its weekday",
        ("2.0", "1.5", "1.0"),
    ),
    AnomalyMethod(
        "weekday-trend",
        "Weekday trend",
        "A value is unusual when it is far from what its recent level and its weekday suggest. "
        "The level is the average of the 7 days before it, and far means more than {times}the "
        "usual spread of how the earlier values of the same weekday compared with their level.",
        "earlier values of its weekday that have a level",
        ("4", "3", "2"),
    ),
)
ANOMALY_METHODS: tuple[str, ...] = tuple(m.key for m in _METHODS)
ANOMALY_LABELS: dict[str, str] = {m.key: m.label for m in _METHODS}
DEFAULT_ANOMALY_METHOD = "adobe"


def _factor_sql(key: str) -> str:
    """The width factor of a local method for the chosen sensitivity, as a CASE on $sensitivity.

    The CASE stands where 0.1.8 had the number, e.g., 3 in 3 * 1.4826 * mad(...), and gives
    the same number for normal. So normal gives the same results as 0.1.8, to the last digit.
    """
    m = next(m for m in _METHODS if m.key == key)
    whens = " ".join(f"WHEN '{s}' THEN {m.factor(s)}" for s in SENSITIVITIES)
    return f"(CASE $sensitivity {whens} END)"


def _local_dv(source: str) -> str:
    """The dv CTE for a local method, with the CTEs it needs before it.

    source gives the daily values as d and their run as ru. Only the days from today - 91 to
    today - 1 count, and a value that is not finite is left out. Each method takes its own
    sample of a day from the earlier days and gives from it the sample size n, the lowest
    value of the sample, the expected value and the range:

    - weekday-median and weekday-iqr: the values of the same weekday.
    - weekday-trend: the ratios of the same weekday. The level of a day is the average of the
      7 days before it, when all 7 have a value and the average is above 0. The ratio of a day
      is its value divided by its level. The method multiplies its range by the day's level.

    The width of each range is a factor of the method for $sensitivity, from _factor_sql().

    The same guards then apply to every method:

    1. A day with fewer than MIN_SAMPLE values in its sample gets no range and no flag.
    2. When the expected value is above 0, the range reaches at least 5 percent above and
       below it.
    3. When no value in the sample is below 0, the lower limit is not below 0.
    4. A day is flagged only when its value is finite, the expected value is MIN_USUAL or
       more, and the value is above the upper limit or below the lower limit.

    Days outside the 91 days keep their value but get no range and no flag. The anomaly_days
    macro in views.sql has the same rules, and tests check that both agree.
    """
    by_median, by_iqr, by_trend = (
        _factor_sql(k) for k in ("weekday-median", "weekday-iqr", "weekday-trend")
    )
    return f"""
m_src AS (
    SELECT d.run_id, d.component_id, d.day, ru.today,
        CASE WHEN isfinite(d.value) THEN d.value END AS value
    FROM {source}
),
m_leveled AS (
    SELECT run_id, component_id, day, value,
        CASE WHEN count(value) OVER w7 = 7 AND avg(value) OVER w7 > 0
            THEN avg(value) OVER w7 END AS level
    FROM m_src
    WHERE day BETWEEN today - 91 AND today - 1
    WINDOW w7 AS (PARTITION BY run_id, component_id ORDER BY day
        RANGE BETWEEN INTERVAL 7 DAY PRECEDING AND INTERVAL 1 DAY PRECEDING)
),
m_sampled AS (
    SELECT run_id, component_id, day,
        CASE $method
            WHEN 'weekday-median' THEN {{
                'n': count(value) OVER w, 'lowest': min(value) OVER w,
                'expected': median(value) OVER w,
                'upper': median(value) OVER w + {by_median} * 1.4826 * mad(value) OVER w,
                'lower': median(value) OVER w - {by_median} * 1.4826 * mad(value) OVER w}}
            WHEN 'weekday-iqr' THEN {{
                'n': count(value) OVER w, 'lowest': min(value) OVER w,
                'expected': median(value) OVER w,
                'upper': quantile_cont(value, 0.75) OVER w
                    + {by_iqr} * (quantile_cont(value, 0.75) OVER w - quantile_cont(value, 0.25) OVER w),
                'lower': quantile_cont(value, 0.25) OVER w
                    - {by_iqr} * (quantile_cont(value, 0.75) OVER w - quantile_cont(value, 0.25) OVER w)}}
            WHEN 'weekday-trend' THEN {{
                'n': count(value / level) OVER w, 'lowest': min(value / level) OVER w,
                'expected': level * median(value / level) OVER w,
                'upper': level * (median(value / level) OVER w
                    + {by_trend} * 1.4826 * mad(value / level) OVER w),
                'lower': level * (median(value / level) OVER w
                    - {by_trend} * 1.4826 * mad(value / level) OVER w)}}
        END AS rng
    FROM m_leveled
    WINDOW w AS (PARTITION BY run_id, component_id, dayofweek(day) ORDER BY day
        RANGE BETWEEN UNBOUNDED PRECEDING AND INTERVAL 1 DAY PRECEDING)
),
m_wide AS (
    SELECT run_id, component_id, day, rng.lowest AS lowest,
        CASE WHEN rng.n >= {MIN_SAMPLE} THEN rng.expected END AS expected,
        CASE WHEN rng.n >= {MIN_SAMPLE} THEN CASE WHEN rng.expected > 0
            THEN greatest(rng.upper, rng.expected * 1.05) ELSE rng.upper END END AS upper,
        CASE WHEN rng.n >= {MIN_SAMPLE} THEN CASE WHEN rng.expected > 0
            THEN least(rng.lower, rng.expected * 0.95) ELSE rng.lower END END AS lower
    FROM m_sampled
),
m_local AS (
    SELECT run_id, component_id, day, expected, upper,
        CASE WHEN lowest >= 0 AND lower < 0 THEN 0 ELSE lower END AS lower
    FROM m_wide
),
dv AS (
    SELECT s.run_id, s.component_id, s.day, s.today, s.value, g.expected, g.upper, g.lower,
        coalesce(s.value IS NOT NULL AND g.expected >= {MIN_USUAL}
            AND (s.value > g.upper OR s.value < g.lower), false) AS anomaly
    FROM m_src s
    LEFT JOIN m_local g ON g.run_id = s.run_id AND g.component_id = s.component_id
        AND g.day = s.day
)"""


# The values of one run for a local method, in place of _DV.
_LOCAL_RUN_DV = _local_dv(
    "daily_values d JOIN runs ru ON ru.run_id = d.run_id WHERE d.run_id = $run_id"
)


def _method(key: str) -> AnomalyMethod:
    for m in _METHODS:
        if m.key == key:
            return m
    raise ValueError(f"Unknown anomaly method: {key!r}. Use one of: {', '.join(ANOMALY_METHODS)}.")


def _check_sensitivity(method: AnomalyMethod, sensitivity: str) -> None:
    """Fail for an unknown sensitivity, and for one other than normal with Adobe's method."""
    if sensitivity not in SENSITIVITIES:
        raise ValueError(
            f"Unknown anomaly sensitivity: {sensitivity!r}. Use one of: {', '.join(SENSITIVITIES)}."
        )
    if method.factors is None and sensitivity != DEFAULT_SENSITIVITY:
        raise ValueError(
            "The sensitivity applies only to a local anomaly method. Adobe returns its range."
        )


def method_data(
    key: str = DEFAULT_ANOMALY_METHOD, sensitivity: str = DEFAULT_SENSITIVITY
) -> dict[str, Any]:
    """The anomaly_method entry of the page data: the method's key, label, rule and sample,
    whether it is Adobe's, and the sensitivity. The page names the method only through this
    entry.

    label is the name of the method, which the page puts in sentences, e.g., "Flagged by the
    Weekday median method". title is the name for the header, which also names a sensitivity
    other than normal, e.g., "Weekday median, high sensitivity". rule names the factor in use.
    """
    m = _method(key)
    _check_sensitivity(m, sensitivity)
    rule = m.rule
    title = m.label
    if m.factors is not None:
        factor = float(m.factor(sensitivity))
        rule = rule.format(times="" if factor == 1 else f"{factor:g} times ")
        if sensitivity != DEFAULT_SENSITIVITY:
            title = f"{m.label}, {sensitivity} sensitivity"
    return {
        "key": m.key,
        "label": m.label,
        "title": title,
        "adobe": m.key == DEFAULT_ANOMALY_METHOD,
        "sensitivity": sensitivity,
        "rule": rule,
        "sample": m.sample,
    }


def _for_method(sql: str, method: str, adobe_dv: str, local_dv: str) -> str:
    """The query with the dv CTE of the method in place of Adobe's.

    The query must hold Adobe's dv CTE exactly once. Otherwise a local method would show
    Adobe's values under its own name, so this fails instead.
    """
    if method == DEFAULT_ANOMALY_METHOD:
        return sql
    if sql.count(adobe_dv) != 1:
        raise RuntimeError(
            f"The query must hold the dv part exactly once to use the anomaly method {method!r}."
        )
    return sql.replace(adobe_dv, local_dv)


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
# Each row also says since when its alert has held, by the rules of the alerts macro in
# views.sql. An alert follows one condition: a value of 0 when its value is 0, else the
# direction of its anomaly flag, else a change past the drop or spike slider. Its streak is the
# unbroken run of days, up to the row's day, on which that condition held.
#
# A value of 0 and a flag do not depend on a slider, so this query counts their days in
# since_days. since_open says that the day before the streak cannot be judged, so the alert
# may have started earlier: the day is not in the run's 91 days or has no value, or, for a
# flag, the method gave it no expected value. A large drop or spike depends on its slider, so
# changes lists the change of each day from the row's day back to the first day the reader can
# pick, newest first, and report.js counts the days past the slider. The day before the first
# of them has no weekday average, so it cannot be judged. In changes, a day without a value is
# empty, and a day whose weekday average is under MIN_USUAL is 0, because it is never a drop or
# a spike. The list stops at the first day that no slider setting counts, to keep the page
# small.
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
-- Each row with a value of 0 or a flag, with each day from its own day back to the day before
-- the run's 91 days.
back AS (
    SELECT r.sel_day, r.component_id, CASE WHEN r.zero THEN 'zero' ELSE r.anomaly END AS condition,
        r.sel_day - b.n::INTEGER AS day
    FROM r, range(0, 93) b(n)
    WHERE (r.zero OR r.anomaly IS NOT NULL) AND r.sel_day - b.n::INTEGER >= $start - 1
),
judged AS (
    SELECT b.sel_day, b.component_id, b.day,
        CASE
            WHEN b.condition = 'zero' AND d.value = 0 THEN 'yes'
            WHEN b.condition <> 'zero' AND {_ANOMALY.format(a="d")} = b.condition THEN 'yes'
            WHEN d.value IS NULL THEN 'unknown'
            WHEN b.condition <> 'zero' AND d.expected IS NULL THEN 'unknown'
            ELSE 'no'
        END AS state
    FROM back b
    LEFT JOIN dv d ON d.component_id = b.component_id AND d.day = b.day
        AND d.day BETWEEN $start AND $yesterday
),
-- The last day before each streak. The day before the run's 91 days is always unknown.
breaks AS (
    SELECT sel_day, component_id, max(day) AS day, arg_max(state, day) AS state
    FROM judged
    WHERE state <> 'yes'
    GROUP BY sel_day, component_id
),
-- The change of each day back from a row that may be a large drop or spike.
back_change AS (
    SELECT r.sel_day, r.component_id, s.sel_day AS day, r.pct_vs_usual < 0 AS drop,
        CASE WHEN d.value IS NULL THEN NULL
            WHEN u.usual >= {MIN_USUAL} THEN {_pct("d.value", "u.usual")} ELSE 0 END AS change
    FROM r JOIN sel s ON s.sel_day <= r.sel_day
    JOIN usual u ON u.sel_day = s.sel_day AND u.component_id = r.component_id
    LEFT JOIN dv d ON d.component_id = r.component_id AND d.day = s.sel_day
    WHERE NOT r.zero AND r.anomaly IS NULL AND r.usual >= {MIN_USUAL}
        AND (r.pct_vs_usual < -{MIN_CHANGE_ALERT} OR r.pct_vs_usual > {MIN_SPIKE_ALERT})
),
-- No slider setting counts a day that is empty or not past the slider's lowest setting, so
-- the list stops at the newest such day. That day is empty, or 0 when it has a value.
cut AS (
    SELECT *, max(CASE WHEN change IS NULL
            OR NOT (CASE WHEN drop THEN change < -{MIN_CHANGE_ALERT}
                ELSE change > {MIN_SPIKE_ALERT} END) THEN day END)
        OVER (PARTITION BY sel_day, component_id) AS stop
    FROM back_change
),
changes AS (
    SELECT sel_day, component_id,
        list(CASE WHEN day = stop AND change IS NOT NULL THEN 0 ELSE change END
            ORDER BY day DESC) AS changes
    FROM cut
    WHERE stop IS NULL OR day >= stop
    GROUP BY sel_day, component_id
)
SELECT CAST(r.sel_day AS VARCHAR) AS day, name, r.component_id, metric_type, expected, actual,
    usual, pct_vs_expected, r.pct_vs_usual, anomaly, zero, unused,
    r.sel_day - b.day AS since_days, b.state = 'unknown' AS since_open, ch.changes
FROM r
LEFT JOIN breaks b ON b.sel_day = r.sel_day AND b.component_id = r.component_id
LEFT JOIN changes ch ON ch.sel_day = r.sel_day AND ch.component_id = r.component_id
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

# The daily values of every listed run, with Adobe's flags.
_HISTORY_DV = """
dv AS (
    SELECT d.run_id, d.component_id, d.day, r.today,
        CASE WHEN isfinite(d.value) THEN d.value END AS value,
        CASE WHEN isfinite(d.upper) THEN d.upper END AS upper,
        d.anomaly
    FROM daily_values d JOIN r ON r.run_id = d.run_id
)"""
# The values of every listed run for a local method, in place of _HISTORY_DV.
_LOCAL_HISTORY_DV = _local_dv("daily_values d JOIN r ru ON ru.run_id = d.run_id")

# The alerts on the last day of each run of the same report suite and segment, by the same
# rules as the tiles: one query over every run's stored values. Each run uses its own dates.
# The runs are this one and the runs for earlier run dates, newest date first, and for this
# run's date, the runs made up to this one. "Made up to" goes by the time a run started, not
# by its number, as in the latest_runs view: an old run that ahd import gave a new number has
# a higher number than the runs made after it. A run made later with --date for an earlier
# date is listed too. drops lists the change of every candidate for a large drop, so the page can
# count them for the slider's setting. A failed or unfinished run is left out, unless it is
# this one.
_HISTORY_SQL = f"""
WITH cur AS (SELECT rsid, segment_id, today, started_at FROM runs WHERE run_id = $run_id),
r AS (
    SELECT r.run_id, r.today, r.status, r.started_at
    FROM runs r, cur
    WHERE r.rsid = cur.rsid AND r.segment_id IS NOT DISTINCT FROM cur.segment_id
        AND (r.today < cur.today OR (r.today = cur.today AND (r.started_at < cur.started_at
            OR (r.started_at IS NOT DISTINCT FROM cur.started_at AND r.run_id <= $run_id))))
        AND (r.status IN ('ok', 'partial') OR r.run_id = $run_id)
    ORDER BY r.today DESC, r.started_at DESC, r.run_id DESC
    LIMIT {HISTORY_RUNS}
),{_HISTORY_DV},
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
GROUP BY r.run_id, r.today, r.status, r.started_at
ORDER BY r.today DESC, r.started_at DESC, r.run_id DESC
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
        tables: dict[str, dict[str, list[Any]]] = {
            k: {"labels": [], "rows": [], "totals": []} for k in ("browser", "device")
        }
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
    method: str = DEFAULT_ANOMALY_METHOD,
    sensitivity: str = DEFAULT_SENSITIVITY,
) -> dict[str, Any]:
    """The page data of a run. method picks the anomaly method, one of ANOMALY_METHODS.

    sensitivity, one of SENSITIVITIES, sets the width of the range of a local method. Adobe's
    method takes only the default.
    """
    chosen = _method(method)
    _check_sensitivity(chosen, sensitivity)
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
        "method": method,
        "sensitivity": sensitivity,
    }

    def flagged(sql: str) -> str:
        """A query that reads the expected values or the flags, for the chosen method."""
        return _for_method(sql, method, _DV, _LOCAL_RUN_DV)

    views: dict[str, list[dict[str, Any]]] = {f"day:{d.isoformat()}": [] for d in w.selectable_days}
    for row in _rows(store, flagged(_DAY_SQL), p):
        views[f"day:{row.pop('sel_day')}"].append(row)
    views["week"] = _rows(store, _WEEK_SQL, p)

    series: dict[str, list[list[Any]]] = {}
    for row in _rows(store, flagged(_SERIES_SQL), p):
        series.setdefault(row["component_id"], []).append(
            [row["day"], row["value"], row["expected"], row["upper"], row["lower"], row["anomaly"]]
        )

    history_sql = _for_method(_HISTORY_SQL, method, _HISTORY_DV, _LOCAL_HISTORY_DV)
    return {
        "run": run,
        "anomaly_method": method_data(chosen.key, sensitivity),
        "views": views,
        "series": series,
        "alerts": _rows(store, flagged(_ALERTS_SQL), p),
        "calendar": _rows(store, flagged(_CALENDAR_SQL), p),
        "history": _rows(store, history_sql, p),
        "declines": _rows(store, _DECLINE_SQL, p),
        "pairs": _pairs(store, run_id, p),
        "issues": _rows(store, _ISSUES_SQL, p),
        "component_drops": component_drops(store, run_id),
        "change_alert": change_alert,
        "spike_alert": spike_alert,
        "min_usual": MIN_USUAL,
        "event_share": EVENT_SHARE,
        "event_min": EVENT_MIN,
        "component_drop_cause": DROP_CAUSE,
    }
