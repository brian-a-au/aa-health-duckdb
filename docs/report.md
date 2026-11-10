# The report

[README](../README.md) · [Usage](usage.md) · [Configuration](configuration.md) · **The report** · [How runs are stored](runs.md) · [DuckDB guide](duckdb.md) · [Run it every day](scheduling.md) · [Troubleshooting](troubleshooting.md) · [Compared with the Excel workbook](excel-comparison.md) · [Development](development.md)

Each run writes one HTML file, e.g., `reports/ahd_my_suite_2026-11-07_run3.html`. A run with a segment has the segment ID in the name, e.g., `ahd_my_suite_s300000_5a1b_2026-11-07_run4.html`. If the folder already holds a report of another run with the same name, e.g., run 3 of another database file, the tool keeps it and adds `_2` to the new name. This page walks through everything on it.

## Contents

- [Open and share it](#open-and-share-it)
- [The top of the page](#the-top-of-the-page)
- [The large drop and spike sliders](#the-large-drop-and-spike-sliders)
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

You can fold some sections to make the page shorter. Click the heading of the section, or the arrow next to it. These sections fold:

- on Components Summary, "Alerts by day" and the charts
- on Individual Component Summary, the top 100 values and the browser and device tables
- on Alerts Summary, "Slow declines" and "Earlier runs"

Every section is open when the page opens. The page does not remember which sections you folded. The component tables and the alert lists do not fold. Choosing a component in the calendar or a table opens the charts again. When you print the page, every folded section opens for the print and folds again after it.

## The top of the page

The header names the report suite, the Individual Component Summary pairs, the segment, the run date in the report suite's time zone, when the run was made, how long it took, and its status:

- **Complete:** everything was read.
- **Complete, with some gaps:** the run finished, but some data could not be read. The status section lists it.
- **Failed:** no data could be read.
- **Not finished:** the run stopped in the middle, e.g., because the window was closed.

Every day in the report counts back from the run date. "Yesterday" is the day before the run date. For a normal run, that is the day before the run was made.

A run made with `ahd run --date` is about an earlier day. A line under the header then says "Run date set with --date", and names the last day of the report, e.g., 2026-09-15. The page uses that date instead of the word "yesterday", and the Day pickers mark it "(day before the run date)".

## The large drop and spike sliders

Two sliders above the tiles set when a change from the weekday average is an alert.

The drop slider sets how far a value must fall below its weekday average to count as a large drop. It shows the drop as a negative change, from -100% on the left to -10% on the right, in steps of 5. Move it left to flag only deeper drops. It starts at -30%, or at the value you chose with `--change-alert`, e.g., `--change-alert 45` starts it at -45%. The part of the line left of the handle is the range that gets flagged. Its orange is stronger further from zero, and the handle takes the color of the line under it.

The spike slider sets how far a value must rise above its weekday average to count as a large spike. It runs from +10% on the left to +200% on the right, in steps of 10. Move it right to flag only bigger spikes. It starts at +50%, or at the value you chose with `--spike-alert`. The part of the line right of the handle gets flagged, and its orange is strongest at the right end. A value that doubled, e.g., because a tag fires twice, is a change of about +100%.

Under the sliders, one line says what the weekday average is. Open "How large drops and spikes are found" to read the full rule:

- **The weekday average** is the average of the same weekday in the 12 weeks before the day, e.g., the 12 Mondays before a Monday. It is not Adobe's expected value. Adobe's anomaly detection computes the expected value with its own model.
- **Small numbers are skipped.** Components with a weekday average under 10 are never large drops or spikes, because small numbers change a lot from day to day. In the weekly view, the same holds for a component whose average week is under 70.
- **Zero and Adobe flags come first.** A value of 0 is listed as "Dropped to zero", and a value that Adobe flagged is listed as an unusual value. Neither one is also a large drop or spike.
- **Arrows follow the sliders.** The tables show a down arrow for a change past the drop slider, and an up arrow for a change past the spike slider.
- **Everything updates at once.** When you move a slider, the tiles, the tables, the "Show only flagged" filter and the alerts change right away.

Two lines under the sliders say how many components are large drops and large spikes on the day the Components Summary shows. Each also names the largest drop or rise, even when it is under the slider's percent, so you can see why nothing was flagged.

## Summary tiles

The tiles count yesterday's results:

- components checked
- values above and below Adobe's expected range
- large drops
- large spikes
- components that dropped to zero, and how many others never collected data
- problems in the run

Click any alert tile to open the Alerts Summary. Click the problems tile to jump to the status section.

## Status

When everything was read, the status section is one line that says "No problems". Otherwise it lists anything that could not be read, grouped by cause, with a plain explanation of each. **Copy problems** copies the list for an email or a ticket. See [Copying](#copying).

It also shows a warning when the run sees far fewer custom events, eVars or props than the run before it. See [Troubleshooting](troubleshooting.md#fewer-components-than-last-time).

## Components Summary

This tab shows every custom event, eVar and prop, then the solution components, e.g., reserved purchase events and Activity Map instances.

### Controls

- **Show:** daily or weekly. Weekly shows the 7 days ending yesterday, against the average week of the 12 weeks before. In the weekly view a Week box takes the Day picker's place and names the week, e.g., "Nov 2 to Nov 8, 2026". The report has 13 weeks of data, so only the last week has a full 12-week average, and the Week box cannot be changed.
- **Day:** any of the last 7 days. Yesterday comes first.
- **Custom components:** shows one custom type or one range of its numbers, e.g., "All custom eVars" or "eVar101 to eVar200". The list holds only the types and ranges that have components, with how many each has. A type that fits in one range of 100 has no range options. While a type or range is chosen, the solution components are hidden, and a note above the tables says what is shown.
- **Show only flagged:** hides everything that is not an Adobe flag, a large drop, a large spike or a drop to zero.

### Alerts by day

A calendar shows the last 13 weeks. Each square is one day, and a darker square had more components with an alert:

- Adobe flagged the value above or below its expected range.
- The value dropped to 0, and the component had data on other days.

Large drops and spikes are not counted, because only the last 7 days have a weekday average. A day with no data at all is an outline.

A release that breaks tracking usually breaks many components on the same day, so it shows as one dark square. Choose a square, or move with the arrow keys, to list that day's components. The left and right keys move a week, and the up and down keys move a day. Choose a component in the list to see it on the metric or dimension chart. The text under the chart is then about that day.

"Days with the most alerts" lists the 5 days with the most alerts, with the count of each kind.

The calendar follows the Day picker. In the weekly view it outlines the week's 7 days, and the text beside it sums up the week, e.g., "Nov 2 to Nov 8, 2026: 6 components had an alert on at least one day." Its lists then hold every component with an alert on any of the 7 days. Choose a square to see one day. DuckDB finds the alerts for every day and component in one query. In a test with 410 components and 92 days, the query took about 10 milliseconds.

### Charts

Two charts show 91 days of one metric and one dimension. Each shows the actual values, Adobe's expected value and expected range, and markers where Adobe flagged a value. Use the arrow keys on a chart to read each day.

The charts follow the Day picker:

- A thin vertical line and a dot mark the chosen day.
- A short bar marks the weekday average for that day. The weekday average is the average of the same weekday in the 12 weeks before the day.
- The line under the chart describes the chosen day until you move over the chart.

In the weekly view, a light band covers the week's 7 days, and the line under the chart gives the week's total and the average week, e.g., "Nov 2 to Nov 8, 2026: 9,853 in total, average week 10,465." The key then shows "Chosen week".

The key under each chart has two rows. The top row holds the lines, the band and the chosen day with its weekday average, or the chosen week. The second row holds Adobe's flags.

- **The Metric chart** starts on Occurrences. Its Type filter lists the metric groups that have data, e.g., custom events or reserved purchase events.
- **The Dimension chart** starts on Page. Its Type filter chooses eVars, props or other dimensions.

### Tables

Each row shows:

- the component number and name
- the total for the day or the week
- the weekday average, or the average week in the weekly view
- the change from that average, with an arrow when it passes a slider's percent, and a small bar
- a flag

The bar goes left of a gray center line for a drop and right of it for a rise. Half the bar is a change of 100 percent. A tick on the left marks the drop slider and a tick on the right marks the spike slider, with a light band between them. A bar that passes a tick is orange.

A small line in each row shows the component's last 13 weeks, on a scale from 0 to its largest value, with a dot on the chosen day. In the weekly view the week's 7 days are drawn in blue instead. A drop to zero or a break in tracking shows as a dip at the right end. Choose the line to see the component on the metric or dimension chart above.

The "Find a component" box above the tables keeps only the components whose name, number or ID contains the text, e.g., "evar5". It works with the Custom components filter and with "Show only flagged".

The table headings stay in view while you scroll. A table that is too wide for the window, e.g., on a phone, scrolls in its own box, and its headings stay at the top of that box. The links under "Jump to" above the tables go to each group, and show how many components each group has. After each table, the link "Back to the top of the tables" goes back to these links.

The flag is Adobe's "Above range" or "Below range", or "Large drop" or "Large spike" when a slider flags it. A red "Zero data" badge marks a value of 0 on a component that has data on other days. A gray "never collected data" note marks a component that was 0 on every day. In the weekly view, Adobe's flags are counted by day, e.g., "Below range on 2 days", and "Show only flagged" keeps those components.

## Individual Component Summary

This tab looks at one dimension by one metric in more depth, e.g., Page by Occurrences:

- a trend chart, like the ones above. It marks the day chosen with the Day picker on the Components Summary tab.
- the top 100 values yesterday, with how each one moved compared with the previous 90 days ("up 3", "down 2", a dash for no change, or "* new")
- the top 10 values for each major browser group: Google, Microsoft, Apple, Mozilla and Other
- the top 10 values for each device type: Other, Mobile Phone, Tablet, Media Player and Television. Desktop traffic falls under Other.

### Several pairs

A run starts with one pair, set by `--dimension` and `--metric` on `ahd run`. Add more with `ahd individual`, e.g., `ahd individual --run 3 --dimension evar17 --metric "Cart Additions"`. It takes a few seconds and writes the report again. Then the Dimension and Metric dropdowns switch between the pairs at once.

The page cannot fetch a new pair by itself, because it works offline. The hint on the tab shows the command with the run number. Add the same `--profile` or `--db` option that you used for the run, so that the command finds the same database.

## Alerts Summary

This tab lists the alerts for one day. It starts on yesterday, and the Day picker chooses any of the last 7 days. The alerts come in these groups:

- **Unusual values.** Adobe's anomaly detection flagged the value as outside the expected range.
- **Drops of more than 30% below the weekday average.** The heading shows the drop slider's percent. The value was further below its weekday average than the slider allows, and Adobe did not flag it.
- **Spikes of more than 50% above the weekday average.** The heading shows the spike slider's percent. The value was further above its weekday average than the slider allows, and Adobe did not flag it.
- **Dropped to zero.** The value was 0, but the component had data on other days in the run.
- **Never collected data.** The value was 0 on every day in the run. These are often unused slots, so they are not alerts, even when Adobe flags the 0. They are in a closed list at the end.

Each alert list has a "Days in a row" column. It counts the days, up to the chosen day, that had the same alert:

- For an unusual value, Adobe flagged the value in the same direction on each day, e.g., above the expected range.
- For a drop, the value was further below its weekday average than the slider allows on each day. The count changes when you move the slider.
- For a spike, the value was further above its weekday average than the spike slider allows on each day.
- For a drop to zero, the value was 0 on each day.

The report covers 7 days. When the count reaches the first of them, the column says "or more", e.g., "7 or more", because the alert may have started earlier.

### Copying

**Copy alerts** puts two versions on the clipboard at once:

- a formatted table, which Outlook, Gmail and Apple Mail paste
- plain text, which a chat or a text editor pastes

The copy follows the chosen day and the sliders. It includes the days in a row for each alert. If the browser cannot copy, open "Show the alert summary as text" and copy the text from there.

To send alerts on a schedule, or only when something is new, see the [DuckDB guide](duckdb.md#email-recipes).

### Slow declines

This table lists components that fell a little each week for the last 12 weeks. No single day falls far enough below its weekday average for an alert, so the alert lists miss a fall like this until it is large. The table is not an alert list. It does not count in the tiles, the copied alerts or the `alerts` macro.

DuckDB adds up each component's values by week. Each week is 7 days and ends on the weekday of the last day checked, so each week has every weekday once. The `regr_slope()` function then fits a straight line through the weekly totals and gives the change per week. A component is listed when:

- its weekly totals fell by 3 percent or more a week
- the weeks are close to a straight line, with a "Steady" score of 0.7 or more
- it has a value on every day of the 12 weeks
- it averaged at least 10 a day in the first week

Steady is 1 when the weekly totals fall in a straight line, and 0 when they jump around. Each row has a small bar for each week, oldest first, and the change over the 12 weeks, e.g., -29.4% from the first week to the last.

### Earlier runs

At the end of the tab, a table lists the runs of the same report suite and segment in the database, newest run date first, up to 30 runs. Each row counts the alerts on the last day of that run:

- the components checked
- values above and below Adobe's expected range
- large drops and large spikes, at the sliders' percents
- drops to zero
- all alerts, with a bar to compare the runs
- problems in the run

DuckDB counts every run in one query over the stored data, by the same rules as the tiles. Every run is counted by this version's rules, even a run that an older version of ahd made. Runs for later dates are not listed, and a run for the same date is listed only if it was made before this one. A failed or unfinished run is left out.

If you run ahd every day, the table shows when a problem started and whether it is getting better.

## How values are shown

- **Currency metrics** show in the report suite's currency, in the tables, the alerts and the charts. They keep the currency's own number of decimals, e.g., $1,234.56 for US dollars and ¥1,235 for yen. Without a currency code they show as plain numbers with two decimals.
- **Other metrics** show as plain numbers, with up to two decimals.
- **Weekday averages and expected values** are rounded: whole numbers from 100 up, one decimal below that. A currency value below 100 keeps the currency's own decimals.
- **A value that could not be read** says "No data". It is never shown as 0.

For how these numbers compare with the Excel workbook, see [Compared with the Excel workbook](excel-comparison.md).
