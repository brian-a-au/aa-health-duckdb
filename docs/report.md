# The report

[README](../README.md) · [Usage](usage.md) · [Configuration](configuration.md) · **The report** · [How runs are stored](runs.md) · [DuckDB guide](duckdb.md) · [Run it every day](scheduling.md) · [Troubleshooting](troubleshooting.md) · [Compared with the Excel workbook](excel-comparison.md) · [Development](development.md)

Each run writes one HTML file, e.g., `reports/ahd_my_suite_2026-11-07_run3.html`. This page walks through everything on it.

## Contents

- [Open and share it](#open-and-share-it)
- [The top of the page](#the-top-of-the-page)
- [The large drop slider](#the-large-drop-slider)
- [Summary tiles](#summary-tiles)
- [Status](#status)
- [Components Summary](#components-summary)
- [Individual Component Summary](#individual-component-summary)
- [Alerts Summary](#alerts-summary)
- [How values are shown](#how-values-are-shown)

## Open and share it

The report is one file with its styles, script and data inside it. Open it in any browser by double clicking it. It loads nothing from the internet, so it also works offline, e.g., as an email attachment.

Working offline does not mean the report is cleared for sharing. It holds your component names and values, so share it only where your organization allows.

A switch at the top right chooses light mode, dark mode or your computer's setting. Your browser remembers the choice. Every table can be sorted by clicking a column heading. Every table also has a name that a screen reader reads out, e.g., "Custom events" or "Top 100 values yesterday".

## The top of the page

The header names the report suite, the Individual Component Summary pairs, the segment, the run date in the report suite's time zone, when the run was made, how long it took, and its status:

- **Complete:** everything was read.
- **Complete, with some gaps:** the run finished, but some data could not be read. The status section lists it.
- **Failed:** no data could be read.
- **Not finished:** the run stopped in the middle, e.g., because the window was closed.

Every day in the report counts back from the run date. "Yesterday" is the day before the run date. For a normal run, that is the day before the run was made.

A run made with `ahd run --date` is about an earlier day. A line under the header then says "Run date set with --date", and names the last day of the report, e.g., 2026-09-15. The page uses that date instead of the word "yesterday", and the Day pickers mark it "(day before the run date)".

## The large drop slider

The slider above the tiles sets how far a value must fall below its usual value to count as a large drop. It goes from 10 to 100 percent, and it starts at 30 percent, or at the value you chose with `--change-alert`.

- **"Usual"** is the 12-week average for the same weekday, e.g., the average of the last 12 Mondays for a Monday.
- **Small numbers are skipped.** Components with a usual value under 10 are never large drops, because small numbers change a lot from day to day.
- **Arrows follow the slider.** The tables show an up or down arrow when a value changes by more than the same percent.
- **Everything updates at once.** When you move the slider, the tiles, the tables, the "Show only flagged" filter and the alerts change right away.

A line under the slider says how many components are large drops on the day the Components Summary shows. It also names the largest drop, even when that drop is under the percent, so you can see why nothing was flagged.

## Summary tiles

The tiles count yesterday's results:

- components checked
- values above and below Adobe's expected range
- large drops
- components that dropped to zero, and how many others never collected data
- problems in the run

Click a flag, drop or zero tile to open the Alerts Summary. Click the problems tile to jump to the status section.

## Status

The status section lists anything that could not be read, grouped by cause, with a plain explanation of each. **Copy problems** copies the list for an email or a ticket. See [Copying](#copying).

It also shows a warning when the run sees far fewer custom events, eVars or props than the run before it. See [Troubleshooting](troubleshooting.md#fewer-components-than-last-time).

## Components Summary

This tab shows every custom event, eVar and prop, then the solution components, e.g., reserved purchase events and Activity Map instances.

### Controls

- **Show:** daily or weekly. Weekly shows the 7 days ending yesterday, against the usual week.
- **Day:** any of the last 7 days. Yesterday comes first.
- **Component numbers:** a range, e.g., 101 to 200, for custom events, eVars and props.
- **Show only flagged:** hides everything that is not an Adobe flag, a large drop or a drop to zero.

### Charts

Two charts show 91 days of one metric and one dimension. Each shows the actual values, Adobe's expected value and expected range, and markers where Adobe flagged a value. Use the arrow keys on a chart to read each day.

- **The Metric chart** starts on Occurrences. Its Type filter lists the metric groups that have data, e.g., custom events or reserved purchase events.
- **The Dimension chart** starts on Page. Its Type filter chooses eVars, props or other dimensions.

### Tables

Each row shows:

- the component number and name
- the total for the day or the week
- the usual value
- the change from usual, with an arrow when it passes the slider's percent
- a flag

The flag is Adobe's "Above range" or "Below range", or "Large drop" when the slider flags it. A red "Zero data" badge marks a value of 0 on a component that has data on other days. A gray "never collected data" note marks a component that was 0 on every day.

## Individual Component Summary

This tab looks at one dimension by one metric in more depth, e.g., Page by Occurrences:

- a trend chart, like the ones above
- the top 100 values yesterday, with how each one moved compared with the previous 90 days ("up 3", "down 2", a dash for no change, or "* new")
- the top 10 values for each major browser group: Google, Microsoft, Apple, Mozilla and Other
- the top 10 values for each device type: Other, Mobile Phone, Tablet, Media Player and Television. Desktop traffic falls under Other.

### Several pairs

A run starts with one pair, set by `--dimension` and `--metric` on `ahd run`. Add more with `ahd individual`, e.g., `ahd individual --run 3 --dimension evar17 --metric "Cart Additions"`. It takes a few seconds and writes the report again. Then the Dimension and Metric dropdowns switch between the pairs at once.

The page cannot fetch a new pair by itself, because it works offline. The hint on the tab shows the command with the run number. Add the same `--profile` or `--db` option that you used for the run, so that the command finds the same database.

## Alerts Summary

This tab lists the alerts for one day. It starts on yesterday, and the Day picker chooses any of the last 7 days. The alerts come in these groups:

- **Unusual values.** Adobe's anomaly detection flagged the value as outside the expected range.
- **Drops of more than 30% below usual.** The heading shows the slider's percent. The value was further below the usual value than the slider allows, and Adobe did not flag it.
- **Dropped to zero.** The value was 0, but the component had data on other days in the run.
- **Never collected data.** The value was 0 on every day in the run. These are often unused slots, so they are not alerts, even when Adobe flags the 0. They are in a closed list at the end.

### Copying

**Copy alerts** puts two versions on the clipboard at once:

- a formatted table, which Outlook, Gmail and Apple Mail paste
- plain text, which a chat or a text editor pastes

The copy follows the chosen day and the slider. If the browser cannot copy, open "Show the alert summary as text" and copy the text from there.

To send alerts on a schedule, or only when something is new, see the [DuckDB guide](duckdb.md#email-recipes).

## How values are shown

- **Currency metrics** show in the report suite's currency, in the tables, the alerts and the charts. They keep the currency's own number of decimals, e.g., $1,234.56 for US dollars and ¥1,235 for yen. Without a currency code they show as plain numbers with two decimals.
- **Other metrics** show as plain numbers, with up to two decimals.
- **Usual and expected values** are rounded: whole numbers from 100 up, one decimal below that. A currency value below 100 keeps the currency's own decimals.
- **A value that could not be read** says "No data". It is never shown as 0.

For how these numbers compare with the Excel workbook, see [Compared with the Excel workbook](excel-comparison.md).
