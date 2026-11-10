# Changelog

This file lists the changes in each release. The version numbers follow [Semantic Versioning](https://semver.org/). Until 1.0.0, a minor release, e.g., 0.2.0, may change the database layout or the command options. The tool then asks you to start a new database file, and this file says so.

## Unreleased

This release adds two commands. The database layout stays at version 7.

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
