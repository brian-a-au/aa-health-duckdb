# Use the database with DuckDB

[README](../README.md) · [Usage](usage.md) · [Configuration](configuration.md) · [The report](report.md) · [How runs are stored](runs.md) · **DuckDB guide** · [Run it every day](scheduling.md) · [Troubleshooting](troubleshooting.md) · [Compared with the Excel workbook](excel-comparison.md) · [Development](development.md)

Every `ahd` run is saved in one DuckDB file. The HTML report shows one run at a time. The file holds every run, for every report suite, and you can ask it any question with SQL. This guide shows what you can do with it, from a daily alert email to an audit of unused variables across all your organizations.

Every example here was run against a real database made by `ahd` with DuckDB 2.0. The outputs are real, from Adobe's demo report suites, and shortened where they were long.

## Contents

- [Why a database beats a spreadsheet](#why-a-database-beats-a-spreadsheet)
- [Before you start](#before-you-start)
- [What is in the file](#what-is-in-the-file)
- [Ready-made views and macros](#ready-made-views-and-macros)
- [Email recipes](#email-recipes)
- [Analysis recipes](#analysis-recipes)
- [Export and share](#export-and-share)
- [Use it from Python](#use-it-from-python)
- [DuckDB features used in this guide](#duckdb-features-used-in-this-guide)

## Why a database beats a spreadsheet

The Excel workbook keeps one refresh at a time. Each refresh replaces the last one, and only Excel on Windows can open it. The DuckDB file works differently:

- **It keeps history.** Each run stays in the file, so you can compare today with last week, or follow one variable over months.
- **It holds many suites and organizations.** One query can cover every report suite in a file, and `ATTACH` adds the files of other organizations.
- **It answers new questions.** Any question you can write in SQL, e.g., "which eVars never collected data in any suite", takes one query, not a new macro.
- **It sends what matters to the right people.** You can send an alert email only when something is new, and route each alert to its owner from a simple CSV file.
- **It shares in any format.** One command writes CSV, JSON, Parquet or an Excel file, and a BI tool can read the Parquet files.
- **It runs anywhere.** DuckDB runs on macOS, Windows and Linux, from the command line, a notebook in your browser, Python, R or most BI tools.

## Before you start

### Install DuckDB 2.0 or newer

`ahd` writes its files with DuckDB 2.0. An older `duckdb` command cannot open them. It stops with an error that starts with "Trying to read a database file with version number". Check your version:

```
duckdb --version
```

To install or update it, use one of these:

```
brew install duckdb                      # macOS with Homebrew
curl https://install.duckdb.org | sh     # macOS or Linux
winget install DuckDB.cli                # Windows
```

If `duckdb --version` still shows an old version after an update, another copy comes first on your `PATH`. Run `which -a duckdb` to see every copy.

### Find your file

| You ran `ahd` with | The file is |
|---|---|
| no profile | `./data/ahd.duckdb` |
| `--profile prod` or `AA_PROFILE=prod` | `~/.aa/orgs/prod/ahd.duckdb` |
| `--db some/file.duckdb` | that file |

### Open it read-only

```
duckdb -readonly data/ahd.duckdb
```

`-readonly` means your queries can never change or delete data. Use it for everything in this guide.

While `ahd run`, `ahd individual`, `ahd prune` or `ahd import` writes to a file, no other program can open it. DuckDB stops with "Could not set lock on file". Wait for the command to finish, then try again. In the same way, an open `duckdb` session blocks `ahd run` until you close it.

### Pick an output format

The `duckdb` command can print a result in the format you need:

| Option | Output | Good for |
|---|---|---|
| (none) | a box table | reading on screen |
| `-markdown` | a Markdown table | GitHub, Slack, Notion |
| `-html` | HTML table rows | email |
| `-csv` | CSV | spreadsheets |
| `-json` | a JSON array | scripts and webhooks |

`-c "..."` runs one query and exits. Without it, you get an interactive prompt.

### Or use the notebook in your browser

```
duckdb -ui -cmd "ATTACH 'data/ahd.duckdb' AS ahd (READ_ONLY); USE ahd;"
```

This opens DuckDB's notebook interface in your browser, with a list of tables and a cell for each query. It installs DuckDB's `ui` extension the first time, and it loads the interface from DuckDB's website. The data stays on your computer.

- **The file must exist.** Make a run first, and put the path of your own file in the command. See [Find your file](#find-your-file). With a path that has no file, DuckDB stops with "database does not exist".
- **Do not add `-readonly` here.** The notebook saves its own notebooks in a small database in your home folder, and `-readonly` stops it from doing that. The page then shows "Initialization Error". The command above opens only your `ahd` file read-only, with `ATTACH ... (READ_ONLY)`, so your queries still cannot change it.
- **The terminal stays open.** The notebook works as long as the `duckdb` prompt in the terminal is open. Type `.quit` or press Ctrl-D there to stop it. While it is open, `ahd run` cannot write to the file.
- **The file is named `ahd` in the notebook.** It is in the list of attached databases on the left, and `USE ahd` makes it the default, so the queries in this guide work as written.

## What is in the file

Every row carries its `run_id`, so you can always tell which run it came from.

| Table | What it holds, for each run |
|---|---|
| `runs` | Report suite ID and name, segment, run date, whether `--date` set the run date, time zone, currency, the version of `ahd`, status, and start and end times |
| `components` | The events, eVars, props and solution components that were checked, with their names, groups and metric types |
| `daily_values` | One row per component per day: the value, Adobe's expected value and range, and whether Adobe flagged it |
| `pairs` | The Individual Component Summary pairs: one dimension by one metric each |
| `top_items` | The top 100 values of each pair, for yesterday and for the 90 days before |
| `breakdowns`, `breakdown_totals` | The browser and device tables of each pair |
| `issues` | Anything that could not be read, with the reason |
| `schema_version` | The version of this layout |
| `views_version` | The version of the views and the macro |

To see every column of a table, run `DESCRIBE daily_values`. To see a quick profile of every column, with minimum, maximum, averages and missing values, run `SUMMARIZE daily_values`.

## Ready-made views and macros

Every file comes with two views and two table macros. A view is a saved query that you use like a table. A table macro is a saved query that takes options. They use the same rules as the report, and the tests check that they agree.

When a new version of `ahd` changes these rules, the next `ahd run`, `ahd individual`, `ahd prune` or `ahd import` replaces the views and the macros in your file. Until then, your own SQL and the [alert email script](#email-recipes) use the old rules. If you built your own views on top of `component_days`, they follow the new rules after the update.

### `latest_runs`

The latest run of each report suite and segment that ended "ok" or "partial". Failed runs are left out.

The latest run is the one with the newest run date, which is the `today` column. The run number does not decide it. A run made later with `ahd run --date` for an old date has a higher number, and so does an old run that `ahd import` gave a new number. When two runs have the same run date, the one that was made last is the latest.

```sql
FROM latest_runs SELECT run_id, rsid_name, today, status ORDER BY run_id DESC;
```

```
┌────────┬───────────────────────┬────────────┬─────────┐
│ run_id │       rsid_name       │   today    │ status  │
├────────┼───────────────────────┼────────────┼─────────┤
│      4 │ Demo Data Evangelists │ 2026-11-12 │ partial │
│      3 │ Demo Data CJA         │ 2026-11-12 │ partial │
│      2 │ Adobe Store           │ 2026-11-12 │ ok      │
│      1 │ Demo Data             │ 2026-11-12 │ ok      │
└────────┴───────────────────────┴────────────┴─────────┘
```

### `component_days`

One row per run, component and full day, with everything the report computes. The column `usual` is the value that the report calls the weekday average.

- `value`, `expected`, `lower` and `upper` from Adobe
- `anomaly`: "high" or "low" when Adobe flagged the value, else empty. This view always has Adobe's values. For another anomaly method, use [`anomaly_days`](#anomaly_daysrun-method--adobe-min_usual--10-sensitivity--normal).
- `usual`: the weekday average, which is the average of the same weekday in the 12 weeks before the day. Only the last 7 days of the run have one, as in the report. An older day falls inside those 12 weeks, so its `usual` is empty.
- `pct_vs_usual`: the change from the weekday average, e.g., -0.25 for 25 percent below. It is empty when `usual` is empty or 0.
- `zero`: the value was 0
- `unused`: the value was 0 or empty on every day of the run, so the component never collected data
- `is_yesterday`: the row is for the last full day of the run

### `alerts(run, threshold := 0.30, spike_threshold := 0.50, min_usual := 10, for_day := NULL, method := 'adobe', sensitivity := 'normal')`

The alerts of one run, by the report's rules. Give the run number first. Give the other options by name, e.g., `alerts(1, threshold := 0.20)`.

- `run`: the run number. It can be a query, e.g., `(SELECT max(run_id) FROM latest_runs WHERE rsid = 'my_suite' AND segment_id IS NULL)`. The query must return one run. `latest_runs` holds one row for each segment of a suite, so name the segment, or use `segment_id IS NULL` for the run without a segment.
- `threshold`: the percent for a large drop, as a share. The default is `0.30`, the same as the report's drop slider.
- `spike_threshold`: the percent for a large spike, as a share. The default is `0.50`, the same as the report's spike slider. A value that doubled is a spike of about `1.0`.
- `min_usual`: a large drop or spike needs a weekday average (`usual`) of at least this much. The default is `10`.
- `for_day`: the day to check, e.g., `'2026-11-03'`. The default is the day before the run date. A large drop or spike needs one of the last 7 days before the run date. An older day can only have the alerts "unusual" and "dropped to zero", as in the report's calendar.
- `method`: the anomaly method that finds the unusual values, one of `'adobe'`, `'weekday-median'`, `'weekday-iqr'` and `'weekday-trend'`. The default is `'adobe'`, which uses Adobe's flags, as `ahd run` does by default. Another name stops the query with an error. [The report](report.md#anomaly-detection-methods) explains the methods. With a local method, `expected` is that method's expected value.
- `sensitivity`: how wide the range of a local method is, one of `'low'`, `'normal'` and `'high'`. The default is `'normal'`, the same as the report's default. `'low'` makes the range wider, so fewer values are flagged, and `'high'` makes it narrower. Another value stops the query with an error. With `'adobe'`, each of the three values is accepted and changes nothing, because Adobe returns its own range. The command line stops with an error in that case instead. [The report](report.md#the-sensitivity-of-the-local-methods) gives the factor of each level.

```sql
FROM alerts(2) SELECT alert, name, round(usual) AS usual, value;
```

```
┌─────────────┬───────────────────────┬────────┬────────┐
│    alert    │         name          │ usual  │ value  │
├─────────────┼───────────────────────┼────────┼────────┤
│ unusual     │ Exit Link Instances   │    2.0 │    8.0 │
│ unusual     │ Cart Additions        │  140.0 │  230.0 │
│ unusual     │ Orders                │   18.0 │   35.0 │
│ large spike │ Custom Link Instances │   12.0 │   41.0 │
│ large spike │ Page                  │ 4849.0 │ 8172.0 │
│ large spike │ Cart Views            │  142.0 │  270.0 │
│ large spike │ Checkouts             │   67.0 │  203.0 │
│ large spike │ Units                 │  491.0 │  838.0 │
│ large spike │ Occurrences           │ 4863.0 │ 8221.0 │
│ large spike │ Page Views            │ 4849.0 │ 8172.0 │
│ large spike │ Unique Visitors       │  341.0 │  738.0 │
│ large spike │ Visits                │  401.0 │  859.0 │
│ large spike │ Product Views         │ 2850.0 │ 4499.0 │
└─────────────┴───────────────────────┴────────┴────────┘
```

Each alert is one of these: "unusual" (the anomaly method flagged it, Adobe by default), "large spike", "large drop" or "dropped to zero".

The same alerts with the Weekday median method, which ahd computes from the run's own daily values:

```sql
FROM alerts(2, method := 'weekday-median') SELECT alert, name, round(expected) AS expected, value;
```

```
┌─────────────┬───────────────────────┬──────────┬────────┐
│    alert    │         name          │ expected │ value  │
├─────────────┼───────────────────────┼──────────┼────────┤
│ unusual     │ Custom Link Instances │     11.0 │   41.0 │
│ unusual     │ Page                  │   3614.0 │ 8172.0 │
│ unusual     │ Checkouts             │     57.0 │  203.0 │
│ unusual     │ Occurrences           │   3625.0 │ 8221.0 │
│ unusual     │ Page Views            │   3614.0 │ 8172.0 │
│ unusual     │ Unique Visitors       │    271.0 │  738.0 │
│ unusual     │ Visits                │    312.0 │  859.0 │
│ unusual     │ Product Views         │   2131.0 │ 4499.0 │
│ large spike │ Cart Additions        │    126.0 │  230.0 │
│ large spike │ Cart Views            │    122.0 │  270.0 │
│ large spike │ Orders                │     17.0 │   35.0 │
│ large spike │ Units                 │    393.0 │  838.0 │
└─────────────┴───────────────────────┴──────────┴────────┘
```

This works for a run whose credential may not use Adobe's anomaly detection, too. Adobe's flags of such a run are empty, so `alerts()` with the default method lists no unusual values.

When you query a file through `ATTACH`, run `USE` with its name before you call `alerts()` or `anomaly_days()`, e.g., `USE prod;`. The views work without it.

### `anomaly_days(run, method := 'adobe', min_usual := 10, sensitivity := 'normal')`

The expected value, the expected range and the flag of each component and day of one run, by one anomaly method. `alerts()` reads its flags from this macro. It covers the same components and days as `component_days`, which are the 91 full days of the run. It gives these columns:

- `run_id`, `component_id` and `day`
- `expected`, `lower` and `upper`: the expected value and the range. With `'adobe'`, they are Adobe's values. With a local method, they are empty on a day with fewer than 8 values in its sample, e.g., in the first 8 weeks. The sample of `'weekday-median'` and `'weekday-iqr'` is the earlier values of the same weekday. With `'weekday-trend'`, the sample is the earlier days of the same weekday that have a level, so the first 9 weeks have no range, and a day also needs a level of its own. See [The report](report.md#the-rules-of-the-local-methods).
- `anomaly`: "high" or "low" when the method flagged the value, else empty

`method` and `sensitivity` take the same values as in `alerts()`. A local method flags a value only when its expected value is at least `min_usual`. With `'adobe'`, `min_usual` and `sensitivity` change nothing.

This query counts, for each of the last 7 days, the values that Adobe flagged and the values that the Weekday median method flagged. The two methods often flag different days:

```sql
SELECT day, count(a.anomaly) AS adobe, count(m.anomaly) AS weekday_median
FROM anomaly_days(2) a
JOIN anomaly_days(2, method := 'weekday-median') m USING (run_id, component_id, day)
GROUP BY day ORDER BY day DESC LIMIT 7;
```

```
┌────────────┬───────┬────────────────┐
│    day     │ adobe │ weekday_median │
├────────────┼───────┼────────────────┤
│ 2026-11-11 │     3 │              8 │
│ 2026-11-10 │     5 │             14 │
│ 2026-11-09 │     2 │              0 │
│ 2026-11-08 │     7 │              8 │
│ 2026-11-07 │     9 │              8 │
│ 2026-11-06 │     2 │              0 │
│ 2026-11-05 │     0 │              0 │
└────────────┴───────┴────────────────┘
```

On 2026-11-11, the Weekday median method flagged 8 values. They are the 8 unusual values in the list of `alerts(2, method := 'weekday-median')` above.

## Email recipes

### Copy alerts into an email by hand

On the report's Alerts tab, click **Copy alerts**. The copy holds a formatted table and plain text. Outlook, Gmail and Apple Mail paste the table, and a chat or a text editor pastes the text. In the status section, **Copy problems** does the same for anything that could not be read.

### Paste a table into Slack, GitHub or Notion

```
duckdb -readonly data/ahd.duckdb -markdown -c "
FROM alerts((SELECT max(run_id) FROM latest_runs WHERE rsid = 'my_suite' AND segment_id IS NULL))
SELECT alert, name, round(usual) AS usual, value, round(pct_vs_usual * 100) AS pct"
```

```
|   alert    |      name       | usual | value  |  pct  |
|------------|-----------------|------:|-------:|------:|
| unusual    | Page            | 650.0 | 2714.0 | 318.0 |
| unusual    | Checkouts       | 8.0   | 68.0   | 768.0 |
| large drop | Units           | 24.0  | 16.0   | -33.0 |
```

### Send an alert email every morning

[`examples/send_alerts.py`](examples/send_alerts.py) emails the latest alerts for one or more report suites, with an HTML table and a plain text part. The email has one section for each suite that has alerts. It sends nothing when no suite has alerts. That way an empty inbox means healthy suites.

The script is in this repository. Run it from a clone, as below. Without a clone, download the file and run it with DuckDB added, e.g., `uv run --with 'duckdb>=2.0' python send_alerts.py ...`.

```
uv run python docs/examples/send_alerts.py ~/.aa/orgs/prod/ahd.duckdb my_suite
uv run python docs/examples/send_alerts.py ~/.aa/orgs/prod/ahd.duckdb my_suite other_suite
uv run python docs/examples/send_alerts.py ~/.aa/orgs/prod/ahd.duckdb --all
```

It reads the latest run of each suite without a segment, from `latest_runs`. `--all` uses every suite that has such a run in the file. Add `--segment ID` for the latest runs with that segment. The script takes only report suite IDs and segment IDs, not names, and they must match exactly. `--threshold` sets the large drop percent as a share, e.g., `--threshold 0.20` for 20 percent. The default is `0.30`. `--spike-threshold` sets the large spike percent the same way, up to `2`. The default is `0.50`.

`--anomaly-method` chooses the anomaly method that flags the unusual values, and `--anomaly-sensitivity` sets how wide the range of a local method is. They take the same values as on `ahd run`, and the defaults are the same, `adobe` and `normal`. So by default the script uses Adobe's flags. With `adobe`, `low` or `high` stops the script with exit code 1, as on the command line, because Adobe returns its own range. The email says which method flagged the unusual values, and it names the sensitivity when it is not `normal`, e.g., "The Weekday median method with high sensitivity flagged the unusual values."

Without these two options, the script reads the variables `AHD_ANOMALY_METHOD` and `AHD_ANOMALY_SENSITIVITY`, as `ahd run` does. Set them once for a scheduled job, and the reports and the email use the same method. An option wins over its variable, and an empty variable counts as not set. A value that is not a method or a sensitivity stops the script with exit code 1, and the message names the variable. See [Set the anomaly method once](usage.md#set-the-anomaly-method-once).

The script uses the `alerts()` macro in the file. The file keeps the macro of the version of `ahd` that last wrote to it. If that version does not know the chosen method or sensitivity, the script stops with exit code 1 and says to run `ahd run` once. The defaults work with the macro of any version.

Without `SMTP_HOST`, it writes `alerts.eml` in the current folder, which you can open and check first. To send, set `SMTP_HOST`, `MAIL_FROM` and `MAIL_TO`. Set `SMTP_PORT` when the server does not use 587. Set `SMTP_USER` and `SMTP_PASSWORD` when the server needs a login. The script connects without encryption and then turns it on with the STARTTLS command. It does not work with a server that needs encryption from the start, e.g., on port 465.

It exits with these codes:

- 0 when it sent or wrote a message
- 1 when something is wrong, e.g., the file holds no finished run of a suite. The alerts of the other suites are still sent. A wrong option also exits with 1 and prints the usage.
- 2 when there was nothing to send

To run the check and the email every morning on Windows, macOS or Linux, see [Run it every day](scheduling.md).

### Send only what is new since the last run

If the same variable has been broken for a week, a daily email about it adds nothing. This query keeps only alerts that were not alerts in the run before:

```sql
SET VARIABLE runs = (
    SELECT list(run_id ORDER BY today DESC, started_at DESC, run_id DESC) FROM runs
    WHERE rsid = 'my_suite' AND segment_id IS NULL AND status IN ('ok', 'partial')
);
SELECT alert, name, value
FROM alerts(getvariable('runs')[1])
WHERE component_id NOT IN (
    SELECT component_id FROM alerts(coalesce(getvariable('runs')[2], -1))
);
```

`SET VARIABLE` keeps a value for later queries, here the list of runs without a segment, with the newest run date first. When the suite has only one such run, every alert is new.

### Send each alert to its owner

Keep a CSV file that says who owns which components. Each `pattern` is a regular expression on the component ID. The first line that matches wins.

```
pattern,owner
^metrics/event(4[0-9]|9[0-9])$,commerce-team@example.com
^metrics/evar,marketing@example.com
.*,analytics@example.com
```

Then one query groups the alerts by owner, ready for one email each:

```sql
WITH owners AS (SELECT row_number() OVER () AS line, * FROM read_csv('owners.csv')),
routed AS (
    SELECT o.owner, a.*
    FROM alerts(1, for_day := '2026-11-03') a
    JOIN owners o ON regexp_matches(a.component_id, o.pattern)
    QUALIFY row_number() OVER (PARTITION BY a.component_id ORDER BY o.line) = 1
)
SELECT owner, count(*) AS alerts, list(name ORDER BY pct_vs_usual)[1:3] AS worst_three
FROM routed GROUP BY ALL ORDER BY alerts DESC;
```

```
┌───────────────────────────┬────────┬────────────────────────────────────────────────────────────┐
│           owner           │ alerts │                        worst_three                         │
├───────────────────────────┼────────┼────────────────────────────────────────────────────────────┤
│ analytics@example.com     │    186 │ [Surveys Completed, Email Error, Push Error]               │
│ marketing@example.com     │    114 │ [Service ID, Error Reason, Demandbase Company ID]          │
│ commerce-team@example.com │     15 │ [Approval Requests Denied, Approval Requests, Course View] │
└───────────────────────────┴────────┴────────────────────────────────────────────────────────────┘
```

`read_csv` reads the file directly, with no import step. `QUALIFY` keeps the first matching owner for each alert. `list(...)[1:3]` gathers the three worst drops into one cell.

### Send one digest for every suite

One row per report suite, with yesterday's alerts from the latest run of each that has no segment. Each alert counts once, by the same rules as the `alerts` macro. An Adobe flag comes first, then a value of 0, then a large drop or spike.

```sql
SELECT r.rsid_name AS suite, r.today - 1 AS day,
    count(*) FILTER (WHERE d.anomaly IS NOT NULL AND NOT (d.zero AND d.unused)) AS unusual,
    count(*) FILTER (WHERE d.zero AND NOT d.unused AND d.anomaly IS NULL) AS dropped_to_zero,
    count(*) FILTER (WHERE d.usual >= 10 AND d.pct_vs_usual < -0.30
                     AND NOT d.zero AND d.anomaly IS NULL) AS large_drops,
    count(*) FILTER (WHERE d.usual >= 10 AND d.pct_vs_usual > 0.50
                     AND NOT d.zero AND d.anomaly IS NULL) AS large_spikes,
    count(*) FILTER (WHERE d.unused) AS never_collected,
    (SELECT count(*) FROM issues i WHERE i.run_id = r.run_id) AS problems
FROM latest_runs r JOIN component_days d ON d.run_id = r.run_id AND d.is_yesterday
WHERE r.segment_id IS NULL
GROUP BY ALL ORDER BY suite;
```

```
┌─────────────┬────────────┬─────────┬─────────────────┬─────────────┬──────────────┬─────────────────┬──────────┐
│    suite    │    day     │ unusual │ dropped_to_zero │ large_drops │ large_spikes │ never_collected │ problems │
├─────────────┼────────────┼─────────┼─────────────────┼─────────────┼──────────────┼─────────────────┼──────────┤
│ Adobe Store │ 2026-11-07 │       9 │               0 │           1 │            2 │              20 │        0 │
│ Demo Data   │ 2026-11-07 │       0 │               0 │           0 │            1 │              42 │        0 │
└─────────────┴────────────┴─────────┴─────────────────┴─────────────┴──────────────┴─────────────────┴──────────┘
```

Run it with `-html` and wrap the rows in `<table>` to get an email body:

```
{ echo "<table>"; duckdb -readonly data/ahd.duckdb -html -c "..."; echo "</table>"; } > digest.html
```

## Analysis recipes

### Every organization in one query

Each profile has its own file. `ATTACH` opens several at once, and each file gets a name:

```sql
ATTACH '~/.aa/orgs/prod/ahd.duckdb' AS prod (READ_ONLY);
ATTACH '~/.aa/orgs/emea/ahd.duckdb' AS emea (READ_ONLY);
SELECT 'prod' AS org, rsid_name, segment_id, today, status FROM prod.latest_runs
UNION ALL BY NAME
SELECT 'emea' AS org, rsid_name, segment_id, today, status FROM emea.latest_runs
ORDER BY org, rsid_name, segment_id;
```

`UNION ALL BY NAME` matches columns by name, so the two sides do not need the same column order.

### Variables that never collected data

This query makes a cleanup list of the components with no data on any of the last 91 days, by report suite and group. It uses the latest run of each suite that has no segment.

```sql
SELECT r.rsid_name AS suite, d."group", count(*) AS never_collected,
    list(d.component_id ORDER BY d.number)[1:3] AS first_three
FROM component_days d JOIN latest_runs r USING (run_id)
WHERE d.is_yesterday AND d.unused AND r.segment_id IS NULL
GROUP BY ALL ORDER BY suite, never_collected DESC;
```

```
┌─────────────┬───────────────┬─────────────────┬──────────────────────────────────────────────────────────────┐
│    suite    │     group     │ never_collected │                         first_three                          │
├─────────────┼───────────────┼─────────────────┼──────────────────────────────────────────────────────────────┤
│ Adobe Store │ custom_events │               6 │ [metrics/event1, metrics/event2, metrics/event3]             │
│ Adobe Store │ custom_props  │               5 │ [variables/prop1, variables/prop2, variables/prop3]          │
│ Adobe Store │ custom_evars  │               4 │ [metrics/evar1instances, metrics/evar2instances, metrics/e…  │
│ Demo Data   │ custom_events │              21 │ [metrics/event28, metrics/event88, metrics/event90]          │
│ Demo Data   │ custom_evars  │              19 │ [metrics/evar24instances, metrics/evar61instances, metrics…  │
└─────────────┴───────────────┴─────────────────┴──────────────────────────────────────────────────────────────┘
```

### Health over time, with a chart in the terminal

How many values Adobe flagged in each run's 91 full days, with a bar drawn by `bar()`:

```sql
SELECT run_id, rsid, today,
    count(*) FILTER (WHERE anomaly IS NOT NULL) AS flags,
    bar(count(*) FILTER (WHERE anomaly IS NOT NULL), 0, 2000, 20) AS chart
FROM runs JOIN component_days USING (run_id)
GROUP BY ALL ORDER BY run_id;
```

```
┌────────┬─────────────────────────┬────────────┬───────┬──────────────────────┐
│ run_id │          rsid           │   today    │ flags │        chart         │
├────────┼─────────────────────────┼────────────┼───────┼──────────────────────┤
│      1 │ dgeo1xxpnwcidluma       │ 2026-11-08 │  1663 │ ████████████████▋    │
│      2 │ dgeo1xxpnwcidadobestore │ 2026-11-08 │    91 │ ▉                    │
└────────┴─────────────────────────┴────────────┴───────┴──────────────────────┘
```

With daily runs, the same query shows whether tracking gets better or worse over weeks.

### The largest drops, as bars

```sql
SELECT name, round(pct_vs_usual * 100) AS pct, bar(-pct_vs_usual, 0, 1, 30) AS drop
FROM alerts(1, for_day := '2026-11-03')
ORDER BY pct_vs_usual LIMIT 4;
```

```
┌──────────────────────────┬───────┬────────────────────────────────┐
│           name           │  pct  │              drop              │
├──────────────────────────┼───────┼────────────────────────────────┤
│ Approval Requests Denied │ -60.0 │ ██████████████████             │
│ Surveys Completed        │ -52.0 │ ███████████████▌               │
│ Email Error              │ -48.0 │ ██████████████▎                │
│ Push Error               │ -46.0 │ █████████████▉                 │
└──────────────────────────┴───────┴────────────────────────────────┘
```

### Count alerts by group with PIVOT

`PIVOT` turns the values of one column into columns:

```sql
PIVOT (FROM alerts(1, for_day := '2026-11-03')) ON alert USING count(*)
GROUP BY "group" ORDER BY "group";
```

```
┌──────────────────────────────┬────────────┬─────────┐
│            group             │ large drop │ unusual │
├──────────────────────────────┼────────────┼─────────┤
│ List Variables               │          1 │       0 │
│ Reserved Props               │          2 │       0 │
│ Reserved Purchase Events     │          8 │       0 │
│ Reserved Traffic Events      │          3 │       0 │
│ Reserved eVars               │          2 │       0 │
│ Target Integration Instances │          1 │       0 │
│ custom_evars                 │        113 │       1 │
│ custom_events                │        165 │       4 │
│ custom_props                 │         15 │       0 │
└──────────────────────────────┴────────────┴─────────┘
```

### A history longer than 92 days

Each run holds 92 days. Runs made on different days overlap, so together they cover more. This query keeps each day once, from the newest finished run of one suite that has it. It leaves out runs with a segment, because their values are smaller:

```sql
SELECT component_id, count(*) AS days, min(day) AS first_day, max(day) AS last_day
FROM (
    FROM component_days JOIN runs USING (run_id)
    WHERE rsid = 'my_suite' AND segment_id IS NULL AND status IN ('ok', 'partial')
    QUALIFY row_number() OVER (
        PARTITION BY component_id, day ORDER BY today DESC, started_at DESC, run_id DESC
    ) = 1
)
WHERE component_id = 'metrics/visits'
GROUP BY ALL;
```

With one run, it covers 91 full days. With a run each month for a year, it covers the whole year.

### Values that dropped out of the top 100

This query finds, for each Individual Component Summary pair, the values in the top 20 of the previous 90 days that are not in yesterday's top 100. A value that vanishes like this often points to a broken page or a changed tracking value. It reads the latest run of each suite, so a value is listed once and not once for every stored run.

```sql
SELECT r.rsid_name AS suite, p.dimension_name || ' by ' || p.metric_name AS pair,
    h.rank AS rank_before, h.item, h.value AS value_before
FROM latest_runs r
JOIN top_items h USING (run_id)
JOIN pairs p USING (run_id, pair)
WHERE r.segment_id IS NULL AND h.period = 'historical' AND h.rank <= 20
  AND NOT EXISTS (
      FROM top_items c
      WHERE c.run_id = h.run_id AND c.pair = h.pair
        AND c.period = 'current' AND c.item = h.item
  )
ORDER BY suite, pair, h.rank;
```

```
┌─────────────┬─────────────────────┬─────────────┬──────────────────────────────────────────────────────────┬──────────────┐
│    suite    │        pair         │ rank_before │                           item                           │ value_before │
├─────────────┼─────────────────────┼─────────────┼──────────────────────────────────────────────────────────┼──────────────┤
│ Adobe Store │ Page by Occurrences │          12 │ https://www.adobestore.com/products/adobe-staff-event-h… │       3035.0 │
│ Adobe Store │ Page by Occurrences │          13 │ https://www.adobestore.com/products/p-adb328/adb328      │       3017.0 │
└─────────────┴─────────────────────┴─────────────┴──────────────────────────────────────────────────────────┴──────────────┘
```

## Export and share

DuckDB writes a query result to a file with `COPY`:

```sql
COPY (FROM component_days WHERE run_id = 1) TO 'days.parquet' (FORMAT parquet, COMPRESSION zstd);
COPY (FROM alerts(1)) TO 'alerts.json' (FORMAT json, ARRAY true);
COPY (FROM alerts(1)) TO 'alerts.csv';
```

Parquet is a compact column format that BI tools, e.g., Power BI and Tableau, can read. The 37,310 rows of one run took about 1 MB. DuckDB reads it back with `FROM 'days.parquet'`, and `FROM 'history/*.parquet'` reads a whole folder of them as one table.

To write an Excel file, load DuckDB's `excel` extension. DuckDB downloads it the first time.

```sql
INSTALL excel;
LOAD excel;
COPY (FROM alerts(1)) TO 'alerts.xlsx' (FORMAT xlsx, HEADER true);
```

`read_xlsx('alerts.xlsx')` reads it back.

The report, the database and every export hold your component names and values. Share them only where your organization allows.

## Use it from Python

In a clone of this repository, `uv run python` already has the `duckdb` package. Elsewhere, run Python with DuckDB added, e.g., `uv run --with 'duckdb>=2.0' python`. Then query the file:

```python
import duckdb

with duckdb.connect("data/ahd.duckdb", read_only=True) as con:
    rows = con.execute(
        "FROM alerts((SELECT max(run_id) FROM latest_runs"
        " WHERE rsid = $rsid AND segment_id IS NULL)) SELECT name, alert",
        {"rsid": "my_suite"},
    ).fetchall()
```

`$rsid` is a named parameter. DuckDB fills it in safely, so a value can never change the query.

## DuckDB features used in this guide

| Feature | Where it helps |
|---|---|
| `FROM` first, e.g., `FROM runs SELECT ...` | shorter queries |
| `GROUP BY ALL` | groups by every column that is not an aggregate |
| `count(*) FILTER (WHERE ...)` | several counts in one pass |
| `QUALIFY` | filters on a window function, e.g., the newest run per day |
| `list(... ORDER BY ...)[1:3]` | gathers values into one cell, then slices them |
| `PIVOT` | turns values into columns |
| `UNION ALL BY NAME` | combines tables by column name |
| `ATTACH ... (READ_ONLY)` | queries several database files at once |
| `read_csv`, `read_xlsx`, `FROM 'file.parquet'` | reads files with no import step |
| `COPY ... TO` | writes CSV, JSON, Parquet or Excel |
| `SET VARIABLE` and `getvariable` | keeps a value between queries |
| Views and table macros | saved queries inside the file: `latest_runs`, `component_days`, `alerts()`, `anomaly_days()` |
| `SUMMARIZE`, `DESCRIBE` | profiles a table in one line |
| `bar()` | draws a bar chart as text |
| `regexp_matches` | matches components to owners by pattern |
| `-markdown`, `-html`, `-json`, `-csv` | prints results ready for Slack, email or scripts |
| `-ui` | a notebook in your browser |
| `-cmd "..."` | runs SQL before the prompt or the notebook starts |
