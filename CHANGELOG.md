# Changelog

This file lists the changes in each release. The version numbers follow [Semantic Versioning](https://semver.org/). Until 1.0.0, a minor release, e.g., 0.2.0, may change the database layout or the command options. A patch release holds fixes and additions that break nothing. The tool then asks you to start a new database file, and this file says so.

## Unreleased

**The report**

- A report of a run that did not finish no longer says that everything was read. Such a run, e.g., one that stopped because you closed the window, stays "running" and holds no problem for the components it did not read. Before, the status section said "No problems were found in this run.", and the problems tile said "Everything was read", although only the header said "Not finished". Now the status section warns that some components may not have been read, and that those components are not shown. The problems tile, the copied alerts and the copied problems say that the run did not finish. A failed run gets the same warning, also when it holds problems. A run fails when it read no component value, or when it stopped part way, e.g., on Ctrl-C or after a sign-in error. In both cases it may not have read every component.
- The note under the browser and device tables of the Individual tab says that the other columns show the same 10 values as the first column. So a value that ranks high only in another column, e.g., Apple, is not in the table. The hint above each table no longer says "Top 10 values for each major browser group", which said the opposite.
- On the Alerts tab of a run without Adobe's anomaly detection, the suggested `ahd render` command now has the same `--db` or `--profile` option that chose the database of the run, e.g., `ahd render --run 3 --profile prod --anomaly-method weekday-median`. Before, the command could read another database. Only the report of such a run holds this command, so any other report names no database file and no profile.

**Names from Adobe in the terminal**

- The text of an error from Adobe, its code and the error field of an answer from the sign-in service are now printed with "?" in place of each character that could change the terminal. The same holds for the names in the "already holds" error of `ahd individual`, and for the report suite ID in the errors for a dimension, a metric or a segment that the suite does not have.
- With `--verbose`, the line about a request that is tried again prints the path of the request, which can hold the report suite ID, with "?" in place of each character that could change the terminal. Before, it printed the path as it was.
- The Arabic letter mark, U+061C, is now replaced with "?" too. It sets the direction of the text around it, as the other marks that the tool already replaced do.

**The commands**

- When the program that reads the output closes it early, e.g., `ahd alerts --rsid my_suite | head -1`, the command now stops without a message and exits with 141. Before, it printed a Python traceback and exited with 120. This also holds on Windows for a large output, e.g., more than 8 KB of `ahd alerts --format json` with Python 3.12 or 3.13. Only the output counts. When the program that reads stderr, where the messages go, closes it early, e.g., `head` in `ahd alerts --rsid my_suite 2>&1 >alerts.txt | head -1`, the command does not exit with 141, and every line that it wrote to `alerts.txt` stays there.
- When a command fails or you press Ctrl-C, and the program that reads the output has closed it, the command now keeps its own exit code, e.g., 1 or 130, and prints only its own message. The same holds for a command that ends with 3 or 4, e.g., a partial run. Before, the command also printed "Exception ignored while flushing sys.stdout" and exited with 120.
- `ahd alerts` works when its output is closed before it starts, e.g., `ahd alerts --rsid my_suite >&-` in a job that only needs the exit code, or with `pythonw` on Windows. It writes nothing and exits with its usual code. Before, it stopped with a Python traceback and exited with 1.

**The database**

- DuckDB no longer downloads or loads an extension by itself. Before, a SQLite file given as `--db` or to `ahd import` or `ahd export` made DuckDB download its SQLite extension from DuckDB's server. The tool now refuses a file that is not a DuckDB file before DuckDB opens it. So DuckDB does not load its SQLite extension, also when it is installed on your computer. Such a file, e.g., a SQLite file or an empty file of 0 bytes, gets the plain message "is not a DuckDB database" from every command, also from `ahd import` and `ahd export`. Before, these two said "could not be read as an ahd database".
- `ahd prune --run`, `ahd render --run`, `ahd alerts --run` and `ahd individual --run` with a run number past the 64-bit limit say that there is no such run, e.g., "There is no stored run 100000000000000000000.", instead of saying that the database could not be read.

**The email and Slack scripts**

- `send_alerts.py` sends nothing when `SMTP_HOST` is set and `MAIL_FROM` or `MAIL_TO` is not. It prints one line that names the missing settings and exits with 1, also on a day without alerts. Before, it sent the email from `ahd@localhost` to `you@example.com` and exited with 0, so a typo in a scheduled job went unnoticed. `alerts.eml` still uses these two addresses when the settings are missing. `send_slack.py` already refused to post without its webhook URL.
- The line "No alerts for my_suite." of both scripts prints "?" in place of each character of the report suite ID that could change the terminal, as `ahd` does. Each script has its own copy of the rule, so that it still needs nothing else from `ahd`. The email and the Slack messages do not change.
- The scripts start `ahd` with `PYTHONIOENCODING=utf-8`. On Windows, a message of `ahd` with a letter such as é, e.g., in the path of the database, now reaches the log unchanged.

**The docs**

- [How runs are stored](docs/runs.md) names all three table macros, with `suite_events()`. Before, it said the file holds two.
- [The report](docs/report.md#individual-component-summary) says that the rows of the browser and device tables are the top 10 values in the first column only.
- [Usage](docs/usage.md#the-formats) shows how `ahd alerts --format json` writes a character outside ASCII, e.g., `\u65e5` for 日. Before, the example showed the character on both sides.

## 0.1.20 (2026-11-25)

This release fixes the same column as 0.1.19, now for dimensions, and moves to DuckDB 2.0.1. The database layout stays at version 7, and the views stay at version 9, so every file keeps working as it is. The daily alert rules do not change.

**The database**

- The `allowed_for_reporting` column of the `components` table now holds Adobe's value for props, list variables and Page in a run of `ahd run`. Before, it was NULL for them in most runs. `ahd run` first asks Adobe for the list of dimensions without this field, to find Page. Adobe then left the field out of the next list too, although the tool asked for it. The tool now also asks for `attributionModel`, and Adobe then sends the field every time. Runs made before this release keep NULL.

**DuckDB 2.0.1**

- The tool now needs DuckDB 2.0.1 or newer. DuckDB 2.0.1 changed its `round()`. It now rounds the exact value of a number, e.g., `round(12.35, 1)` gives 12.3, because 12.35 is stored as a number a little below 12.35. DuckDB 2.0.0 gave 12.4.
- The alert email script and the Slack script round the values they show in the same way. So a percentage in an alert message is the same as in your own SQL with DuckDB 2.0.1.

## 0.1.19 (2026-11-25)

This release fixes one column of the database. The database layout stays at version 7, and the views stay at version 9, so every file keeps working as it is. The daily alert rules do not change.

**The database**

- The `allowed_for_reporting` column of the `components` table now holds Adobe's value for each component that the tool reads from the list of metrics, e.g., custom events and eVars. Before, it was NULL for each of them, although Adobe sends the value. The props and the other components from the list of dimensions already had it. Runs made before this release keep NULL.

## 0.1.18 (2026-11-18)

This release fixes the faults that a review of the whole tool found after 0.1.17. The database layout stays at version 7, and the views stay at version 9, so every file keeps working as it is. The daily alert rules do not change.

**Runs**

- A run that read no component value is now "failed", also when the Individual Component Summary pair stored data. `ahd individual` keeps such a run "failed". Before, the run was "partial", and `ahd alerts` could choose it and report no alerts for a suite that was not checked.
- When you give no `--dimension` or `--metric` and the suite does not list Page or Occurrences, `ahd run` asks for the default pair anyway. The pair records an issue, and the components are still checked. Before, the run stopped for that suite. A value that you give is still refused when the suite does not have it.
- `--segment` takes a segment ID, e.g., `s300_5f1a2b9c`, also when the list of segments of the suite does not hold it. A name that is not in the list is still refused.
- An expected value or a bound that is not finite in an answer from Adobe, e.g., NaN, is now stored as NULL. Before, it was stored as NaN or infinity.
- A time zone name that the file system refuses, e.g., one that is too long, now gives the plain time zone message. Before, it gave an error from Python.

**Names from Adobe in the terminal**

- `ahd suites`, `ahd segments`, `ahd check`, `ahd individual`, `ahd runs` and the lines of `ahd run` print each name and ID from Adobe with "?" in place of each character that could change the terminal. The same holds for the list of login companies in the sign-in error, and for the IDs in the "More than one ... is named" error.

**Sign-in and settings**

- The sign-in now tries a request again when the connection drops while Adobe sends its answer, as the other requests already do.
- When Adobe's sign-in service asks for a longer wait with a Retry-After header, the tool now waits that long, up to 2 minutes, as it does for the other requests.
- An answer with HTTP 200 and no token now says that it did not come from Adobe's sign-in service, e.g., from a proxy or the sign-in page of a network. It no longer asks you to check the credential.
- When only some of the `AHD_*` variables are set, the message now names the missing variables, e.g., `AHD_SECRET, AHD_SCOPES`, instead of the keys of a config file.

**The database and ahd alerts**

- `ahd export` and `ahd import` keep the order of the rows inside a run. Before, a run with many issues could list them in another order after an import.
- `ahd prune --run` with a run number past the 32-bit limit says that there is no such run, instead of saying that the database could not be read.
- `ahd alerts` says "1 more alert of this event is not listed." when one alert is left.

**The report**

- The weekly view does not flag a week that lacks the value of a day. It still shows the sum, but gives no change from the average week, no large drop, large spike or zero flag, and says how many days have no value. The slider lines and "Show only flagged" leave it out. The chart readouts, also on the Individual tab, give the total without the average week.
- The calendar shows a day whose stored values are all empty, e.g., NaN, as a day where no data was read, not as a day with 0 alerts.
- The "Problems in this run" tile moves the keyboard focus to the status section.

**The email and Slack scripts**

- The scripts send the warning for a report suite ID without a finished run also when no other suite has anything to send, and also without `--always`. They then exit with 1. Before, a mistyped suite ID could give no message at all.
- When an email holds no alerts, its subject counts a suite without a finished run together with the suites that have a warning or a suite-wide event, e.g., "Analytics Health warnings for 2 report suites". Before, a suite with an event next to a missing suite gave the subject "Analytics Health warning for my_suite", which named only the suite with the event.
- With `--segment`, the email subject and headings, and the Slack header and notification text, name the segment. The line for a missing suite names it too.
- `send_alerts.py` gives one plain line when `SMTP_USER` or `SMTP_PASSWORD` is not ASCII. The line holds no part of the password. Before, Python's error could show a character of it.
- `send_alerts.py` names the real reason when the mail server lacks STARTTLS, a login (AUTH) or SMTPUTF8. Before, each of them was reported as a missing STARTTLS.
- `send_slack.py` with an `ahd` older than 0.1.13 says to update also on a day without alerts, not with a `KeyError`.
- `send_alerts.py` says "1 more alert of this event is not listed." for one. In a long Slack list, the line that counts the suites left out says "1 more suite has no alerts." for one.
- The Windows examples use `set "NAME=value"`, so a password with `&` or `^` works, and say that a `%` in a `.cmd` file must be written `%%`.

## 0.1.17 (2026-11-18)

This release fixes three small faults in the alert messages. The database layout stays at version 7, and the views stay at version 9, so every file keeps working as it is.

**Fixes**

- `ahd alerts` now counts the failed runs after a run in the same order as `latest_runs`, also when a run has no start time. A file filled by `ahd import` from an older layout can hold such runs. Before, the warning could leave out a failed run of the same day.
- The Slack all-clear message and the message for missing suites name every suite. Before, past about 50 suites, or about 25 suites without a finished run, the text passed Slack's limit of 3000 characters a section, and the last suites were cut off without notice. For a very large list, the message names the suites that fit and counts the rest.
- With `--new-only`, an email or Slack message that holds only an ongoing suite-wide event no longer says "warning" in its subject or notification text. It says, e.g., "Analytics Health: a suite-wide event goes on in my_suite". An old run or a missing suite still gets a warning.

## 0.1.16 (2026-11-17)

This release makes the alert emails and Slack messages easier to trust. A quiet inbox could mean healthy suites or a daily job that had stopped, and now the two look different. The database layout stays at version 7, and the views stay at version 9, so every file keeps working as it is.

**Old runs**

- `ahd alerts` says when the latest finished run of a suite is old, which means that its alerts are for a day before yesterday in the time zone of the suite. This happens when the runs after it failed, when the sign-in failed, or when the daily job stopped. A run chosen with `--run` is named by its number. The text has a warning line, e.g., "Warning: The latest finished run of my_suite has the alerts of 2026-11-13, but yesterday was 2026-11-16 in the time zone of the suite. 2 later runs of it failed." The exit codes do not change. See [Old runs](docs/usage.md#old-runs).
- The JSON of each run has the new fields `current_day`, `stale`, `failed_after` and `warning`. The CSV has the new columns `current_day`, `stale` and `failed_after` after the fields of the run. `format_version` stays 1.
- The new option `ahd alerts --date YYYY-MM-DD` checks the runs as if today were that date.

**Only the new alerts**

- `ahd alerts --new-only` lists only the alerts that are new on the day, in every format. Each suite-wide event keeps its line, also when none of its alerts is new, so a suite whose data stopped days ago is still named. See [Only the new alerts](docs/usage.md#only-the-new-alerts).

**The email and Slack scripts**

- `--test` sends a test message to check the mail settings or the webhook. It reads no alerts and does not need `ahd`.
- `--always` sends a short all-clear message on a day without alerts, so a day without a message means that the job did not run. A suite you named that has no finished run makes the message a warning, not an all-clear. See [Know when the job stops](docs/scheduling.md#know-when-the-job-stops).
- `--new-only` lists only the new alerts. It needs `ahd` 0.1.16 or newer.
- With `ahd` 0.1.16 or newer, a suite whose latest finished run is old gets a warning in the email or the Slack message, also without alerts. The scripts then exit with 1.

**The docs**

- [Troubleshooting](docs/troubleshooting.md#email-and-slack-alerts) has a new section on the messages of the alert scripts: the warning about an old run, a suite without a finished run, `--test` and `--new-only`.
- [Run it every day](docs/scheduling.md#know-when-the-job-stops) explains how `--always` and the warning show that a daily job stopped.
- The download links of the alert scripts hold `v0.1.16`, so the scripts and the tool come from the same release.

## 0.1.15 (2026-11-15)

This release changes no code of the tool. It adds an example script for Slack and makes the alert scripts easy to use without a clone of this repository. The database layout stays at version 7, and the views stay at version 9, so every file keeps working as it is.

**Alerts in Slack**

- The new example script `docs/examples/send_slack.py` posts the alerts of the latest runs to a Slack channel, with one message for each suite that has alerts. It posts with an incoming webhook, which is a URL that Slack makes for one channel. It takes the options of `send_alerts.py` and has its exit codes. It needs `ahd` 0.1.13 or newer. See [Slack alerts](docs/slack-alerts.md).

**The docs**

- Email alerts and Slack alerts each have their own page, with numbered steps from the install to a first email or Slack message. See [Email alerts](docs/email-alerts.md#step-by-step).
- The steps no longer need a clone of this repository. You install `ahd` from PyPI and download only the script. The download link holds the version of the release, e.g., `v0.1.15`, so the script and the tool come from the same release.
- [Run it every day](docs/scheduling.md) now uses `ahd` from PyPI and the downloaded script, in one folder. The daily job adds `~/.local/bin` to `PATH`, so a scheduled job finds `ahd` and `uv`. The steps for a clone are still there.

## 0.1.14 (2026-11-14)

This release fixes bugs that a review of the whole tool found. The database layout stays at version 7, so files made by 0.1.2 to 0.1.13 keep working. The views version changes from 8 to 9, because two alert rules change and the `component_days` view gets a new column `first_data` at the end. The next `ahd run`, `ahd individual`, `ahd prune` or `ahd import` replaces the views and the macros in your file. `ahd render` and `ahd alerts` open the file read-only, so they do not replace them, but they use the new rules for their own session.

**Alerts**

- A day before a component's first day with data is never an alert, with any method. Before, a new component counted as "dropped to zero" on every day before it started. So the calendar, the lists, the suite-wide events and `alerts()` for an older day can show fewer alerts. The alerts of yesterday change only when Adobe flagged a day without data. The number of components that collected data, which the events use, does not change.
- Adobe sometimes flags a value without the upper end of its range. Such a flag is now "Above range" when the value is above the expected value. Before, it was always "Below range", and it could join the wrong suite-wide event.
- The report writes a drop or spike percent with its decimal, e.g., "27.5%", as `ahd alerts` does. Before, it wrote "28%" for a threshold of 27.5.
- `ahd alerts --format csv` puts a single quote in front of a text that a spreadsheet would run as a formula, e.g., a component name that starts with `=`. Numbers stay as they are. See [Usage](docs/usage.md#the-formats).

**The alert email**

- `send_alerts.py` now checks the certificate and the host name of the mail server. Before, it sent `SMTP_PASSWORD` over a connection that it did not check.
- It waits at most 60 seconds for the mail server. When it cannot send, it prints one line that says why and exits with 1. Before, a server that did not answer could hold the job forever, and an error printed a trace.
- The rules text in the email gives the exact percent, e.g., 27.5% for `--threshold 0.275`.

**Runs and their status**

- A run stopped with Ctrl-C now records an issue for each component that it did not read, and its report says so. It stays "failed" after `ahd individual`. Before, `ahd individual` could make it "partial", and `ahd alerts` and the email then used a run that lacked components without saying so.
- `ahd individual` sets a run to "ok" when the pair it replaced held the run's only issues. Before, the run stayed "partial".
- `ahd individual` can try again a pair whose browser and device tables came back empty. Before, it said that the run already held the pair.
- A trend day that Adobe sends without a value is now recorded as an issue. Before, the day was left out without a word.
- A value of NaN, of infinity or a number too large to read now counts as an answer that could not be read. Before, it was stored, or it ended the run with an error trace.
- Each run sends one request less, because the answer of the anomaly test is stored as the trend of its component.

**Commands**

- `--config` together with all four `AHD_*` credential variables now stops with a message. Before, the variables won and the file was ignored without a word, so a run could read another organization. See [Configuration](docs/configuration.md).
- Options that cannot be used together now exit with 2 and print the usage, as [Usage](docs/usage.md) says, e.g., `--profile` with `--config`, or `ahd alerts --segment` with `--run`. Before, they exited with 1.
- `ahd prune --run N` exits with 1 and deletes nothing when a run that it names is not stored. Before, it exited with 0.
- `ahd run` no longer leaves a new empty database behind when it stops before it stores a run, e.g., after a failed sign-in.
- The hint after a report that could not be written now names the `--db` or the profile in use.
- A `~` at the start of a path option now means your home folder, also in quotes, e.g., `--db "~/ahd/ahd.duckdb"`.
- A DuckDB file that ahd did not make now gets the message "The database ... is not an ahd database. Choose another file with --db." Before, it was told to delete the file. A new database file is made in one step, so Ctrl-C cannot leave it half made.
- `ahd import` reads a folder whose name holds `[`, `]`, `*` or `?` as it is. Before, it could read another folder that the name matched as a pattern.
- The help of `--profile` says what it chooses for each command: the credential, the database or both.

**Sign-in and requests**

- The Analytics discovery call at sign-in is now tried 3 times after a temporary error, like the token request. Before, one error there stopped the run.
- `ahd run` keeps a connection open for each of up to 16 workers. Before, it kept 10, and with more workers it printed warnings and made new connections.
- After Ctrl-C, no thread sends a request or signs in again, and a wait between sign-in tries ends at once.

**The report**

- The chart axis of a currency metric with small values gives each line its own label, e.g., $0.05 and $0.10. Before, two lines could both read $0.1.
- Percent changes, thresholds and short numbers follow the language of the browser, e.g., "-12,5 %" in German, as the other numbers and the dates already did.
- "Copy alerts" copies the last slider setting in both versions, also on a large report that is still waiting to draw.
- A screen reader can enter the trend chart and the calendar with the arrow keys and says each day.

**The package**

- The source package now holds the files its tests read, so the tests pass in it.
- The release workflow now checks that the wheel holds its SQL, its template and its page files, with the new `scripts/check_wheel.py`.

## 0.1.13 (2026-11-12)

This release gives one line to a problem that hits most of a report suite at once, e.g., when its data stops. The database layout stays at version 7, so files made by 0.1.2 to 0.1.12 keep working. The views version changes from 7 to 8, because the `alerts()` macro gets a new column and a new macro `suite_events()` comes next to it. The next `ahd run`, `ahd individual`, `ahd prune` or `ahd import` replaces them in your file. `ahd render` and `ahd alerts` open the file read-only, so they do not replace them, but they use the new rules for their own session. Which components are alerts does not change, and neither do the counts in the tiles, the list headings, the alert line and the earlier runs.

- A suite-wide event is a group of at least 10 alerts of the same kind that covers at least half of the components that collected data in the run. The kind is the condition that the Since column follows: a value of 0, a value below or above the expected range, a large drop or a large spike. Its line names the start that most of its alerts share, e.g., "356 of the 356 components that collected data had a value of 0 on Wed, Nov 11 (yesterday). For 330 of them, this started on Nov 5. For the other 26, it started on other days." [The report](docs/report.md#suite-wide-events) explains the rule and its numbers.
- **What looks different in the report:** a day with an event starts the Alerts Summary with "Suite-wide events" and one line for each event. Its rows are folded under the line, behind "Show the 356 alerts". The lists below leave these rows out and say how many of their alerts are in an event, e.g., "All 356 are part of a suite-wide event above, which lists them." The heading of each list and the tiles still count every alert. Moving a slider can make or end an event of large drops or spikes. The copied alerts start with each event, its first 5 alerts and a count of the others. A day without an event looks as before.
- **What looks different in `ahd alerts`:** the text gives each event a line that starts with "Suite-wide event:", its first 5 alerts and a line that counts the others, then "Other alerts:" and the alerts outside an event. The new option `--every-alert` lists every alert of an event. The JSON gives each run a new list `events`, and each alert a new field `event`, e.g., `"zero"` or `null`. Every alert stays in the `alerts` list, so a program that ignores the new fields gets what it got before. `format_version` stays 1. The CSV has a new column `event` at the end. See [Usage](docs/usage.md#suite-wide-events-in-the-output).
- **What looks different in the alert email:** `send_alerts.py` starts the section of a suite with an event with the line of the event and a table of its first 5 alerts, then lists the other alerts. On Demo Data Evangelists, the email has 5 rows in place of 356. Its options, variables and exit codes stay the same, and the subject still counts every alert. With an older `ahd`, the email lists every alert as before.
- The `alerts()` macro has a new column `event` at the end. The other columns and the rows stay the same. The new macro `suite_events()` takes the same options and gives one row for each event. See the [DuckDB guide](docs/duckdb.md#suite_eventsrun-).

## 0.1.12 (2026-11-12)

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
