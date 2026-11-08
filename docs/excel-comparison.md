# Compared with the Excel workbook

[README](../README.md) · [Usage](usage.md) · [The report](report.md) · [DuckDB guide](duckdb.md) · **Compared with the Excel workbook**

aa-health-duckdb is an unofficial alternative to Adobe's Excel workbook ["Analytics Health Dashboard"](https://new.express.adobe.com/webpage/y6DGOeWmpo7Uh). It covers what the workbook does, and it changes a few things where that gives a better result. This page lists the differences.

## How the numbers differ

- **The week** is the 7 days ending yesterday. **The baseline** is the 84 days from 91 days ago through 8 days ago, divided by 12 to give a weekly average.
- **"Today"** is today in the report suite's time zone, not your computer's. The workbook asks for today's partial data as well. The tool fetches that row and ignores it.
- **The top 100 tables** compare yesterday with the 90 days before it, from 91 days ago through 2 days ago. The browser and device tables use yesterday.
- **Arrows** show when the change is more than the slider's percent, 30 percent by default. The workbook uses a fixed 20 percent. When the baseline is zero, there is no percent change and no arrow.
- **Zero values.** The workbook alerts on every component with a value of 0. The tool alerts on a component that dropped to 0, and it lists a component that was 0 on every day separately, as never collected data.
- **Large drops.** The workbook alerts only on Adobe's anomaly flags and on zero values. The tool also alerts on drops larger than the slider's percent.
- **Unreadable data.** A column the credential cannot read is listed as a problem. It is never shown as 0.
- **Currency.** A currency metric shows in the report suite's currency, e.g., $1,234.56.
- **eVars** are treated as dimensions, like props, so they appear in the Dimension chart. The workbook filed them under events. Each eVar is still counted with its instances metric, as in the workbook.

## What the tool adds

- **Several Individual Component Summary pairs per run.** The workbook keeps one pair at a time. The tool keeps every pair you add, for any dimension and metric of the suite, and switches between them at once.
- **History.** Every run stays in the database, so you can compare runs over weeks. The workbook replaces its data on each refresh.
- **Alerts for any of the last 7 days**, with a slider for large drops.
- **A warning when components disappear** between runs, which usually means a permission change.
- **Plain sign-in errors** that name the cause and the fix.
- **SQL on every run, suite and organization**, with recipes for alert emails, owner routing and audits. See the [DuckDB guide](duckdb.md).
- **Any system.** The report opens in any browser, offline. The workbook needs Excel on Windows.

## What it leaves out, on purpose

- **Usage tracking.** The workbook sends usage data to Adobe. The tool never sends anything.
- **The version check on open.** It is not needed for a private tool.
- **Languages.** The workbook offers 9 languages. The tool is in English only.
- **Outlook email.** The workbook builds an Outlook email on Windows. The tool copies the alerts as a formatted table for any email program, and the DuckDB guide shows how to send them on a schedule.
- **The manual token form.** The tool signs in with an OAuth Server-to-Server credential, with no browser step.
