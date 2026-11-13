# Changelog

This file lists the changes in each release. The version numbers follow [Semantic Versioning](https://semver.org/). Until 1.0.0, a minor release, e.g., 0.2.0, may change the database layout or the command options. A patch release holds fixes and additions that break nothing. The tool then asks you to start a new database file, and this file says so.

## 0.1.13 (unreleased)

This release gives one line to a problem that hits most of a report suite at once, e.g., when its data stops. The database layout stays at version 7, so files made by 0.1.2 to 0.1.12 keep working. The views version changes from 7 to 8, because the `alerts()` macro gets a new column and a new macro `suite_events()` comes next to it. The next `ahd run`, `ahd individual`, `ahd prune` or `ahd import` replaces them in your file. `ahd render` and `ahd alerts` open the file read-only, so they do not replace them, but they use the new rules for their own session. Which components are alerts does not change, and neither do the counts in the tiles, the list headings, the alert line and the earlier runs.

- A suite-wide event is a group of at least 10 alerts of the same kind that covers at least half of the components that collected data in the run. The kind is the condition that the Since column follows: a value of 0, a value below or above the expected range, a large drop or a large spike. Its line names the start that most of its alerts share, e.g., "356 of the 356 components that collected data had a value of 0 on Wed, Nov 11 (yesterday). For 330 of them, this started on Nov 5. For the other 26, it started on other days." [The report](docs/report.md#suite-wide-events) explains the rule and its numbers.
- **What looks different in the report:** a day with an event starts the Alerts Summary with "Suite-wide events" and one line for each event. Its rows are folded under the line, behind "Show the 356 alerts". The lists below leave these rows out and say how many of their alerts are in an event, e.g., "All 356 are part of a suite-wide event above, which lists them." The heading of each list and the tiles still count every alert. Moving a slider can make or end an event of large drops or spikes. The copied alerts start with each event, its first 5 alerts and a count of the others. A day without an event looks as before.
- **What looks different in `ahd alerts`:** the text gives each event a line that starts with "Suite-wide event:", its first 5 alerts and a line that counts the others, then "Other alerts:" and the alerts outside an event. The new option `--every-alert` lists every alert of an event. The JSON gives each run a new list `events`, and each alert a new field `event`, e.g., `"zero"` or `null`. Every alert stays in the `alerts` list, so a program that ignores the new fields gets what it got before. `format_version` stays 1. The CSV has a new column `event` at the end. See [Usage](docs/usage.md#suite-wide-events-in-the-output).
- **What looks different in the alert email:** `send_alerts.py` starts the section of a suite with an event with the line of the event and a table of its first 5 alerts, then lists the other alerts. On Demo Data Evangelists, the email has 5 rows in place of 356. Its options, variables and exit codes stay the same, and the subject still counts every alert. With an older `ahd`, the email lists every alert as before.
- The `alerts()` macro has a new column `event` at the end. The other columns and the rows stay the same. The new macro `suite_events()` takes the same options and gives one row for each event. See the [DuckDB guide](docs/duckdb.md#suite_eventsrun-).

## 0.1.12 (unreleased)

This release says since when each alert has held, so you can tell new alerts from ongoing ones. The database layout stays at version 7, so files made by 0.1.2 to 0.1.11 keep working. The views version changes from 6 to 7, because the `alerts()` macro gets new columns. The next `ahd run`, `ahd individual`, `ahd prune` or `ahd import` replaces it in your file. `ahd render` and `ahd alerts` open the file read-only, so they do not replace it, but they use the new rules for their own session. Which components are alerts does not change, and neither do the counts.

- Each alert says on which day it started and for how many days it has held, e.g., "since 2026-11-04, 8 days". An alert that started on its own day is new. An alert follows one condition: a value of 0, else the direction of its anomaly flag, else a change past the drop or spike percent. Its streak is the unbroken run of days on which that condition held. So a 0 that Adobe flagged on its first days keeps one start, also when its label changes from an unusual value to "dropped to zero". [The report](docs/report.md#new-and-ongoing-alerts) explains the rules.
- When the day before a streak cannot be judged, the alert says "or earlier" and "at least", e.g., "since 2026-11-05 or earlier, at least 7 days". This is always the case for a large drop or spike that held on all of the last 7 days, because only those days have a weekday average.
- The `alerts()` macro has four new columns at the end: `since`, `days`, `at_least` and `is_new`. The other columns keep their names, their meaning and their order, and the rows keep their order. Your own SQL that selects columns by name keeps working. SQL that uses `SELECT *` or `FROM alerts(...)` alone, e.g., `COPY (FROM alerts(1)) TO 'alerts.csv'`, now gets the four columns too. See the [DuckDB guide](docs/duckdb.md#alertsrun-threshold--030-spike_threshold--050-min_usual--10-for_day--null-method--adobe-sensitivity--normal).
- The report has a "Since" column in each alert list in place of "Days in a row", e.g., "Nov 4, 3 days" or "New". The old column counted only the days with the same label, and only within the 7 days of the report, so a drop to zero that started 8 days before said "7 or more". The new column counts back over the 91 days of the run for a value of 0 or a flag. Each list shows the new alerts first, and its heading counts them, e.g., "3 components, 1 new". Each alert tile with alerts says how many are new, e.g., "1 new". The copied alerts have the same column and order.
- `ahd alerts` lists the new alerts first in every format. The others follow in the order of the macro, as before. The text has a line that counts the new alerts, and each alert line ends with since when it has held. The JSON and the CSV have the four new fields at the end of each alert. `format_version` stays 1, because no field was removed or changed its meaning. See [Usage](docs/usage.md#the-formats).
- The alert email `send_alerts.py` lists the new alerts first in each section, says since when each alert has held in a new Since column, and counts the new alerts in the heading of each section, e.g., "13 alerts, 1 new". The subject stays the same. With `ahd` 0.1.11, the Since column stays empty.
- The DuckDB guide shows how to keep only the new alerts of one run with `WHERE is_new`. The recipe that compares two runs stays, for runs that are not made every day.

## 0.1.11 (2026-11-12)

This release adds the command `ahd alerts`. The database layout stays at version 7 and the views version at 6, so files made by 0.1.2 to 0.1.10 keep working, and no command replaces anything in them. The rules of the alerts do not change.

- `ahd alerts` prints the alerts of stored runs, with no calls to Adobe. It takes `--run N`, `--rsid` with a report suite ID, or `--all-suites`, and `--segment` with a segment ID. With `--rsid` or `--all-suites`, it uses the latest run of each suite that ended "ok" or "partial". See [Usage](docs/usage.md#alerts).
  - It finds the alerts with the `alerts()` macro, so they are the same as in the report and in the alert line of `ahd run`.
  - It takes `--change-alert`, `--spike-alert`, `--anomaly-method` and `--anomaly-sensitivity` with the same defaults as `ahd render`, and reads the variables `AHD_ANOMALY_METHOD` and `AHD_ANOMALY_SENSITIVITY`. Here `--change-alert` and `--spike-alert` take any percent, e.g., `27.5`, because there is no slider.
  - `--format text` is the default, for a person or a plain email. `--format json` and `--format csv` are for another program. [Usage](docs/usage.md#the-formats) lists the fields of the JSON. Later versions may add fields.
  - It exits with 0 when it printed an alert, with 4 when there were none, with 1 when something is wrong and with 2 for a wrong option. So a scheduled job can send an email only on a day with alerts.
  - It opens the file read-only, so it works while a `duckdb -readonly` session has the file open. It uses the views and macros of the `ahd` that you run for its own session, so it follows the current rules also on a file that an older version wrote, and it leaves the file as it is.
- The alert email script `send_alerts.py` now gets its alerts from `ahd alerts --format json`. It no longer keeps its own copy of the anomaly methods and of the checks of the variables. Its options, its variables, its exit codes and its email stay the same, so a scheduled job keeps working. These things change for a user of the script:
  - **It needs `ahd` 0.1.11 or newer.** Before, it needed only DuckDB. In a clone of the repository, `uv run` already has `ahd`. Elsewhere, run it with `ahd` added, e.g., `uv run --with aa-health-duckdb python send_alerts.py ...`, or install `ahd` so that it is on your `PATH`. Without `ahd`, the script stops with exit code 1 and says how to install it. With an older `ahd`, it says to update it.
  - Every anomaly method and sensitivity now works on a file that an older version of `ahd` wrote last. Before, the script stopped with exit code 1 and said to run `ahd run` once.
  - The messages for a wrong anomaly option or variable, a locked file and a missing file are now those of `ahd`, e.g., "Error: The database ... is in use by another program". A sensitivity of `low` or `high` with `adobe` from the options no longer prints the usage of the script. The exit code stays 1.
  - The shares of `--threshold` and `--spike-threshold` go to `ahd alerts` as exact percents, e.g., `0.275` as `27.5`, so a job gets the same alerts as before. The rows of the email are the same. Two rows with the same alert and the same change now always come in the order of their group and ID. Before, DuckDB could list them in either order.
- [Run it every day](docs/scheduling.md#without-the-email-script) shows a job that sends the text of `ahd alerts` with a `mail` program, and the [DuckDB guide](docs/duckdb.md#print-the-alerts-with-ahd-alerts) shows the command next to the SQL recipes.

## 0.1.10 (2026-11-12)

The database layout stays at version 7, so files made by 0.1.2 to 0.1.9 keep working. The views version changes from 5 to 6, because the `anomaly_days()` macro changes. The next `ahd run`, `ahd individual`, `ahd prune` or `ahd import` replaces it in your file. `ahd render` opens the file read-only, so it does not replace it.

- The `anomaly_days()` macro no longer gives rows for the Individual Component Summary pairs, e.g., `individual/1`. These pairs have daily values but are not components, and `component_days` and `alerts()` already left them out. Counts from your own SQL on `anomaly_days()` can go down by the rows of the pairs, for every method, also `adobe`. For one run of Adobe's demo suites, the Weekday median method flagged 8 values on a day instead of 9, and Adobe 7 instead of 8. `alerts()`, the report, the alert line and `send_alerts.py` do not change. The DuckDB guide no longer explains how to leave these rows out.
- Two new environment variables set the defaults of the anomaly options: `AHD_ANOMALY_METHOD` for `--anomaly-method` and `AHD_ANOMALY_SENSITIVITY` for `--anomaly-sensitivity`. `ahd run`, `ahd individual`, `ahd render` and the alert email script `send_alerts.py` read them. A scheduled job can set them once, so the reports and the email use the same method. Before, the job had to give the same two options to both commands, and nothing caught a difference. See [Set the anomaly method once](docs/usage.md#set-the-anomaly-method-once).
  - An option on the command line wins over its variable. Without both, the defaults stay `adobe` and `normal`. An empty variable counts as not set.
  - A value that is not a method or a sensitivity stops the command with exit code 1 and a message that names the variable and the values it takes.
  - With the method `adobe`, a sensitivity of `low` or `high` still stops the command, wherever each value comes from. The message names the variable that gave a value.
  - [Run it every day](docs/scheduling.md) now sets the two variables in the scripts of the job.
- Every output in the DuckDB guide now comes from one file, with four runs of Adobe's demo report suites dated 2026-11-12. Before, the guide mixed output from files of different days. The examples with `for_day` now use a day of that file that has alerts, and a block that is shortened says so.
- [The report](docs/report.md#when-not-to-choose-weekday-trend) now says when not to choose `weekday-trend`. It is a poor choice for a suite whose rise or fall can speed up or slow down, and it should not be relied on to report data that stops. The rule of the method does not change. Six changes to it were tested on four of Adobe's demo report suites, and none of them gave better results.

## 0.1.9 (2026-11-12)

This release adds a sensitivity for the local anomaly methods. The database layout stays at version 7, so files made by 0.1.2 to 0.1.8 keep working. The views version changes from 4 to 5, because the `alerts()` and `anomaly_days()` macros take a new option. The next `ahd run`, `ahd individual`, `ahd prune` or `ahd import` replaces them in your file. `ahd render` opens the file read-only, so it does not replace them. Until a command replaces them, your own SQL that gives `sensitivity` to a macro stops with an error.

- `ahd run`, `ahd individual` and `ahd render` take `--anomaly-sensitivity` with `low`, `normal` or `high`. It sets how wide the range of `weekday-median`, `weekday-iqr` and `weekday-trend` is. The default is `normal`, which gives the same ranges and flags as 0.1.8.
  - `weekday-median` and `weekday-trend` use 4 spreads with `low`, 3 with `normal` and 2 with `high`.
  - `weekday-iqr` uses 2 quartile ranges with `low`, 1.5 with `normal` and 1 with `high`.
  - The other rules stay the same, e.g., a range still reaches at least 5 percent on each side of the expected value.
- The sensitivity does not apply to `adobe`, because Adobe returns its own range. With `adobe`, `low` or `high` stops the command with an error.
- The sensitivity is not stored with the run, like the method. `ahd render` can write the report of any stored run again with another sensitivity.
- The header fact "Anomaly detection" names a sensitivity other than `normal`, e.g., "Weekday median, high sensitivity". "How large drops and spikes are found" states the factor in use.
- The alert line that `ahd run` prints uses the chosen sensitivity.
- The `alerts()` and `anomaly_days()` macros take `sensitivity := 'normal'`, `'low'` or `'high'`. Another value stops the query with an error. With `method := 'adobe'`, each of the three values changes nothing. See the [DuckDB guide](docs/duckdb.md#anomaly_daysrun-method--adobe-min_usual--10-sensitivity--normal).
- The alert email script `send_alerts.py` takes `--anomaly-method` and `--anomaly-sensitivity`, with the same values and defaults as `ahd run`. Before, it always used Adobe's flags, and the DuckDB guide said to change its query by hand for another method. With `adobe`, `low` or `high` stops the script with exit code 1, as on the command line. The email now says which method flagged the unusual values, and it names a sensitivity other than `normal`. See [Send an alert email every morning](docs/duckdb.md#send-an-alert-email-every-morning).
- [The report](docs/report.md#which-sensitivity-to-choose) has advice on which sensitivity to choose, with counts from four of Adobe's demo report suites.
- The DuckDB guide shows real output for the examples with `weekday-median`. It now says that `anomaly_days()` also has rows for the Individual Component Summary pairs, e.g., `individual/1`, and how to leave them out.
- The 0.1.8 entry below now links to the new heading of `anomaly_days()` in the DuckDB guide.

## 0.1.8 (2026-11-12)

This release adds a choice of anomaly method. The database layout stays at version 7, so files made by 0.1.2 to 0.1.7 keep working. The views version changes from 2 to 4, because the `alerts()` macro takes a new option and the file gets a new macro, `anomaly_days()`. Version 3 was only used before this release, by the development version without `weekday-trend`. The next `ahd run`, `ahd individual`, `ahd prune` or `ahd import` replaces them in your file. `ahd render` opens the file read-only, so it does not replace them. Until a command replaces them, your own SQL that gives `method` to `alerts()` stops with an error.

- `ahd run`, `ahd individual` and `ahd render` take `--anomaly-method` with `adobe`, `weekday-median`, `weekday-iqr` or `weekday-trend`. The default is `adobe`, which uses Adobe's anomaly detection as before. The other three compute the expected range from the run's own daily values. They are simpler than Adobe's anomaly detection and will flag different days. [The report](docs/report.md#anomaly-detection-methods) explains their rules and when to choose each.
  - `weekday-median` and `weekday-iqr` use the earlier values of the same weekday. They need 8 of them, so the first 8 of the 13 weeks have no range and no flags.
  - `weekday-trend` uses the average of the 7 days before a day as its level, and compares the day with how the earlier days of the same weekday compared with their level. It follows a component that grows or shrinks over the weeks. Only about the last 4 weeks have a range, and a day after a gap in the data has none.
- The method is not stored with the run. `ahd render` can write the report of any stored run again with another method, and it makes no requests to Adobe.
- A run whose credential may not use Adobe's anomaly detection, or whose test of it failed, can now get flags with a local method. With `adobe`, the Alerts tab of such a run suggests the `ahd render` command that does this.
- The report's header has a new fact, "Anomaly detection", which names the method. "How large drops and spikes are found" states the rule of the method in use. With a local method, every text that said that Adobe flagged a value names the method instead, and the list of earlier runs counts every run with that method.
- The alert line that `ahd run` prints uses the chosen method.
- The `alerts()` macro takes `method := 'adobe'`, `'weekday-median'`, `'weekday-iqr'` or `'weekday-trend'`. The default is `'adobe'`, so your existing SQL gives the same rows as before. The new macro `anomaly_days()` gives the expected value, the range and the flag of each component and day by the chosen method. See the [DuckDB guide](docs/duckdb.md#anomaly_daysrun-method--adobe-min_usual--10-sensitivity--normal).

## 0.1.7 (2026-11-11)

Fixes to the tool and to the docs. The database layout stays at version 7, and the views and the `alerts()` macro do not change.

- `ahd run --date` now makes a run when your computer does not know the report suite's time zone. Before, it stopped with the message that names `--date` as the way out. Such a run skips the check that the date is not after today in the suite's time zone.
- The report's list of earlier runs now orders the runs of one run date by the time they started. Before, it ordered them by their number. After an `ahd import` that gave old runs new numbers, the report of a run could leave out a run that was made before it on the same run date, and the report of the old run could list a newer run as earlier.
- A report answer from Adobe with a value of the wrong kind in one more place, e.g., an object where an expected value or the name of an item belongs, no longer ends the run with a database error. The request is listed in the report's status section under "Adobe sent an answer that could not be read", as 0.1.4 does for the other places.
- `ahd prune --before` now takes a date only in the form YYYY-MM-DD, as `ahd run --date` does. Before, it also took other forms, e.g., `2026-W45-1`, and its message for a wrong date was not plain.
- A run whose test of anomaly detection failed, e.g., on a network error, now shows "Anomaly detection not available" in the two tiles and "Not available" in the Flag column. Before, the report said "0, Flagged by Adobe" and "Adobe flagged no unusual values", although nothing could be flagged. A run with a credential that may not use anomaly detection already showed this.
- In the calendar, a day without data can now be clicked. Before, a click only worked on the outline of its square.
- A trend line in a table now shows a day with a value when the days before and after it have none. Before, such a day drew nothing, so a component with a value on one day only had an empty trend line.
- For a run made with `--date`, the hint on the Week box now names the last day. Before, it said "the 7 days ending yesterday".
- The "Show" and "Custom components" boxes and "Show only flagged" now start at their first setting after a reload in Firefox. Firefox puts back what you chose before a reload, and the tables then did not match these controls.

Fixes to the docs:

- The DuckDB guide's command for the notebook in your browser did not work. `duckdb -readonly -ui` opens a page that shows "Initialization Error", because the notebook cannot save its own notebooks in a read-only session. The guide now starts the notebook with `duckdb -ui -cmd "ATTACH 'data/ahd.duckdb' AS ahd (READ_ONLY); USE ahd;"`, which still keeps your file read-only. It also says that the file must exist, where to find your file, and that the terminal stays open while you use the notebook.
- The recipe "Values that dropped out of the top 100" listed each value once for every stored run. It now reads the latest run of each suite and names the suite.
- The docs now name `ahd import` among the commands that write to the file and that replace the views and the macro.
- The guide said that `unused` means a value of 0 on every day. It means a value of 0 or no value on every day.
- Troubleshooting now covers more messages: an answer from Adobe that could not be read, an error from Adobe without a code, a profile name that is not valid, and a database file that does not exist or holds no runs.
- Troubleshooting and "How runs are stored" now say which run is "the run before" and that a higher run number does not always mean a newer run, as 0.1.6 works.
- The docs said that `ahd runs` lists the newest run first. It lists the highest run number first.
- "The report" now says that a report with more than 400 components draws its tables when you pause, not on every step of a slider.

## 0.1.6 (2026-11-10)

A bug-fix release. The database layout stays at version 7, so files made by 0.1.2 to 0.1.5 keep working. The `latest_runs` view changes. The next `ahd run`, `ahd individual`, `ahd prune` or `ahd import` replaces it in your file.

- `latest_runs` now gives the run with the newest run date. Before, it gave the run with the highest number. That was the wrong run in two cases. The first is a run made later with `ahd run --date` for an old date. The second is an `ahd import` into a file that already held runs, because the imported old runs get higher numbers. The alert email script `send_alerts.py` then read the old run. If your own SQL uses `latest_runs`, it may now get another run than before. When two runs have the same run date, the one that was made last is the latest.
- The warning "this run sees fewer components than the run before it" now compares with the run that was made last before this one. Before, it compared with the run that had the next lower number. After an `ahd import` that gave old runs new numbers, the next run could be compared with an old run and warn for no reason.
- Two recipes in the DuckDB guide ordered runs by their number. They now order by run date: "Send only what is new since the last run" and "A history longer than 92 days". If you copied them, copy them again.
- An answer from Adobe that is valid JSON but not in the usual form no longer ends a command with an error trace. This covers the lists of report suites, segments, metrics and dimensions, the details of a report suite and the list of login companies. An entry of a list that cannot be read, e.g., a metric without an ID, is left out. A whole answer that cannot be read, e.g., a list where one report suite belongs, ends the command with one line. In a run of several suites, the other suites go on. 0.1.4 already covered the report answers.

## 0.1.5 (2026-11-10)

This release adds two commands and changes nothing that exists. The database layout stays at version 7, so files made by 0.1.2 to 0.1.4 keep working.

- `ahd export --out FOLDER` writes every stored run to a folder of Parquet files, e.g., as a backup or for other tools. It also reads a file that the tool refuses because of its layout.
- `ahd import SOURCE` copies the runs of another ahd database, or of a folder that `ahd export` wrote, into your database. It leaves out the runs that your database already holds. The runs keep their numbers when those are free.
- A change to the tables no longer costs you your stored runs. The tool still refuses a file with an older layout, and it still never changes such a file. Its message now says how to keep the runs: rename the file, then run `ahd import` with it. This works for layout 7 and newer, which means files from 0.1.2 on.
- The message for a file from a newer version of the tool now says to update the tool.

## 0.1.4 (2026-11-10)

A bug-fix release. The database layout stays at version 7, so files made by 0.1.2 and 0.1.3 keep working. The views and the `alerts()` macro do not change.

- The weekly view no longer shows a large drop or spike for a component with a small average week. It compared the average week with the daily minimum of 10, so a component with 2 a day could be flagged. The weekly view now skips a component whose average week is under 70, which is 10 a day, as the daily views do.
- A report with more than 400 components now draws its tables again when you pause, not on every step of a slider or every letter in the find box. Dragging a slider on a large report no longer stutters. A smaller report still draws at once.
- In the calendar, an arrow key at the edge of the grid now keeps the chosen day. Before, it jumped to the first or the last day, which could be another weekday.
- "Original order" under a sorted table now keeps the keyboard focus on the column's heading. Before, the focus went back to the top of the page.
- The sentences under the sliders are no longer read out again by a screen reader when their text did not change, e.g., while you type in the find box.
- The note under a table sorted by Flag or Number no longer says "A to Z".
- A chart of very small values, e.g., up to 0.2, now gives each line its own label. Before, two lines could both read "0.1".
- The report no longer scrolls sideways in a window between about 600 and 690 px wide.
- The report's Content Security Policy now also blocks a changed base address and any form target.
- After Ctrl-C, a request that was waiting to try again, e.g., after a rate limit, is no longer tried. Before, the command could take a minute or more to end.
- A database error in the middle of a run, e.g., a full disk, is now one line. In a run of several suites, the other suites go on. Before, the command ended with an error trace.
- The tool now asks for `requests` 2.31 or newer and `jinja2` 3.1 or newer.
- An answer from Adobe that is valid JSON but not a report, e.g., one with an empty value where a number belongs, no longer ends the run with an error trace. The requests that got such an answer are listed in the report's status section under "Adobe sent an answer that could not be read", and the run ends "partial" with the rest of its data.
- When Adobe sends a day in a form the tool does not know, the tool reads the day from Adobe's own number for it. That reading was wrong by one month, and it failed for every day in January. It is now correct. Adobe's usual answers were not affected.
- `ahd check` without `--rsid` now counts every report suite. Before, it stopped counting at 1,000.
- When the folder for the database cannot be made, e.g., because `--db` names a path through a file, the tool now says so in one line. Before, it ended with an error trace.

## 0.1.3 (2026-11-10)

A bug-fix release. The database layout stays at version 7, so files made by 0.1.2 keep working.

- A fix to the alert rules in the views or the `alerts()` macro no longer asks you to start a new database file. The next `ahd run`, `ahd individual` or `ahd prune` replaces the views and the macro in your file, and your stored runs then follow the new rules. A change to the tables still asks you to start a new file. If you built your own views on top of `component_days`, they follow the new rules after the update.
- Each file now has a `views_version` table with the version of its views and macro. If you go back to an older version of the tool, from 0.1.3 on, it puts back its own views and macro. The alert line of `ahd run` then always follows the same rules as its report.

## 0.1.2 (2026-11-09)

A bug-fix release.

This release changes the database layout to version 7. The tool refuses a file made by 0.1.0 or 0.1.1. Delete the old file, or choose a new one with `--db`, and run again.

- A temporary error from Adobe's sign-in service, e.g., HTTP 503, no longer ends a run. The tool tries the sign-in 3 times. If it still fails, only the requests that needed it fail, and the run ends "partial" with its report. Before, the run was marked "failed" although it held data, and no report was written. In a run of several suites, the other suites now go on. A wrong credential still stops every suite.
- When a new sign-in during a run fails, the requests that were waiting for it now get the same error at once. Before, each of them tried the sign-in again in turn, while every other request waited.
- The `alerts` macro no longer lists large drops or spikes for a day before the last 7 days of a run. Such a day falls inside the 12 weeks that its weekday average came from, so the average was wrong. In `component_days`, `usual` and `pct_vs_usual` are now empty for those days. Drops to zero and values that Adobe flagged are still listed for any day. The report's calendar already worked this way.
- A tile in the report now opens the Alerts tab on the day it counts. Before, the tab kept a day chosen there earlier, so its list could show a different count than the tile.
- `ahd run --date` now refuses a date after today in the report suite's time zone. Such a run treated a day without complete data as the last full day, so most components looked like large drops. It also refuses a date that is not written as YYYY-MM-DD, e.g., `20261107`, and a date so early or late that its 91 days cannot be counted. Before, such a date ended with an error trace.
- A report of a run with a segment now has the segment ID in its file name. A report no longer replaces the report of another run that has the same name, e.g., run 1 of another database file. The new report gets `_2` at the end of its name instead. Each report now names its run in a `<meta name="ahd-run">` tag, so the tool can tell them apart. Reports from earlier versions have no such tag and are still replaced.
- When the report cannot be written, e.g., because `--out` names a file or the disk is full, the tool now says so in one line. The line names the folder and says that the run is stored and how to write its report later. Before, `run`, `individual` and `render` ended with an error trace, and a run of several suites stopped.
- On Windows, output sent to a file or a pipe, e.g., a Task Scheduler log, no longer stops the tool at a name that the output cannot show, e.g., "日本サイト". The tool prints "?" for such characters.
- `ahd individual` can now add the run's own dimension and metric to a run whose first try at them stored nothing, e.g., a failed run. Before, it said that the run already held them. The empty pair and its problems are removed once the new pair is stored.
- A chart opened from a 13-week line or from the calendar now keeps the keyboard focus when its section was folded. Before, the page drew the chart again a moment later and the focus went back to the top of the page.
- The main chart now breaks its lines and its expected range at days without a stored value, as the 13-week line in the tables does. Before, it drew a straight line across them.
- A chart of a short series, e.g., a component with only one day of data, now names each day once along its bottom edge. Before, it drew the same date up to 5 times in one place.

## 0.1.1 (2026-11-09)

A docs-only release. The tool works the same as in 0.1.0.

- "Compared with the Excel workbook" now explains what the tool adds, grouped by what each addition does for you. It gives examples from a run on Adobe's Demo Data report suite.
- The docs now cover large spikes wherever they cover large drops, e.g., in the README and the alert rules in AGENTS.md.
- The docs no longer assume a clone of the repository. "Development" shows how to make one, and the scheduling guide and the email recipe say when they need one.
- "Development" says how `main` is protected, which checks must pass and how to merge, and lists the release steps.
- CONTRIBUTING.md and SECURITY.md are shorter and plainer.
- Smaller fixes, e.g., the name of the Custom components filter in "The report".

## 0.1.0 (2026-11-09)

The first release on PyPI, as `aa-health-duckdb`. It installs the `ahd` command.

- `ahd run` reads a report suite from the Adobe Analytics 2.0 API, stores the run in a local DuckDB file and writes one HTML report. It can check several suites, or every suite the credential can see.
- The report has a Components Summary, an Individual Component Summary and an Alerts Summary. It works offline and loads nothing from the internet.
- Alerts cover values that Adobe flagged, large drops and large spikes against the weekday average, and drops to zero. Two sliders set the drop and spike percents.
- The report has a calendar of alerts by day for 13 weeks, a 13-week line for each component, a list of slow declines and a table of earlier runs of the suite.
- The weekly view compares the last 7 days with the average week of the 12 weeks before.
- Every run stays in the database. The `alerts` macro and the views let you query the runs with your own SQL, e.g., to send alert emails.
- `ahd check`, `suites`, `segments`, `individual`, `render`, `runs` and `prune` cover sign-in checks, lookups, rebuilding reports and deleting runs.
- The database layout is version 6.
