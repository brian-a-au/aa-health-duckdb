# Use the database with DuckDB

[README](../README.md) · [Usage](usage.md) · [Configuration](configuration.md) · [The report](report.md) · [How runs are stored](runs.md) · **DuckDB guide** · [Run it every day](scheduling.md) · [Troubleshooting](troubleshooting.md) · [Compared with the Excel workbook](excel-comparison.md) · [Development](development.md)

Every `ahd` run is saved in one DuckDB file. The HTML report shows one run at a time. The file holds every run, for every report suite, and you can ask it any question with SQL. This guide shows what you can do with it, from a daily alert email to an audit of unused variables across all your organizations.

Every example here was run against a real database made by `ahd` with DuckDB 2.0. The outputs are real, from one file with four runs of Adobe's demo report suites, all with the run date 2026-11-12: run 1 for Demo Data, run 2 for Adobe Store, run 3 for Demo Data CJA and run 4 for Demo Data Evangelists. A block that is shortened says so. Where two rows have the same value in the column that orders them, DuckDB can list them in either order, so your output may differ in that order.

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

Every file comes with two views and three table macros. A view is a saved query that you use like a table. A table macro is a saved query that takes options. They use the same rules as the report, and the tests check that they agree.

When a new version of `ahd` changes these rules, the next `ahd run`, `ahd individual`, `ahd prune` or `ahd import` replaces the views and the macros in your file. Until then, your own SQL uses the old rules. If you built your own views on top of `component_days`, they follow the new rules after the update. [`ahd alerts`](#print-the-alerts-with-ahd-alerts) and the [alert email script](#send-an-alert-email-every-morning) always use the rules of the `ahd` that you run, also on a file with older views.

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

The last four columns say since when each alert has held. `ahd` 0.1.12 added them after the other columns, which keep their names, their meaning and their order:

- `since`: the day the alert started. An alert follows one condition: a value of 0 when its value is 0, else the direction of its anomaly flag, else a change past `threshold` or `spike_threshold`. `since` is the first day of the unbroken run of days, up to the alert's day, on which that condition held. A 0 that the anomaly method flagged on some days keeps one start, also when its `alert` changes from "unusual" to "dropped to zero".
- `days`: the number of days from `since` to the alert's day, both counted.
- `at_least`: true when the day before `since` cannot be judged, so the alert may have started earlier. That day is not in the run's 91 days, or it has no value. For a flag, the method gave it no expected value. For a large drop or spike, it is older than the last 7 days, which are the only days with a weekday average. So a drop on all 7 days has `days` 7 and `at_least` true.
- `is_new`: true when the alert started on its own day, so `days` is 1 and `at_least` is false.

With `for_day`, the streak ends on that day. The rows keep the order they had before 0.1.12. Add `ORDER BY is_new DESC` to put the new alerts first, as the report, `ahd alerts` and the email do:

```sql
FROM alerts(2) SELECT alert, name, since, days, at_least, is_new ORDER BY is_new DESC, since DESC, name;
```

```
┌─────────────┬───────────────────────┬────────────┬───────┬──────────┬─────────┐
│    alert    │         name          │   since    │ days  │ at_least │ is_new  │
├─────────────┼───────────────────────┼────────────┼───────┼──────────┼─────────┤
│ unusual     │ Exit Link Instances   │ 2026-11-11 │     1 │ false    │ true    │
│ large spike │ Cart Views            │ 2026-11-10 │     2 │ false    │ false   │
│ unusual     │ Orders                │ 2026-11-10 │     2 │ false    │ false   │
│ large spike │ Unique Visitors       │ 2026-11-10 │     2 │ false    │ false   │
│ large spike │ Units                 │ 2026-11-10 │     2 │ false    │ false   │
│ large spike │ Visits                │ 2026-11-10 │     2 │ false    │ false   │
│ unusual     │ Cart Additions        │ 2026-11-09 │     3 │ false    │ false   │
│ large spike │ Custom Link Instances │ 2026-11-09 │     3 │ false    │ false   │
│ large spike │ Checkouts             │ 2026-11-08 │     4 │ false    │ false   │
│ large spike │ Occurrences           │ 2026-11-07 │     5 │ false    │ false   │
│ large spike │ Page                  │ 2026-11-07 │     5 │ false    │ false   │
│ large spike │ Page Views            │ 2026-11-07 │     5 │ false    │ false   │
│ large spike │ Product Views         │ 2026-11-07 │     5 │ false    │ false   │
└─────────────┴───────────────────────┴────────────┴───────┴──────────┴─────────┘
```

On Demo Data CJA, whose values fall every day, the large drops held on all of the last 7 days, so they may have started earlier:

```sql
FROM alerts(3) SELECT alert, since, days, at_least, is_new, count(*) AS alerts GROUP BY ALL ORDER BY alerts DESC;
```

```
┌─────────────────┬────────────┬───────┬──────────┬─────────┬────────┐
│      alert      │   since    │ days  │ at_least │ is_new  │ alerts │
├─────────────────┼────────────┼───────┼──────────┼─────────┼────────┤
│ large drop      │ 2026-11-05 │     7 │ true     │ false   │     97 │
│ dropped to zero │ 2026-11-11 │     1 │ false    │ true    │      2 │
└─────────────────┴────────────┴───────┴──────────┴─────────┴────────┘
```

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

The last column, `event`, names the suite-wide event that the alert belongs to, or is `NULL`. `ahd` 0.1.13 added it. See [`suite_events()`](#suite_eventsrun-) below. On Adobe Store, the 10 large spikes are one event, and the 3 unusual values are outside it:

```sql
FROM alerts(2) SELECT alert, event, count(*) AS alerts GROUP BY ALL ORDER BY alerts DESC;
```

```
┌─────────────┬─────────┬────────┐
│    alert    │  event  │ alerts │
├─────────────┼─────────┼────────┤
│ large spike │ spike   │     10 │
│ unusual     │ NULL    │      3 │
└─────────────┴─────────┴────────┘
```

Add `WHERE event IS NULL` to keep only the alerts outside an event:

```sql
FROM alerts(2) SELECT alert, name, since WHERE event IS NULL;
```

```
┌─────────┬─────────────────────┬────────────┐
│  alert  │        name         │   since    │
├─────────┼─────────────────────┼────────────┤
│ unusual │ Exit Link Instances │ 2026-11-11 │
│ unusual │ Cart Additions      │ 2026-11-09 │
│ unusual │ Orders              │ 2026-11-10 │
└─────────┴─────────────────────┴────────────┘
```

When you query a file through `ATTACH`, run `USE` with its name before you call `alerts()`, `suite_events()` or `anomaly_days()`, e.g., `USE prod;`. The views work without it.

### `suite_events(run, ...)`

The suite-wide events of one run, one row for each event. It takes the same options as `alerts()`, with the same defaults, e.g., `suite_events(4, for_day := '2026-11-04')`. `ahd` 0.1.13 added it.

An event is a group of alerts that follow the same condition, e.g., a value of 0. It needs at least 10 alerts, and they must be at least half of the components that collected data in the run. [The report](report.md#suite-wide-events) explains why. The condition is the one that `since` follows in `alerts()`, so a 0 that the anomaly method flagged belongs to the same event as a drop to zero. The columns:

- `run_id` and `day`: as in `alerts()`.
- `event`: the condition: `zero` for a value of 0, `low` or `high` for a flag below or above the expected range, `drop` for a large drop and `spike` for a large spike. The `event` column of `alerts()` holds the same name for each alert of the event.
- `alerts`: the number of alerts in the event.
- `components`: the number of components that collected data in the run, i.e., had a value other than 0 on at least one of its 91 days. The others cannot alert, so they do not count.
- `since` and `at_least`: the start that most alerts of the event share. When two starts are shared by as many alerts, the earlier one wins.
- `since_alerts`: the number of alerts with that start.
- `new`: the number of alerts of the event that are new on the day.

The biggest event comes first. Most days have no event, and then the macro gives no row. On Demo Data Evangelists, the data stopped. All 356 components that collected data were 0, and 330 of them since 2026-11-05:

```sql
FROM suite_events(4);
```

```
┌────────┬────────────┬─────────┬────────┬────────────┬────────────┬──────────┬──────────────┬───────┐
│ run_id │    day     │  event  │ alerts │ components │   since    │ at_least │ since_alerts │  new  │
├────────┼────────────┼─────────┼────────┼────────────┼────────────┼──────────┼──────────────┼───────┤
│      4 │ 2026-11-11 │ zero    │    356 │        356 │ 2026-11-05 │ false    │          330 │     0 │
└────────┴────────────┴─────────┴────────┴────────────┴────────────┴──────────┴──────────────┴───────┘
```

On the day the data stopped, most values were small but not 0, and Adobe flagged them as below the expected range:

```sql
FROM suite_events(4, for_day := '2026-11-04') SELECT event, alerts, components, since, since_alerts, new;
```

```
┌─────────┬────────┬────────────┬────────────┬──────────────┬───────┐
│  event  │ alerts │ components │   since    │ since_alerts │  new  │
├─────────┼────────┼────────────┼────────────┼──────────────┼───────┤
│ low     │    234 │        356 │ 2026-11-04 │          233 │   233 │
└─────────┴────────┴────────────┴────────────┴──────────────┴───────┘
```

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

For the Adobe Store suite, `dgeo1xxpnwcidadobestore`, it prints:

```
|    alert    |         name          | usual  | value  |  pct  |
|-------------|-----------------------|-------:|-------:|------:|
| unusual     | Exit Link Instances   | 2.0    | 8.0    | 231.0 |
| unusual     | Cart Additions        | 140.0  | 230.0  | 64.0  |
| unusual     | Orders                | 18.0   | 35.0   | 91.0  |
| large spike | Custom Link Instances | 12.0   | 41.0   | 239.0 |
| large spike | Page                  | 4849.0 | 8172.0 | 69.0  |
| large spike | Cart Views            | 142.0  | 270.0  | 91.0  |
| large spike | Checkouts             | 67.0   | 203.0  | 204.0 |
| large spike | Units                 | 491.0  | 838.0  | 71.0  |
| large spike | Occurrences           | 4863.0 | 8221.0 | 69.0  |
| large spike | Page Views            | 4849.0 | 8172.0 | 69.0  |
| large spike | Unique Visitors       | 341.0  | 738.0  | 116.0 |
| large spike | Visits                | 401.0  | 859.0  | 114.0 |
| large spike | Product Views         | 2850.0 | 4499.0 | 58.0  |
```

### Print the alerts with ahd alerts

`ahd alerts` prints the alerts of the latest run of a suite, or of any stored run, with no SQL. It uses the `alerts()` macro of the `ahd` that you run, so it gives the same alerts as the queries above. It opens the file read-only.

```
ahd alerts --db data/ahd.duckdb --rsid dgeo1xxpnwcidadobestore
```

```
A large drop is more than 30% below the weekday average, and a large spike is more than 50% above it. The weekday average is the average of the same weekday in the 12 weeks before. Adobe's anomaly detection flags the unusual values.

Run 2, Adobe Store (dgeo1xxpnwcidadobestore), status ok
Alerts for 2026-11-11: 3 unusual values, 0 large drops, 10 large spikes, 0 drops to zero
1 of these alerts is new on 2026-11-11.
Suite-wide event: 10 of the 15 components that collected data had a value more than 50% above the weekday average on 2026-11-11. For 4 of them, this started on 2026-11-07. For the other 6, it started on other days.
- large spike: Custom Link Instances (metrics/customlinkinstances), weekday average 12.08, actual 41, change +239.3%, since 2026-11-09, 3 days
- large spike: Page (variables/page), weekday average 4,848.50, actual 8,172, change +68.5%, since 2026-11-07, 5 days
- large spike: Cart Views (metrics/cartviews), weekday average 141.50, actual 270, change +90.8%, since 2026-11-10, 2 days
- large spike: Checkouts (metrics/checkouts), weekday average 66.83, actual 203, change +203.7%, since 2026-11-08, 4 days
- large spike: Units (metrics/units), weekday average 490.83, actual 838, change +70.7%, since 2026-11-10, 2 days
5 more alerts of this event are not listed. Add --every-alert to list them.
Other alerts:
- unusual: Exit Link Instances (metrics/exitlinkinstances), weekday average 2.42, actual 8, expected 2.51, change +231.0%, new on 2026-11-11
- unusual: Cart Additions (metrics/cartadditions), weekday average 140.08, actual 230, expected 77.06, change +64.2%, since 2026-11-09, 3 days
- unusual: Orders (metrics/orders), weekday average 18.33, actual 35, expected 20.91, change +90.9%, since 2026-11-10, 2 days
```

The 10 large spikes are a [suite-wide event](#suite_eventsrun-), so the text gives them one line and lists only the first 5. `--every-alert` lists all of them. `--all-suites` prints every suite with a finished run. `--format json` and `--format csv` give the same alerts to another program. The command exits with 0 when it printed an alert and with 4 when there was none, so a scheduled job can send an email only on a day with alerts. [Usage](usage.md#alerts) gives every option, the fields of the JSON and the exit codes.

### Send an alert email every morning

[`examples/send_alerts.py`](examples/send_alerts.py) emails the latest alerts for one or more report suites, with an HTML table and a plain text part. The email has one section for each suite that has alerts. It sends nothing when no suite has alerts. That way an empty inbox means healthy suites.

Each section lists the alerts that are new on the day first, and its heading counts them, e.g., "Adobe Store (dgeo1xxpnwcidadobestore), 2026-11-11: 13 alerts, 1 new". Each alert says since when it has held, e.g., "since 2026-11-09, 3 days". The Since column needs `ahd` 0.1.12 or newer. With 0.1.11, it stays empty.

When a suite has a [suite-wide event](#suite_eventsrun-), its section starts with one line for the event, e.g., "356 of the 356 components that collected data had a value of 0 on 2026-11-11". A table of the first 5 alerts of the event follows, and a line counts the others. Then "Other alerts" lists the alerts outside the event. So when the data of a whole suite stops, the email has 5 rows for it, not 356. The subject still counts every alert. This needs `ahd` 0.1.13 or newer. With an older `ahd`, the email lists every alert.

The script gets its alerts from `ahd alerts --format json`, and builds and sends the email itself. So it needs `ahd` 0.1.11 or newer. Before 0.1.11, it needed only DuckDB, and it read the file with its own copy of the rules for the anomaly options. The script is in this repository. Run it from a clone, as below, where `uv run` already has `ahd`. Without a clone, download the file and run it with `ahd` added, e.g., `uv run --with aa-health-duckdb python send_alerts.py ...`. The script looks for `ahd` in the Python that runs it first, then for the `ahd` program on your `PATH`. Without either, it stops with exit code 1 and says how to install it.

```
uv run python docs/examples/send_alerts.py ~/.aa/orgs/prod/ahd.duckdb my_suite
uv run python docs/examples/send_alerts.py ~/.aa/orgs/prod/ahd.duckdb my_suite other_suite
uv run python docs/examples/send_alerts.py ~/.aa/orgs/prod/ahd.duckdb --all
```

It reads the latest run of each suite without a segment, from `latest_runs`. `--all` uses every suite that has such a run in the file. Add `--segment ID` for the latest runs with that segment. The script takes only report suite IDs and segment IDs, not names, and they must match exactly. `--threshold` sets the large drop percent as a share, e.g., `--threshold 0.20` for 20 percent. The default is `0.30`. `--spike-threshold` sets the large spike percent the same way, up to `2`. The default is `0.50`. The script gives each share to `ahd alerts` as the exact percent, e.g., `0.275` as `--change-alert 27.5`, so the email lists the same alerts as before.

`--anomaly-method` chooses the anomaly method that flags the unusual values, and `--anomaly-sensitivity` sets how wide the range of a local method is. The script gives both to `ahd alerts`, so they take the same values as on `ahd run`, and the defaults are the same, `adobe` and `normal`. So by default the script uses Adobe's flags. With `adobe`, `low` or `high` stops the script with exit code 1 and the message of `ahd`, because Adobe returns its own range. The email says which method flagged the unusual values, and it names the sensitivity when it is not `normal`, e.g., "The Weekday median method with high sensitivity flagged the unusual values."

Without these two options, `ahd alerts` reads the variables `AHD_ANOMALY_METHOD` and `AHD_ANOMALY_SENSITIVITY`, as `ahd run` does. Set them once for a scheduled job, and the reports and the email use the same method. An option wins over its variable, and an empty variable counts as not set. A value that is not a method or a sensitivity stops the script with exit code 1, and the message names the variable. See [Set the anomaly method once](usage.md#set-the-anomaly-method-once).

`ahd alerts` uses the rules of the installed `ahd`, also on a file that an older version wrote, so every method and sensitivity works on any file. Before 0.1.11, the script used the macro in the file, and on a file that an older version had written last, it stopped and said to run `ahd run` once.

Without `SMTP_HOST`, it writes `alerts.eml` in the current folder, which you can open and check first. To send, set `SMTP_HOST`, `MAIL_FROM` and `MAIL_TO`. Set `SMTP_PORT` when the server does not use 587. Set `SMTP_USER` and `SMTP_PASSWORD` when the server needs a login. The script connects without encryption and then turns it on with the STARTTLS command. It does not work with a server that needs encryption from the start, e.g., on port 465.

It exits with these codes:

- 0 when it sent or wrote a message
- 1 when something is wrong, e.g., the file holds no finished run of a suite. The alerts of the other suites are still sent. A wrong option also exits with 1 and prints the usage.
- 2 when there was nothing to send

To run the check and the email every morning on Windows, macOS or Linux, see [Run it every day](scheduling.md).

### Send only what is new since the last run

If the same variable has been broken for a week, a daily email about it adds nothing. The column `is_new` of `alerts()` keeps only the alerts that started on the day of the run. It needs only one run:

```sql
FROM alerts((SELECT max(run_id) FROM latest_runs WHERE rsid = 'my_suite' AND segment_id IS NULL))
SELECT alert, name, value WHERE is_new;
```

For the Adobe Store suite, it prints:

```
┌─────────┬─────────────────────┬────────┐
│  alert  │        name         │ value  │
├─────────┼─────────────────────┼────────┤
│ unusual │ Exit Link Instances │    8.0 │
└─────────┴─────────────────────┴────────┘
```

An alert whose day before cannot be judged is never new, e.g., a large drop that held on all of the last 7 days. Add `OR (at_least AND days = 1)` to keep an alert that may be new.

The query below answers a different question. It keeps the alerts that were not alerts in the run before, whatever their days. Use it when your runs are not made every day, e.g., once a week, so that an alert that started between two runs counts as new:

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
    FROM alerts(1, for_day := '2026-11-08') a
    JOIN owners o ON regexp_matches(a.component_id, o.pattern)
    QUALIFY row_number() OVER (PARTITION BY a.component_id ORDER BY o.line) = 1
)
SELECT owner, count(*) AS alerts, list(name ORDER BY pct_vs_usual)[1:3] AS worst_three
FROM routed GROUP BY ALL ORDER BY alerts DESC;
```

```
┌───────────────────────────┬────────┬─────────────────────────────────────────────────────┐
│           owner           │ alerts │                     worst_three                     │
├───────────────────────────┼────────┼─────────────────────────────────────────────────────┤
│ analytics@example.com     │     20 │ [Push Error, Return In Store, Online Return]        │
│ marketing@example.com     │      7 │ [Return Reason, Return ID, Foresee Custom Question] │
│ commerce-team@example.com │      1 │ [Approval Requests Approved]                        │
└───────────────────────────┴────────┴─────────────────────────────────────────────────────┘
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
┌───────────────────────┬────────────┬─────────┬─────────────────┬─────────────┬──────────────┬─────────────────┬──────────┐
│         suite         │    day     │ unusual │ dropped_to_zero │ large_drops │ large_spikes │ never_collected │ problems │
├───────────────────────┼────────────┼─────────┼─────────────────┼─────────────┼──────────────┼─────────────────┼──────────┤
│ Adobe Store           │ 2026-11-11 │       3 │               0 │           0 │           10 │              20 │        0 │
│ Demo Data             │ 2026-11-11 │       0 │               0 │           0 │            2 │              42 │        0 │
│ Demo Data CJA         │ 2026-11-11 │       0 │               2 │          97 │            0 │              25 │        1 │
│ Demo Data Evangelists │ 2026-11-11 │       0 │             356 │           0 │            0 │              15 │        3 │
└───────────────────────┴────────────┴─────────┴─────────────────┴─────────────┴──────────────┴─────────────────┴──────────┘
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
┌───────────────────────┬──────────────────────────────┬─────────────────┬─────────────────────────────────────────────────────────────────────────────┐
│         suite         │            group             │ never_collected │                                 first_three                                 │
├───────────────────────┼──────────────────────────────┼─────────────────┼─────────────────────────────────────────────────────────────────────────────┤
│ Adobe Store           │ custom_events                │               6 │ [metrics/event1, metrics/event2, metrics/event3]                            │
│ Adobe Store           │ custom_props                 │               5 │ [variables/prop1, variables/prop2, variables/prop3]                         │
│ Adobe Store           │ custom_evars                 │               4 │ [metrics/evar1instances, metrics/evar2instances, metrics/evar3instances]    │
│ Adobe Store           │ Target Integration Instances │               2 │ [metrics/targetconversion, metrics/targetimpression]                        │
│ Adobe Store           │ Reserved Purchase Events     │               1 │ [metrics/carts]                                                             │
│ Adobe Store           │ Reserved eVars               │               1 │ [metrics/campaigninstances]                                                 │
│ Adobe Store           │ Reserved Props               │               1 │ [metrics/downloadlinkinstances]                                             │
│ Demo Data             │ custom_events                │              21 │ [metrics/event28, metrics/event88, metrics/event90]                         │
│ Demo Data             │ custom_evars                 │              19 │ [metrics/evar24instances, metrics/evar61instances, metrics/evar62instances] │
│ Demo Data             │ Reserved Props               │               1 │ [metrics/exitlinkinstances]                                                 │
│ Demo Data             │ Target Integration Instances │               1 │ [metrics/targetconversion]                                                  │
│ Demo Data CJA         │ custom_evars                 │               9 │ [metrics/evar9instances, metrics/evar28instances, metrics/evar29instances]  │
│ Demo Data CJA         │ custom_props                 │               5 │ [variables/prop1, variables/prop2, variables/prop3]                         │
│ Demo Data CJA         │ Reserved Purchase Events     │               5 │ [metrics/cartadditions, metrics/cartremovals, metrics/carts]                │
│ Demo Data CJA         │ custom_events                │               2 │ [metrics/event54, metrics/event55]                                          │
│ Demo Data CJA         │ Reserved Props               │               2 │ [metrics/downloadlinkinstances, metrics/exitlinkinstances]                  │
│ Demo Data CJA         │ Target Integration Instances │               2 │ [metrics/targetconversion, metrics/targetimpression]                        │
│ Demo Data Evangelists │ custom_evars                 │              11 │ [metrics/evar24instances, metrics/evar90instances, metrics/evar95instances] │
│ Demo Data Evangelists │ Target Integration Instances │               2 │ [metrics/targetconversion, metrics/targetimpression]                        │
│ Demo Data Evangelists │ Reserved Props               │               1 │ [metrics/exitlinkinstances]                                                 │
│ Demo Data Evangelists │ custom_events                │               1 │ [metrics/event28]                                                           │
└───────────────────────┴──────────────────────────────┴─────────────────┴─────────────────────────────────────────────────────────────────────────────┘
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
┌────────┬──────────────────────────┬────────────┬───────┬──────────────────────┐
│ run_id │           rsid           │   today    │ flags │        chart         │
├────────┼──────────────────────────┼────────────┼───────┼──────────────────────┤
│      1 │ dgeo1xxpnwcidluma        │ 2026-11-12 │  1566 │ ███████████████▋     │
│      2 │ dgeo1xxpnwcidadobestore  │ 2026-11-12 │   115 │ █▏                   │
│      3 │ dgeo1xxpnwcidcjab2b      │ 2026-11-12 │   176 │ █▊                   │
│      4 │ egeo1xxpnwcidevangelists │ 2026-11-12 │  3054 │ ████████████████████ │
└────────┴──────────────────────────┴────────────┴───────┴──────────────────────┘
```

`bar()` draws a full bar for a count above its maximum, here 2000, as for run 4. With daily runs, the same query shows whether tracking gets better or worse over weeks.

### The largest drops, as bars

```sql
SELECT name, round(pct_vs_usual * 100) AS pct, bar(-pct_vs_usual, 0, 1, 30) AS drop
FROM alerts(1, for_day := '2026-11-08')
ORDER BY pct_vs_usual LIMIT 4;
```

```
┌─────────────────┬────────┬────────────────────────────────┐
│      name       │  pct   │              drop              │
├─────────────────┼────────┼────────────────────────────────┤
│ Push Error      │  -41.0 │ ████████████▍                  │
│ Return In Store │  -41.0 │ ████████████▏                  │
│ Return Reason   │  -40.0 │ ███████████▉                   │
│ Return ID       │  -40.0 │ ███████████▉                   │
└─────────────────┴────────┴────────────────────────────────┘
```

### Count alerts by group with PIVOT

`PIVOT` turns the values of one column into columns:

```sql
PIVOT (FROM alerts(1, for_day := '2026-11-08')) ON alert USING count(*)
GROUP BY "group" ORDER BY "group";
```

```
┌───────────────┬────────────┬─────────────┐
│     group     │ large drop │ large spike │
├───────────────┼────────────┼─────────────┤
│ custom_evars  │          6 │           1 │
│ custom_events │         21 │           0 │
└───────────────┴────────────┴─────────────┘
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
┌───────────────────────┬─────────────────────┬─────────────┬──────────────────────────────────────────────────────────────────┬──────────────┐
│         suite         │        pair         │ rank_before │                               item                               │ value_before │
├───────────────────────┼─────────────────────┼─────────────┼──────────────────────────────────────────────────────────────────┼──────────────┤
│ Adobe Store           │ Page by Occurrences │          11 │ https://www.adobestore.com/products/adobe-staff-event-tee/adb539 │       3660.0 │
│ Demo Data Evangelists │ Page by Occurrences │           1 │ home                                                             │     580781.0 │
│ Demo Data Evangelists │ Page by Occurrences │           2 │ app: launch                                                      │     286300.0 │
│ Demo Data Evangelists │ Page by Occurrences │           3 │ category 5                                                       │     240650.0 │
└───────────────────────┴─────────────────────┴─────────────┴──────────────────────────────────────────────────────────────────┴──────────────┘
(Shortened: 17 more rows of Demo Data Evangelists follow, with ranks 4 to 20.)
```

In this file, the run of Demo Data Evangelists has no top 100 for yesterday, because no rows came back for it. Every value of its top 20 is then missing from yesterday's list. The status section of that run's report names the problem, and the `issues` table holds it too.

## Export and share

DuckDB writes a query result to a file with `COPY`:

```sql
COPY (FROM component_days WHERE run_id = 1) TO 'days.parquet' (FORMAT parquet, COMPRESSION zstd);
COPY (FROM alerts(1)) TO 'alerts.json' (FORMAT json, ARRAY true);
COPY (FROM alerts(1)) TO 'alerts.csv';
```

Parquet is a compact column format that BI tools, e.g., Power BI and Tableau, can read. The 37,310 rows of run 1 took about 0.9 MB. DuckDB reads it back with `FROM 'days.parquet'`, and `FROM 'history/*.parquet'` reads a whole folder of them as one table.

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
