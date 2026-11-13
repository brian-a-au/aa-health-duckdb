-- Views and table macros stored in every ahd database, for your own SQL.
-- They follow the same rules as the report. See docs/duckdb.md for examples.
--
-- The views are latest_runs and component_days. The macro alerts lists the alerts of a run.
-- It reads its expected values and flags from the macro anomaly_days, which applies the
-- anomaly method that alerts is given. The macro suite_events groups the alerts of a run into
-- suite-wide events.
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
-- run, by an anomaly method. It covers the same components and days as component_days. The
-- pairs of the Individual Component Summary, e.g., individual/1, have daily values but are not
-- components, so it leaves them out by the start of their ID, as src/ahd/summary.py does.
--
-- 'adobe' gives the values that Adobe returned, as component_days does. The other methods
-- judge each day by earlier days in the run. Each takes its own sample of a day and gives from
-- it the sample size n, the lowest value of the sample, the expected value and the range:
--
-- - 'weekday-median' and 'weekday-iqr': the finite values of the same weekday.
-- - 'weekday-trend': the ratios of the same weekday. The level of a day is the average of the
--   7 days before it, when all 7 have a finite value and the average is above 0. The ratio of
--   a day is its value divided by its level. The method multiplies its range by the day's
--   level.
--
-- sensitivity sets the width factor of the range: 'low' is 4/3 of 'normal' and 'high' is 2/3
-- of it. Each branch picks its factor with a CASE on sensitivity, in the place where ahd 0.1.8
-- had the number, so 'normal' gives the same results as 0.1.8. The factors must match those of
-- _METHODS in src/ahd/summary.py. 'adobe' takes any of the three and does not change, because
-- Adobe returns its range.
--
-- The same guards then apply to every method:
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
CREATE OR REPLACE MACRO anomaly_days(run_number, method := 'adobe', min_usual := 10,
    sensitivity := 'normal') AS TABLE
-- An unknown method or sensitivity stops the query with an error. DuckDB reads the argument of
-- range() before it reads any table, so this also happens for a run without values.
WITH checked AS (
    SELECT method AS chosen
    FROM range(CASE WHEN method IN ('adobe', 'weekday-median', 'weekday-iqr', 'weekday-trend')
            THEN CASE WHEN sensitivity IN ('low', 'normal', 'high') THEN 1
                ELSE error(concat('Unknown anomaly sensitivity: ', sensitivity,
                    '. Use low, normal or high.')) END
        ELSE error(concat('Unknown anomaly method: ', method,
            '. Use adobe, weekday-median, weekday-iqr or weekday-trend.')) END)
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
        AND NOT starts_with(d.component_id, 'individual/')
),
-- The level of each day, for weekday-trend.
leveled AS (
    SELECT run_id, component_id, day, value, checked.chosen,
        CASE WHEN count(value) OVER w7 = 7 AND avg(value) OVER w7 > 0
            THEN avg(value) OVER w7 END AS level
    FROM dv, checked
    WHERE checked.chosen <> 'adobe'
    WINDOW w7 AS (PARTITION BY run_id, component_id ORDER BY day
        RANGE BETWEEN INTERVAL 7 DAY PRECEDING AND INTERVAL 1 DAY PRECEDING)
),
-- The sample of a day is in the window w. Each method is one branch of the CASE.
sampled AS (
    SELECT run_id, component_id, day, value,
        CASE chosen
            -- The median, and 4, 3 or 2 spreads on each side. The spread is 1.4826 times the
            -- median distance from the median.
            WHEN 'weekday-median' THEN {
                'n': count(value) OVER w, 'lowest': min(value) OVER w,
                'expected': median(value) OVER w,
                'upper': median(value) OVER w
                    + (CASE sensitivity
                        WHEN 'low' THEN 4 WHEN 'normal' THEN 3 WHEN 'high' THEN 2 END)
                    * 1.4826 * mad(value) OVER w,
                'lower': median(value) OVER w
                    - (CASE sensitivity
                        WHEN 'low' THEN 4 WHEN 'normal' THEN 3 WHEN 'high' THEN 2 END)
                    * 1.4826 * mad(value) OVER w}
            -- The median, and 2, 1.5 or 1 times the distance between the quartiles past each
            -- quartile.
            WHEN 'weekday-iqr' THEN {
                'n': count(value) OVER w, 'lowest': min(value) OVER w,
                'expected': median(value) OVER w,
                'upper': quantile_cont(value, 0.75) OVER w
                    + (CASE sensitivity
                        WHEN 'low' THEN 2.0 WHEN 'normal' THEN 1.5 WHEN 'high' THEN 1.0 END)
                    * (quantile_cont(value, 0.75) OVER w - quantile_cont(value, 0.25) OVER w),
                'lower': quantile_cont(value, 0.25) OVER w
                    - (CASE sensitivity
                        WHEN 'low' THEN 2.0 WHEN 'normal' THEN 1.5 WHEN 'high' THEN 1.0 END)
                    * (quantile_cont(value, 0.75) OVER w - quantile_cont(value, 0.25) OVER w)}
            -- As weekday-median, with the ratios to the level, times the level of the day.
            WHEN 'weekday-trend' THEN {
                'n': count(value / level) OVER w, 'lowest': min(value / level) OVER w,
                'expected': level * median(value / level) OVER w,
                'upper': level * (median(value / level) OVER w
                    + (CASE sensitivity
                        WHEN 'low' THEN 4 WHEN 'normal' THEN 3 WHEN 'high' THEN 2 END)
                    * 1.4826 * mad(value / level) OVER w),
                'lower': level * (median(value / level) OVER w
                    - (CASE sensitivity
                        WHEN 'low' THEN 4 WHEN 'normal' THEN 3 WHEN 'high' THEN 2 END)
                    * 1.4826 * mad(value / level) OVER w)}
        END AS rng
    FROM leveled
    WINDOW w AS (PARTITION BY run_id, component_id, dayofweek(day) ORDER BY day
        RANGE BETWEEN UNBOUNDED PRECEDING AND INTERVAL 1 DAY PRECEDING)
),
-- Guards 1 and 2. The 8 of guard 1 must match MIN_SAMPLE in src/ahd/summary.py.
wide AS (
    SELECT run_id, component_id, day, value, rng.lowest AS lowest,
        CASE WHEN rng.n >= 8 THEN rng.expected END AS expected,
        CASE WHEN rng.n >= 8 THEN CASE WHEN rng.expected > 0
            THEN greatest(rng.upper, rng.expected * 1.05) ELSE rng.upper END END AS upper,
        CASE WHEN rng.n >= 8 THEN CASE WHEN rng.expected > 0
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
-- method and sensitivity the width of its range, as in anomaly_days.
--
-- The last four columns say since when each alert has held. An alert follows one condition:
-- a value of 0 when its value is 0, else the direction of its anomaly flag, else a change past
-- the drop or spike threshold. since is the first day of the unbroken run of days, up to the
-- alert's day, on which that condition held, and days counts them. The label of the alert may
-- change inside the run, e.g., from unusual to dropped to zero, while the value stays 0.
--
-- at_least is true when the day before since cannot be judged for the condition, so the alert
-- may have started earlier. That day cannot be judged when it is not in the run's 91 days or
-- has no value, for a flag when the method gave it no expected value, and for a drop or spike
-- when it is older than the last 7 days, which are the only days with a weekday average.
-- is_new is true for an alert that started on its own day: days is 1 and at_least is false.
--
-- The last column, event, names the suite-wide event that the alert belongs to, or is NULL. An
-- event is a group of alerts of the same condition that holds at least 10 alerts and at least
-- half of the components that collected data in the run. event is the name of the condition:
-- zero, low, high, drop or spike. The macro suite_events gives one row for each event. The
-- numbers 10 and 0.5 must match EVENT_MIN and EVENT_SHARE in src/ahd/summary.py.
--
-- src/ahd/summary.py and src/ahd/render/static/report.js hold the same rules for the report,
-- and tests check that all three agree.
CREATE OR REPLACE MACRO alerts(run_number, threshold := 0.30, spike_threshold := 0.50, min_usual := 10,
    for_day := NULL, method := 'adobe', sensitivity := 'normal') AS TABLE
WITH days AS (
    SELECT c.run_id, c.day, c.is_yesterday, c.component_id, c.name, c."group", c.metric_type,
        c.value, c.usual, a.expected, c.pct_vs_usual, c.zero, c.unused, a.anomaly
    FROM component_days c
    JOIN anomaly_days(run_number, method := method, min_usual := min_usual,
        sensitivity := sensitivity) a
        ON a.run_id = c.run_id AND a.component_id = c.component_id AND a.day = c.day
    WHERE c.run_id = run_number
),
listed AS (
    SELECT run_id, day, component_id, name, "group", metric_type, value, usual, expected,
        pct_vs_usual,
        CASE
            WHEN anomaly IS NOT NULL THEN 'unusual'
            WHEN zero THEN 'dropped to zero'
            WHEN pct_vs_usual > 0 THEN 'large spike'
            ELSE 'large drop'
        END AS alert,
        anomaly,
        -- The condition that the streak follows.
        CASE
            WHEN zero THEN 'zero'
            WHEN anomaly IS NOT NULL THEN anomaly
            WHEN pct_vs_usual > 0 THEN 'spike'
            ELSE 'drop'
        END AS condition
    FROM days
    WHERE (CASE WHEN for_day IS NULL THEN is_yesterday ELSE day = for_day::DATE END)
        AND NOT (zero AND unused)
        AND (anomaly IS NOT NULL OR zero OR (usual >= min_usual
            AND (pct_vs_usual < -threshold OR pct_vs_usual > spike_threshold)))
),
-- Each alert with each day from its own day back to the day before the run's 91 days.
back AS (
    SELECT l.component_id, l.day AS alert_day, l.condition, l.day - b.n::INTEGER AS day
    FROM listed l JOIN runs r ON r.run_id = l.run_id, range(0, 93) b(n)
    WHERE l.day - b.n::INTEGER >= r.today - 92
),
-- Whether the condition held on each of those days: yes, no, or unknown when the day cannot
-- be judged. A day that is not in component_days has no value. component_days gives a weekday
-- average only to the last 7 days.
judged AS (
    SELECT b.component_id, b.alert_day, b.day,
        CASE
            WHEN b.condition = 'zero' AND d.value = 0 THEN 'yes'
            WHEN b.condition IN ('high', 'low') AND d.anomaly = b.condition THEN 'yes'
            WHEN b.condition = 'drop' AND d.usual >= min_usual AND d.pct_vs_usual < -threshold
                THEN 'yes'
            WHEN b.condition = 'spike' AND d.usual >= min_usual
                AND d.pct_vs_usual > spike_threshold THEN 'yes'
            WHEN d.value IS NULL THEN 'unknown'
            WHEN b.condition IN ('high', 'low') AND d.expected IS NULL THEN 'unknown'
            WHEN b.condition IN ('drop', 'spike') AND d.usual IS NULL THEN 'unknown'
            ELSE 'no'
        END AS state
    FROM back b
    LEFT JOIN days d ON d.component_id = b.component_id AND d.day = b.day
),
-- The last day before each streak. The day before the run's 91 days is always unknown, so
-- every alert has one.
breaks AS (
    SELECT component_id, alert_day, max(day) AS day, arg_max(state, day) AS state
    FROM judged
    WHERE state <> 'yes'
    GROUP BY component_id, alert_day
),
-- The components that collected data in the run. The others cannot alert. The 0.5 and the 10
-- must match EVENT_SHARE and EVENT_MIN in src/ahd/summary.py.
collected AS (
    SELECT count(DISTINCT component_id) AS n FROM days WHERE NOT unused
),
grouped AS (
    SELECT condition FROM listed, collected
    GROUP BY condition, collected.n
    HAVING count(*) >= 10 AND count(*) >= 0.5 * collected.n
)
SELECT l.run_id, l.day, l.component_id, l.name, l."group", l.metric_type, l.value, l.usual,
    l.expected, l.pct_vs_usual, l.alert, l.anomaly,
    b.day + 1 AS since,
    l.day - b.day AS days,
    b.state = 'unknown' AS at_least,
    l.day - b.day = 1 AND b.state = 'no' AS is_new,
    g.condition AS event
FROM listed l
JOIN breaks b ON b.component_id = l.component_id AND b.alert_day = l.day
LEFT JOIN grouped g ON g.condition = l.condition
ORDER BY l.alert DESC, l."group", l.component_id;

-- The suite-wide events of one run on one day, from the event column of alerts, with the same
-- arguments. Each row is one event: its condition, its number of alerts, and the number of
-- components that collected data in the run. since and at_least give the start that most of
-- its alerts share, and since_alerts counts them. When two starts are shared by as many alerts,
-- the earlier one wins. new counts the alerts of the event that are new on the day. The biggest
-- event comes first, and events of the same size come by name.
CREATE OR REPLACE MACRO suite_events(run_number, threshold := 0.30, spike_threshold := 0.50,
    min_usual := 10, for_day := NULL, method := 'adobe', sensitivity := 'normal') AS TABLE
WITH listed AS (
    FROM alerts(run_number, threshold := threshold, spike_threshold := spike_threshold,
        min_usual := min_usual, for_day := for_day, method := method, sensitivity := sensitivity)
    WHERE event IS NOT NULL
),
collected AS (
    SELECT count(DISTINCT component_id) AS n
    FROM component_days WHERE run_id = run_number AND NOT unused
),
starts AS (
    SELECT event, since, at_least, count(*) AS n
    FROM listed
    GROUP BY event, since, at_least
    QUALIFY row_number() OVER (PARTITION BY event ORDER BY count(*) DESC, since, at_least) = 1
)
SELECT any_value(l.run_id) AS run_id, any_value(l.day) AS day, l.event,
    count(*) AS alerts, any_value(c.n) AS components,
    any_value(s.since) AS since, any_value(s.at_least) AS at_least,
    any_value(s.n) AS since_alerts,
    count(*) FILTER (WHERE l.is_new) AS new
FROM listed l
JOIN starts s ON s.event = l.event
CROSS JOIN collected c
GROUP BY l.event
ORDER BY alerts DESC, l.event;
