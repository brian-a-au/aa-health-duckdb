# Compared with the Excel workbook

[README](../README.md) · [Usage](usage.md) · [Configuration](configuration.md) · [The report](report.md) · [How runs are stored](runs.md) · [DuckDB guide](duckdb.md) · [Run it every day](scheduling.md) · [Troubleshooting](troubleshooting.md) · **Compared with the Excel workbook** · [Development](development.md)

aa-health-duckdb is an unofficial alternative to Adobe's Excel workbook ["Analytics Health Dashboard"](https://new.express.adobe.com/webpage/y6DGOeWmpo7Uh). It covers what the workbook does, and it changes a few things where that gives a better result. This page lists the differences.

## How the numbers differ

- **The week** is the 7 days ending yesterday. **The baseline** is the 84 days from 91 days ago through 8 days ago, divided by 12 to give a weekly average.
- **"Today"** is today in the report suite's time zone, not your computer's. The workbook asks for today's partial data as well. The tool fetches that row and ignores it.
- **The top 100 tables** compare yesterday with the 90 days before it, from 91 days ago through 2 days ago. The browser and device tables use yesterday.
- **Arrows** show when the change is past a slider's percent: a down arrow past the drop slider, 30 percent by default, and an up arrow past the spike slider, 50 percent by default. The workbook uses a fixed 20 percent. When the baseline is zero, there is no percent change and no arrow.
- **Zero values.** The workbook alerts on every component with a value of 0. The tool alerts on a component that dropped to 0, and it lists a component that was 0 on every day separately, as never collected data.
- **Large drops and spikes.** The workbook alerts only on Adobe's anomaly flags and on zero values. The tool also alerts on drops and spikes larger than the sliders' percents.
- **Unreadable data.** A column the credential cannot read is listed as a problem. It is never shown as 0.
- **Currency.** A currency metric shows in the report suite's currency, e.g., $1,234.56.
- **eVars** are treated as dimensions, like props, so they appear in the Dimension chart. The workbook filed them under events. Each eVar is still counted with its instances metric, as in the workbook.

## What the tool adds

The workbook shows one report suite as it was when you last refreshed it. The tool keeps every run, so it can show how a problem started and whether it is over. It also checks for more kinds of problems than Adobe's anomaly flags. The groups below say what each addition does for you.

The examples come from a run on Adobe's Demo Data report suite on 2026-11-09, with 410 components.

### Catch more problems

- **Large drops and spikes.** Adobe's anomaly detection flags a value only when it leaves Adobe's expected range, and that range can be wide. The tool also compares each value with its weekday average, the average of the same weekday in the 12 weeks before. In the Demo Data run, Adobe flagged no value for the day, but 27 components were more than 30 percent below their weekday average.
- **Sliders for the percents.** You choose how large a drop or a spike must be, e.g., -30% and +50%. Every count and list on the page changes as you move a slider, so you can see at once how many components are close to the limit.
- **Spikes as well as drops.** A tag that fires twice doubles a value, i.e., a change of about +100%. In the Demo Data run, one eVar was 69.5 percent above its weekday average for 5 days in a row, and Adobe flagged none of those days.
- **Zero values that mean something.** The workbook alerts on every component with a value of 0, so unused slots fill the list every day. The tool alerts only on a component that had data and then dropped to 0. It lists the components that never collected data apart. In the Demo Data run, 42 components never collected data, and none of them is an alert.
- **Slow declines.** A component that falls a little each week never has a day that is unusual enough for an alert. The tool fits a line through 12 weekly totals and lists the components that fell by 3 percent or more a week.
- **Any of the last 7 days.** The Day picker shows the alerts of each of the last 7 days, e.g., the weekend's on a Monday morning.
- **Days in a row.** Each alert says how many days it has held, so you can tell a new problem from one that started last week.
- **A weekly view.** It compares the last 7 days with the average week of the 12 weeks before. It counts Adobe's flags by day, e.g., "Below range on 2 days", so a component that Adobe flagged on one day of the week still stands out.

### Find the cause faster

- **A calendar of alerts by day.** It shows the last 13 weeks for the whole suite. A release that breaks tracking usually breaks many components on the same day, so it shows as one dark square. In the Demo Data run, Sep 18 stands out, with 368 components that had an alert. Nearly all of them were below their expected range. Choose a day to list its components.
- **Charts with context.** Each chart shows 91 days with Adobe's expected range. It marks the chosen day and its weekday average, so you see how far the day is from normal. Choose a component in the calendar or a table to open it on the chart.
- **A 13-week line in each row.** Every component in the tables has a small line of its last 13 weeks. A drop to zero or a break in tracking shows as a dip at the right end, without opening a chart.
- **Find a component.** The find box keeps the components that match what you type, e.g., "evar5". The Custom components filter shows one custom type or one range of numbers, e.g., eVar101 to eVar200, with how many components each holds.
- **Several Individual Component Summary pairs.** The workbook keeps one dimension by metric pair at a time. The tool keeps every pair you add to a run, e.g., Internal Search Term by Cart Additions, and switches between them at once.

### Keep a history

- **Every run stays.** The tool stores each run in a local DuckDB file. The workbook replaces its data on each refresh, so last week's numbers are gone.
- **Earlier runs in each report.** A table counts the alerts of each earlier run of the same suite. If you run the tool every day, it shows when a problem started and whether it is getting better.
- **A warning when components disappear.** When a run sees far fewer custom components than the run before, the tool says so. The usual cause is a change to the credential's permissions, and the warning names it.
- **SQL on every run.** You can query all your runs with SQL, across report suites and organizations. The [DuckDB guide](duckdb.md) has recipes, e.g., an email with only the alerts that are new since the last run.

### Check many report suites

- **Several suites in one command.** `ahd run --all-suites` checks every suite the credential can see, and writes one report for each. The workbook checks one suite at a time. A suite with 410 components takes about 1 minute.
- **Segments.** `--segment` applies a segment to every request, e.g., to check mobile traffic on its own.
- **Reports without new requests.** `ahd render` writes a report again from a stored run, e.g., with other slider settings, and makes no API calls.

### Share the results

- **One HTML file.** The report works in any browser, offline, e.g., as an email attachment. It has light and dark modes, and it prints with every section open.
- **Alerts ready to paste.** Copy alerts puts a formatted table and plain text on the clipboard. Outlook and Gmail paste the table, and Slack or a ticket pastes the text. The workbook builds an email in Outlook on Windows only.
- **Problems ready to paste.** When some data could not be read, Copy problems lists each problem with its cause, for an email or a ticket.
- **Daily checks.** The tool can run on a schedule, and email the alerts only when there are any. See [Run it every day](scheduling.md).

### Trust the numbers

- **Unreadable data is never a zero.** A column the credential cannot read is listed as a problem in the status section. The workbook would show it as 0, which looks like a tracking failure.
- **Plain sign-in errors.** When sign-in or access fails, the message names the cause and the fix.
- **One set of rules.** The report and the `alerts` macro for your own SQL use the same alert rules, and tests check that they agree.
- **Read-only and private.** The tool only reads from Adobe. It never shows or stores your credential, and it sends no usage data anywhere. The report loads nothing from the internet.

### Use it anywhere

- **Any system.** The tool runs on macOS and Linux as well as Windows, with Python 3.12 or newer. The workbook needs Excel on Windows.
- **Easy to install and update.** `uv tool install aa-health-duckdb` installs it from PyPI, and `uv tool upgrade aa-health-duckdb` updates it.
- **Keyboard and screen reader.** The charts and the calendar work with the arrow keys. Every table and control has a name that a screen reader reads out, and the text meets contrast checks in both themes.

## Performance

The workbook was not timed for this page. This section gives the times of the tool only.

The tool was timed on 2026-11-08, on a demo report suite with 410 components. Each run used a date that no earlier run had used, so Adobe had no saved answers to reuse. Each setting ran twice.

| `--workers` | `--batch-size` | Faster run | Slower run |
|---|---|---|---|
| 2 | 20 | 2 min 53 s | 3 min 21 s |
| 4 | 20 | 1 min 43 s | 2 min 15 s |
| 8 (default) | 20 | 1 min 0 s | 1 min 9 s |
| 4 | 10 | 1 min 38 s | 2 min 1 s |
| 4 | 40 | 1 min 42 s | 1 min 55 s |

- Nearly all of a run is waiting for Adobe to answer the report requests. The tool's own work after the last answer takes less than a second.
- `--workers` sets how many requests run at the same time. More workers made a run faster. With 8 workers, each request took about as long as with 4. The default is 8 for this reason.
- `--batch-size` sets how many components one request asks for. It made little difference, because Adobe takes longer to answer a larger request.
- Adobe sent no rate limit errors in these runs.
- These runs were made before the tool started to follow Adobe's limit of 12 requests in any 6 seconds. Two later runs with 8 workers on the same suite, each with a new date, took 56 s and 1 min 10 s. In the slower run, the limit made the tool wait about 2 seconds in all.
- A second run for the same date can be much faster, e.g., 32 seconds instead of 112, probably because Adobe reuses its earlier answers.

## What it leaves out, on purpose

- **The version check on open.** The tool comes from PyPI, so `uv tool upgrade aa-health-duckdb` gets the latest version.
- **Languages.** The workbook offers 9 languages. The tool is in English only.
- **Outlook email.** The workbook builds an Outlook email on Windows. The tool copies the alerts as a formatted table for any email program, and the DuckDB guide shows how to send them on a schedule.
- **The manual token form.** The tool signs in with an OAuth Server-to-Server credential, with no browser step.
