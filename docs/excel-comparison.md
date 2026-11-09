# Compared with the Excel workbook

[README](../README.md) · [Usage](usage.md) · [Configuration](configuration.md) · [The report](report.md) · [How runs are stored](runs.md) · [DuckDB guide](duckdb.md) · [Run it every day](scheduling.md) · [Troubleshooting](troubleshooting.md) · **Compared with the Excel workbook** · [Development](development.md)

aa-health-duckdb is an unofficial alternative to Adobe's Excel workbook ["Analytics Health Dashboard"](https://new.express.adobe.com/webpage/y6DGOeWmpo7Uh). It covers what the workbook does, and it changes a few things where that gives a better result. This page lists the differences.

## How the numbers differ

- **The week** is the 7 days ending yesterday. **The baseline** is the 84 days from 91 days ago through 8 days ago, divided by 12 to give a weekly average.
- **"Today"** is today in the report suite's time zone, not your computer's. The workbook asks for today's partial data as well. The tool fetches that row and ignores it.
- **The top 100 tables** compare yesterday with the 90 days before it, from 91 days ago through 2 days ago. The browser and device tables use yesterday.
- **Arrows** show when the change is past a slider's percent: a down arrow past the drop slider, 30 percent by default, and an up arrow past the spike slider, 50 percent by default. The workbook uses a fixed 20 percent. When the baseline is zero, there is no percent change and no arrow.
- **Zero values.** The workbook alerts on every component with a value of 0. The tool alerts on a component that dropped to 0, and it lists a component that was 0 on every day separately, as never collected data.
- **Large drops.** The workbook alerts only on Adobe's anomaly flags and on zero values. The tool also alerts on drops and spikes larger than the sliders' percents.
- **Unreadable data.** A column the credential cannot read is listed as a problem. It is never shown as 0.
- **Currency.** A currency metric shows in the report suite's currency, e.g., $1,234.56.
- **eVars** are treated as dimensions, like props, so they appear in the Dimension chart. The workbook filed them under events. Each eVar is still counted with its instances metric, as in the workbook.

## What the tool adds

- **Several Individual Component Summary pairs per run.** The workbook keeps one pair at a time. The tool keeps every pair you add, for any dimension and metric of the suite, and switches between them at once.
- **History.** Every run stays in the database, so you can compare runs over weeks. The workbook replaces its data on each refresh.
- **Alerts for any of the last 7 days**, with sliders for large drops and spikes. Each alert shows how many days in a row it held.
- **Charts that mark the chosen day** and its weekday average.
- **A calendar of alerts by day** for the last 13 weeks, for the whole suite. It shows the days when many components broke at once.
- **Earlier runs** of the same suite, with their alert counts, in each report.
- **Slow declines**, i.e., components that fell a little each week for 12 weeks. No single day in such a fall is unusual enough for an alert.
- **A warning when components disappear** between runs, which usually means a permission change.
- **Plain sign-in errors** that name the cause and the fix.
- **SQL on every run, suite and organization**, with recipes for alert emails, owner routing and audits. See the [DuckDB guide](duckdb.md).
- **Any system.** The report opens in any browser, offline. The workbook needs Excel on Windows.

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

- **The version check on open.** It is not needed for a private tool.
- **Languages.** The workbook offers 9 languages. The tool is in English only.
- **Outlook email.** The workbook builds an Outlook email on Windows. The tool copies the alerts as a formatted table for any email program, and the DuckDB guide shows how to send them on a schedule.
- **The manual token form.** The tool signs in with an OAuth Server-to-Server credential, with no browser step.
