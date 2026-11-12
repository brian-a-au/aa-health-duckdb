-- Views and table macros stored in every ahd database, for your own SQL.
-- They follow the same rules as the report. See docs/duckdb.md for examples.
--
-- The views are latest_runs and component_days. The macro alerts lists the alerts of a run.
-- It reads its expected values and flags from the macro anomaly_days, which applies the
-- anomaly method that alerts is given.
--
-- ahd runs this file again on an existing file when VIEWS_VERSION in store.py is raised, so
-- every statement must replace what it creates. To remove a view or a macro, add a
-- DROP ... IF EXISTS statement for it. The views may only use columns the tables already have.

-- The latest run is the one with the newest run date. A run number does not say which run is
-- newer: a run made later with --date for an old date has a higher number, and so does an old
-- run that ahd import gave a new number. For runs of the same date, the one made last wins.
CREATE OR REPLACE VIEW latest_runs AS
FROM runs
WHERE status IN ('ok', 'partial')
QUALIFY row_number() OVER (PARTITION BY rsid, coalesce(segment_id, '')
    ORDER BY today DESC, started_at DESC, run_id DESC) = 1;

CREATE OR REPLACE VIEW component_days AS
WITH dv AS (
    SELECT d.run_id, d.component_id, d.day, r.today,
        CASE WHEN isfinite(d.value) THEN d.value END AS value,
        CASE WHEN isfinite(d.expected) THEN d.expected END AS expected,
        CASE WHEN isfinite(d.lower) THEN d.lower END AS lower,
        CASE WHEN isfinite(d.upper) THEN d.upper END AS upper,
        d.anomaly
    FROM daily_values d JOIN runs r USING (run_id)
    WHERE d.day BETWEEN r.today - 91 AND r.today - 1
),
usual AS (
    SELECT run_id, component_id, dayofweek(day) AS dow, sum(value) / 12 AS usual
    FROM dv WHERE day <= today - 8
    GROUP BY ALL
),
used AS (
    SELECT run_id, component_id, bool_or(value <> 0) AS had_data FROM dv GROUP BY ALL
)
SELECT dv.run_id, dv.day, dv.day = dv.today - 1 AS is_yesterday,
    c.component_id, c.name, c.kind, c."group", c.number, c.metric_type,
    dv.value, dv.expected, dv.lower, dv.upper,
    CASE WHEN dv.anomaly THEN CASE WHEN dv.value > dv.upper THEN 'high' ELSE 'low' END END
        AS anomaly,
    -- Only the last 7 days have a weekday average, from the 12 weeks before them. An older day
    -- falls inside those weeks, so it has none, as in the report.
    CASE WHEN dv.day >= dv.today - 7 THEN coalesce(u.usual, 0) END AS usual,
    -- Rounded so that a drop of exactly the threshold is not "more than" it, as in the report.
    CASE WHEN dv.day >= dv.today - 7 AND u.usual <> 0 THEN round(dv.value / u.usual - 1, 9) END
        AS pct_vs_usual,
    coalesce(dv.value = 0, false) AS zero,
    NOT coalesce(used.had_data, false) AS unused
FROM dv
JOIN components c ON c.run_id = dv.run_id AND c.component_id = dv.component_id
LEFT JOIN usual u ON u.run_id = dv.run_id AND u.component_id = dv.component_id
    AND u.dow = dayofweek(dv.day)
LEFT JOIN used ON used.run_id = dv.run_id AND used.component_id = dv.component_id;

-- The expected value, the expected range and the anomaly flag of each component and day of one
-- run, by an anomaly method. It covers the same days as component_days.
--
-- 'adobe' gives the values that Adobe returned, as component_days does. The other methods
-- judge each day by the earlier days of the same weekday in the run. Their sample is the
-- finite values of those days. Each method gives the expected value and the range from the
-- sample, and the same guards then apply to every method:
--
-- 1. A day with fewer than 8 values in its sample gets no range and no flag. The number must
--    match MIN_SAMPLE in src/ahd/summary.py.
-- 2. When the expected value is above 0, the range reaches at least 5 percent above and
--    below it.
-- 3. When no value in the sample is below 0, the lower limit is not below 0.
-- 4. A day is flagged only when its value is finite, the expected value is min_usual or more,
--    and the value is above the upper limit or below the lower limit.
--
-- src/ahd/summary.py holds the same rules for the report, and tests check that both agree.
CREATE OR REPLACE MACRO anomaly_days(run_number, method := 'adobe', min_usual := 10) AS TABLE
-- An unknown method stops the query with an error. DuckDB reads the argument of range() before
-- it reads any table, so this also happens for a run without values.
WITH checked AS (
    SELECT method AS chosen
    FROM range(CASE WHEN method IN ('adobe', 'weekday-median', 'weekday-iqr') THEN 1
        ELSE error(concat('Unknown anomaly method: ', method,
            '. Use adobe, weekday-median or weekday-iqr.')) END)
),
dv AS (
    SELECT d.run_id, d.component_id, d.day,
        CASE WHEN isfinite(d.value) THEN d.value END AS value,
        CASE WHEN isfinite(d.expected) THEN d.expected END AS expected,
        CASE WHEN isfinite(d.lower) THEN d.lower END AS lower,
        CASE WHEN isfinite(d.upper) THEN d.upper END AS upper,
        d.anomaly
    FROM daily_values d JOIN runs r USING (run_id)
    WHERE d.run_id = run_number AND d.day BETWEEN r.today - 91 AND r.today - 1
),
-- The sample of a day is in the window w. Each method is one branch of the CASE.
sampled AS (
    SELECT run_id, component_id, day, value,
        count(value) OVER w AS n,
        min(value) OVER w AS lowest,
        CASE checked.chosen
            -- The median, and 3 spreads on each side. The spread is 1.4826 times the median
            -- distance from the median.
            WHEN 'weekday-median' THEN {
                'expected': median(value) OVER w,
                'upper': median(value) OVER w + 3 * 1.4826 * mad(value) OVER w,
                'lower': median(value) OVER w - 3 * 1.4826 * mad(value) OVER w}
            -- The median, and 1.5 times the distance between the quartiles past each quartile.
            WHEN 'weekday-iqr' THEN {
                'expected': median(value) OVER w,
                'upper': quantile_cont(value, 0.75) OVER w
                    + 1.5 * (quantile_cont(value, 0.75) OVER w - quantile_cont(value, 0.25) OVER w),
                'lower': quantile_cont(value, 0.25) OVER w
                    - 1.5 * (quantile_cont(value, 0.75) OVER w - quantile_cont(value, 0.25) OVER w)}
        END AS rng
    FROM dv, checked
    WHERE checked.chosen <> 'adobe'
    WINDOW w AS (PARTITION BY run_id, component_id, dayofweek(day) ORDER BY day
        RANGE BETWEEN UNBOUNDED PRECEDING AND INTERVAL 1 DAY PRECEDING)
),
-- Guards 1 and 2. The 8 of guard 1 must match MIN_SAMPLE in src/ahd/summary.py.
wide AS (
    SELECT run_id, component_id, day, value, lowest,
        CASE WHEN n >= 8 THEN rng.expected END AS expected,
        CASE WHEN n >= 8 THEN CASE WHEN rng.expected > 0
            THEN greatest(rng.upper, rng.expected * 1.05) ELSE rng.upper END END AS upper,
        CASE WHEN n >= 8 THEN CASE WHEN rng.expected > 0
            THEN least(rng.lower, rng.expected * 0.95) ELSE rng.lower END END AS lower
    FROM sampled
),
-- Guard 3.
local AS (
    SELECT run_id, component_id, day, value, expected, upper,
        CASE WHEN lowest >= 0 AND lower < 0 THEN 0 ELSE lower END AS lower
    FROM wide
)
SELECT run_id, component_id, day, expected, lower, upper,
    CASE WHEN anomaly THEN CASE WHEN value > upper THEN 'high' ELSE 'low' END END AS anomaly
FROM dv, checked
WHERE checked.chosen = 'adobe'
UNION ALL
-- Guard 4.
SELECT run_id, component_id, day, expected, lower, upper,
    CASE WHEN value IS NOT NULL AND expected >= min_usual AND (value > upper OR value < lower)
        THEN CASE WHEN value > upper THEN 'high' ELSE 'low' END END AS anomaly
FROM local;

-- The alerts of one run on one day, by the same rules as the report. method picks the anomaly
-- method, as in anomaly_days.
CREATE OR REPLACE MACRO alerts(run_number, threshold := 0.30, spike_threshold := 0.50, min_usual := 10,
    for_day := NULL, method := 'adobe') AS TABLE
WITH days AS (
    SELECT c.run_id, c.day, c.is_yesterday, c.component_id, c.name, c."group", c.metric_type,
        c.value, c.usual, a.expected, c.pct_vs_usual, c.zero, c.unused, a.anomaly
    FROM component_days c
    JOIN anomaly_days(run_number, method := method, min_usual := min_usual) a
        ON a.run_id = c.run_id AND a.component_id = c.component_id AND a.day = c.day
    WHERE c.run_id = run_number
)
SELECT run_id, day, component_id, name, "group", metric_type, value, usual, expected,
    pct_vs_usual,
    CASE
        WHEN anomaly IS NOT NULL THEN 'unusual'
        WHEN zero THEN 'dropped to zero'
        WHEN pct_vs_usual > 0 THEN 'large spike'
        ELSE 'large drop'
    END AS alert,
    anomaly
FROM days
WHERE (CASE WHEN for_day IS NULL THEN is_yesterday ELSE day = for_day::DATE END)
    AND NOT (zero AND unused)
    AND (anomaly IS NOT NULL OR zero OR (usual >= min_usual
        AND (pct_vs_usual < -threshold OR pct_vs_usual > spike_threshold)))
ORDER BY alert DESC, "group", component_id;
