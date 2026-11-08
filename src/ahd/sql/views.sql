-- Views and a table macro stored in every ahd database, for your own SQL.
-- They follow the same rules as the report. See docs/duckdb.md for examples.

CREATE VIEW latest_runs AS
FROM runs
WHERE status IN ('ok', 'partial')
QUALIFY row_number() OVER (PARTITION BY rsid, coalesce(segment_id, '') ORDER BY run_id DESC) = 1;

CREATE VIEW component_days AS
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
    coalesce(u.usual, 0) AS usual,
    -- Rounded so that a drop of exactly the threshold is not "more than" it, as in the report.
    CASE WHEN u.usual <> 0 THEN round(dv.value / u.usual - 1, 9) END AS pct_vs_usual,
    coalesce(dv.value = 0, false) AS zero,
    NOT coalesce(used.had_data, false) AS unused
FROM dv
JOIN components c ON c.run_id = dv.run_id AND c.component_id = dv.component_id
LEFT JOIN usual u ON u.run_id = dv.run_id AND u.component_id = dv.component_id
    AND u.dow = dayofweek(dv.day)
LEFT JOIN used ON used.run_id = dv.run_id AND used.component_id = dv.component_id;

CREATE MACRO alerts(run_number, threshold := 0.30, min_usual := 10, for_day := NULL) AS TABLE
SELECT run_id, day, component_id, name, "group", metric_type, value, usual, expected,
    pct_vs_usual,
    CASE
        WHEN anomaly IS NOT NULL THEN 'unusual'
        WHEN zero THEN 'dropped to zero'
        ELSE 'large drop'
    END AS alert,
    anomaly
FROM component_days
WHERE run_id = run_number
    AND (CASE WHEN for_day IS NULL THEN is_yesterday ELSE day = for_day::DATE END)
    AND NOT (zero AND unused)
    AND (anomaly IS NOT NULL OR zero OR (usual >= min_usual AND pct_vs_usual < -threshold))
ORDER BY alert DESC, "group", component_id;
