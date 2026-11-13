# The report

[README](../README.md) · [Usage](usage.md) · [Configuration](configuration.md) · **The report** · [How runs are stored](runs.md) · [DuckDB guide](duckdb.md) · [Run it every day](scheduling.md) · [Troubleshooting](troubleshooting.md) · [Compared with the Excel workbook](excel-comparison.md) · [Development](development.md)

Each run writes one HTML file, e.g., `reports/ahd_my_suite_2026-11-07_run3.html`. A run with a segment has the segment ID in the name, e.g., `ahd_my_suite_s300000_5a1b_2026-11-07_run4.html`. If the folder already holds a report of another run with the same name, e.g., run 3 of another database file, the tool keeps it and adds `_2` to the new name. This page walks through everything on it.

## Contents

- [Open and share it](#open-and-share-it)
- [The top of the page](#the-top-of-the-page)
- [The large drop and spike sliders](#the-large-drop-and-spike-sliders)
- [Anomaly detection methods](#anomaly-detection-methods)
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

The header names the report suite, the Individual Component Summary pairs, the segment, the anomaly detection method, the run date in the report suite's time zone, when the run was made, how long it took, and its status:

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
- **Zero and anomaly flags come first.** A value of 0 is listed as "Dropped to zero", and a value that the anomaly method flagged is listed as an unusual value. Neither one is also a large drop or spike.
- **The rule of the anomaly method.** One item states in one sentence when the method in use flags a value. See [Anomaly detection methods](#anomaly-detection-methods).
- **Arrows follow the sliders.** The tables show a down arrow for a change past the drop slider, and an up arrow for a change past the spike slider.
- **Everything updates at once.** When you move a slider, the tiles, the tables, the "Show only flagged" filter and the alerts change right away. In a report with more than 400 components, they change when you pause, so that the slider stays smooth. The same holds for the "Find a component" box.

Two lines under the sliders say how many components are large drops and large spikes on the day the Components Summary shows. Each also names the largest drop or rise, even when it is under the slider's percent, so you can see why nothing was flagged.

## Anomaly detection methods

An unusual value is a value outside the expected range for its day. By default, the report uses the expected values, the ranges and the flags that Adobe's anomaly detection returned for the run. The option `--anomaly-method` on `ahd run`, `ahd individual` and `ahd render` chooses another method for one report:

| `--anomaly-method` | Name on the page | Where the expected range comes from |
|---|---|---|
| `adobe` | Adobe | Adobe's anomaly detection. This is the default. |
| `weekday-median` | Weekday median | ahd computes it from the median of the earlier values of the same weekday in the run. |
| `weekday-iqr` | Weekday quartile range | ahd computes it from the quartiles of the earlier values of the same weekday in the run. |
| `weekday-trend` | Weekday trend | ahd computes it from the recent level of the component, and from how the earlier values of the same weekday compared with their level. |

The three local methods are much simpler than Adobe's anomaly detection, and they will flag different days. They are useful when Adobe's anomaly detection is not available for a run, or as a second look at the same run. ahd computes them in DuckDB from the daily values that the run stored, so they make no requests to Adobe.

The option `--anomaly-sensitivity` makes the range of a local method wider or narrower. See [The sensitivity of the local methods](#the-sensitivity-of-the-local-methods).

The method and its sensitivity are fixed for each report, and the page has no switch for them. They are not stored with the run, so `ahd render` can write the report of any stored run again with another method or sensitivity. See [render](usage.md#render).

The method changes the expected values, the ranges and the flags. The values and the weekday averages stay the same. Which values are listed as large drops, large spikes or drops to zero can change, because a flagged value is listed as an unusual value instead.

### Which local method to choose

- Choose `weekday-median` or `weekday-iqr` for a component whose level stays about the same over the weeks. They compare a day with the same weekday in earlier weeks. They have a range for the last 5 weeks.
- Choose `weekday-trend` for a component whose level rises or falls at about the same speed over the weeks, e.g., one that grows a little every day. The other two methods compare such a component with the older values of its weekday, which were lower or higher. Their expected value then lags behind, and their range is wide. `weekday-trend` follows the recent level instead. It has a range only for about the last 4 weeks, and a day after a gap in the data has no range. With `normal`, it had more flags in common with Adobe on Demo Data than the other local methods, 21 of Adobe's 64. On Adobe Store, 35 of its 53 flags were also flagged by Adobe. That is 66 percent, a larger share than with the other local methods.
- Do not choose `weekday-trend` for a suite whose rise or fall can speed up or slow down. Choose `weekday-median` or `adobe` instead. Do not rely on `weekday-trend` to report data that stops, either. See [When not to choose weekday-trend](#when-not-to-choose-weekday-trend).

### When not to choose weekday-trend

`weekday-trend` gives poor results in two cases.

**A rise or fall that changes its speed.** `weekday-trend` learns how each weekday compared with the average of the 7 days before it. In a steady rise or fall, these ratios hardly vary, so the range is narrow. When the rise or fall speeds up or slows down, many days fall outside that range. The sample still holds the ratios of the earlier weeks, so the flags go on for weeks.

On Demo Data CJA, the values fell by about 20 percent a week until mid October, and by 32 to 54 percent a week after that. In the 28 days from 2026-10-15 to 2026-11-11, `weekday-trend` flagged 93 of the 94 components whose expected value is 10 or more. It flagged 1,090 values. `weekday-median` flagged 169 values of 17 components, and Adobe flagged 3 values. With `low`, `weekday-trend` still flagged 744 values.

**Data that stops.** Do not rely on `weekday-trend`, or on the range of any method, to report data that stops. A value of 0 is an alert with every method when the component had data on other days. When the method flags the 0, the value is listed as an unusual value. Otherwise, it is listed as "Dropped to zero". `weekday-trend` flags fewer of these values than `weekday-median`, for two reasons:

- The lower end of its range is often 0 already, and a value of 0 is not below 0. This happens on a suite with single-day spikes. A spike is part of the average of the 7 days before each of the next days, so the ratios of those days vary a lot, and the range is very wide.
- The level falls with the values of 0 over the next 7 days. After a week, the expected value is under 10, or the day has no level, so the day gets no flag.

On Demo Data Evangelists, the data stopped on 2026-11-04, and 345 components went to 0. From 2026-11-04 to 2026-11-11, `weekday-trend` flagged 39, 179, 316, 203, 236, 215, 18 and 8 values "Below range" on each day. `weekday-median` flagged 301, 341, 344, 341, 340, 295, 29 and 288. On 2026-11-11, 166 of the 345 components had no flag with `weekday-trend`, because their expected value was under 10 or they had no level. Adobe flagged the values of 0 only on the first two days, 236 values and then 22.

**The number of alerts did not change.** In both cases, the method changed the labels in the alert lists and the tiles, and the flags in the charts and the calendar. It did not change how many alerts a reader sees. A value of 0 is an alert with every method, as described above. A value far below its weekday average is a large drop when the method does not flag it, because the large drops come from the weekday average and not from the method. On 2026-11-11, Demo Data CJA had 99 alerts with each of the four methods. With `weekday-trend`, 55 of them were unusual values and 42 were large drops. With `adobe`, 97 were large drops. Demo Data Evangelists had 356 alerts with each method. The calendar can still look very different, because it counts flags and drops to zero but not large drops.

These counts come from the four runs of Adobe's demo report suites that are described in [Which sensitivity to choose](#which-sensitivity-to-choose), with `normal` unless the text names another sensitivity. Adobe generates the data of these suites, so a real suite may give other counts.

### Where the page names the method

- The header has the fact "Anomaly detection". It shows one of these:
  - "Adobe", for the default.
  - "Weekday median", "Weekday quartile range" or "Weekday trend", with the note "(computed by ahd from the daily values of this run)". With a sensitivity other than `normal`, the name also says it, e.g., "Weekday median, high sensitivity".
  - "Adobe" with the note "(not available for this run)", when the run could not use Adobe's anomaly detection. See [A run without Adobe's anomaly detection](#a-run-without-adobes-anomaly-detection).
- "How large drops and spikes are found" has one item that states the rule of the method in use in plain words. For a local method, it names the factor of the sensitivity in use, e.g., "more than 2 times the usual spread" with `high`. A factor of 1 is not written out, so `weekday-iqr` with `high` says "more than the width of the middle half past its edge". For a local method, it also says that a day is flagged only when the run has at least 8 earlier values of its weekday and its expected value is 10 or more. For `weekday-trend`, the 8 earlier values must have a level. The exact rules are below.
- Every text that says who flagged a value names the method in use, e.g., a tile says "Flagged by the Weekday median method" instead of "Flagged by Adobe". These texts do not name the sensitivity.

### The rules of the local methods

The local methods judge each day of a component by the earlier days of the same weekday in the run, e.g., a Monday by the Mondays before it. For `weekday-median` and `weekday-iqr`, the values of those days are the sample of the day. For `weekday-trend`, the sample holds a ratio for each of those days, as explained below. The sample only holds days from 91 days before the run date to the day before the run date. A day without a value, or with a value that is not a number, is left out of the sample.

Two terms explain the rules:

- The **median** of some values is the middle value when you sort them. With an even number of values, it is the average of the two middle values.
- The **quartiles** split the sorted values into four parts of the same size. A quarter of the values are below the lower quartile, and a quarter are above the upper quartile. When a quartile falls between two values, it lies between them in proportion. The distance between the two quartiles is the quartile range.

`weekday-median` computes the range this way:

- The expected value is the median of the sample.
- The spread is 1.4826 times the median distance of the sample values from their median.
- The range goes from 3 spreads below the expected value to 3 spreads above it. The sensitivity changes the 3. See [The sensitivity of the local methods](#the-sensitivity-of-the-local-methods).

The page calls this spread the usual spread of the values.

`weekday-iqr` computes the range this way:

- The expected value is the median of the sample.
- The range goes from 1.5 quartile ranges below the lower quartile to 1.5 quartile ranges above the upper quartile. The sensitivity changes the 1.5.

The page calls the values between the two quartiles the middle half, and the quartile range its width. The quartiles are the edges of the middle half.

`weekday-trend` computes the range this way:

- The level of a day is the average of the values of the 7 days before it. A day has a level only when all 7 days are in the run's 91 days and have a value that is a number, and the average is above 0.
- The ratio of a day is its value divided by its level. A day without a level, or without a value, has no ratio.
- The sample of a day is the ratios of the earlier days of the same weekday.
- The middle ratio is the median of the sample. The spread is 1.4826 times the median distance of the ratios from the middle ratio.
- The expected value is the level of the day times the middle ratio.
- The range goes from the level times the middle ratio minus 3 spreads, to the level times the middle ratio plus 3 spreads. The sensitivity changes the 3.

A day without a level gets no expected value, no range and no flag. The level follows the component when it grows or shrinks, and the ratios keep the pattern of the weekday, e.g., a Saturday that is usually lower than the days before it.

Then the same four checks apply to every local method, in this order:

1. A day with fewer than 8 values in its sample gets no expected value, no range and no flag. Smaller samples flag too many days.
2. When the expected value is above 0, the range reaches at least 5 percent above it and 5 percent below it. A component that hardly changes is then not flagged for a small change.
3. When no value in the sample is below 0, the lower end of the range is not below 0. For `weekday-trend`, the sample is the ratios, and the level is above 0, so this means that the lower end is not below 0 when no ratio is below 0.
4. A day is flagged only when its value is a number, its expected value is 10 or more, and its value is above the range or below it. A value that is exactly on the upper or lower end is not flagged.

A value above the range is flagged "Above range", and a value below it "Below range", as with Adobe. A component that never collected data is never an alert, with any method.

An example for `weekday-median`. A Monday has 8 earlier Mondays in the run, with the values 100, 110, 90, 100, 105, 95, 100 and 110:

- Sorted, the values are 90, 95, 100, 100, 100, 105, 110 and 110. The two middle values are both 100, so the median is 100.
- The distances from 100 are 0, 10, 10, 0, 5, 5, 0 and 10. Sorted, they are 0, 0, 0, 5, 5, 10, 10 and 10, so the median distance is 5.
- The spread is 1.4826 times 5, which is 7.413. Three spreads are 22.239.
- The range is 77.761 to 122.239. It already reaches more than 5 percent on each side, so check 2 changes nothing.
- A value of 125 on that Monday is flagged "Above range", and a value of 77 is flagged "Below range". A value of 120 or 78 is not flagged.

An example for `weekday-trend`. The 7 days before a Monday add up to 1,400, so its level is 200. The 8 earlier Mondays in the run had the ratios 1, 1.1, 0.9, 1, 1.05, 0.95, 1 and 1.1:

- Sorted, the ratios are 0.9, 0.95, 1, 1, 1, 1.05, 1.1 and 1.1, so the middle ratio is 1.
- The distances from 1 are 0, 0.1, 0.1, 0, 0.05, 0.05, 0 and 0.1, so the median distance is 0.05.
- The spread is 1.4826 times 0.05, which is 0.07413. Three spreads are 0.22239.
- The expected value is 200 times 1, which is 200. The range is 200 times 0.77761 to 200 times 1.22239, which is 155.522 to 244.478.

### The sensitivity of the local methods

The option `--anomaly-sensitivity` on `ahd run`, `ahd individual` and `ahd render` sets how wide the range of a local method is. It takes `low`, `normal` or `high`. The default is `normal`, which gives the ranges described above. The sensitivity changes the number of spreads or quartile ranges in each rule. This number is the factor of the method:

| `--anomaly-sensitivity` | `weekday-median` and `weekday-trend` | `weekday-iqr` |
|---|---|---|
| `low` | 4 spreads | 2 quartile ranges |
| `normal` | 3 spreads | 1.5 quartile ranges |
| `high` | 2 spreads | 1 quartile range |

`low` multiplies the factor of `normal` by 4/3, so the range is wider and fewer values are flagged. `high` multiplies it by 2/3, so the range is narrower and more values are flagged. The expected value stays the same. The four checks above stay the same too. A range still reaches at least 5 percent on each side of the expected value, so `high` does not flag a change of 5 percent or less.

In the example for `weekday-median` above, the spread is 7.413. With `high`, the range is 2 spreads on each side, which is 85.174 to 114.826. A value of 120 on that Monday is then flagged "Above range". With `low`, the range is 4 spreads on each side, which is 70.348 to 129.652, and a value of 125 is not flagged.

The sensitivity does not apply to `adobe`, because Adobe returns its own range. With `adobe`, `low` or `high` stops the command with an error. See [Troubleshooting](troubleshooting.md#anomaly-detection-options).

### Which sensitivity to choose

- Start with `normal`. It is the default, and it gives the same ranges as ahd 0.1.8.
- Choose `low` when a local method flags more values than you can check, e.g., on a suite whose values jump a lot from week to week. It flags fewer values, but it also misses more of the values that Adobe flags.
- Choose `high` when a missed problem costs more than a false alarm. It finds more of the values that Adobe flags, but it flags many more values that Adobe does not flag.
- Do not expect a sensitivity to make a local method agree with Adobe. On the suites below, no method at any sensitivity found more than about three quarters of Adobe's flags, and on Demo Data, the best found half of them.
- A sensitivity does not help `weekday-trend` much when the rise or fall of a component changes its speed. On Demo Data CJA, `weekday-trend` flagged 744 values even with `low`, and `weekday-median` flagged 64 with `low`. Choose another method for such a suite. See [When not to choose weekday-trend](#when-not-to-choose-weekday-trend).

The evidence for this advice is limited. The four suites below are the only suites that were available for the test, and all four are Adobe's demo report suites. Adobe generates their data, so it does not behave like the data of a real website, e.g., Demo Data CJA falls on every day of the run, and Demo Data Evangelists has no data from 2026-11-04 on. A real suite may give other counts. To check your own suite, run the query in [`anomaly_days`](duckdb.md#anomaly_daysrun-method--adobe-min_usual--10-sensitivity--normal) with `sensitivity := 'low'` and then `'high'` added to the local method.

These counts come from one run of each suite, with the run date 2026-11-12. They cover the 28 days from 2026-10-15 to 2026-11-11, which have a range with every local method. Each cell gives the number of values that the method flagged, where a value is one component on one day. The number in brackets is how many of them Adobe flagged too.

| Method | Sensitivity | Demo Data | Adobe Store | Demo Data CJA | Demo Data Evangelists |
|---|---|---:|---:|---:|---:|
| `adobe` | | 64 | 58 | 3 | 667 |
| `weekday-median` | `low` | 57 (3) | 66 (33) | 64 (2) | 3,175 (469) |
| `weekday-median` | `normal` | 281 (7) | 91 (38) | 169 (2) | 3,478 (495) |
| `weekday-median` | `high` | 1,220 (24) | 138 (43) | 303 (2) | 3,919 (513) |
| `weekday-iqr` | `low` | 107 (1) | 78 (36) | 101 (0) | 2,290 (287) |
| `weekday-iqr` | `normal` | 358 (6) | 96 (37) | 174 (0) | 2,660 (293) |
| `weekday-iqr` | `high` | 706 (22) | 126 (44) | 307 (2) | 2,848 (295) |
| `weekday-trend` | `low` | 82 (4) | 40 (28) | 744 (0) | 691 (261) |
| `weekday-trend` | `normal` | 300 (21) | 53 (35) | 1,090 (2) | 1,620 (283) |
| `weekday-trend` | `high` | 1,106 (32) | 92 (43) | 1,447 (2) | 2,718 (463) |

The suites have 410, 35, 124 and 371 components. On Demo Data Evangelists, most flags of the local methods are values of 0 after the data stopped. Adobe flagged almost all of its values of 0 on the first two days, 2026-11-04 and 2026-11-05.

### The first eight weeks have no range

The charts show 91 days, which is 13 weeks. A local method needs 8 earlier days of the same weekday in the run. The days of the first 8 weeks do not have that many, so they get no expected range and no flags. The charts draw the range only for the last 5 weeks. In the same way, a square in the first 8 weeks of the calendar only counts drops to zero. With a local method, the calendar shows flags only for the last 5 weeks.

`weekday-trend` needs one week more. The days of the first week have no level, because the 7 days before them are not all in the run. Its first range is in week 10, so it has a range only for the last 4 weeks.

A day that has no value in the run does not count as an earlier day. A component with missing days therefore gets its first range later. With `weekday-trend`, a day without a value also leaves the 7 days after it without a level. Those days get no range, and they do not count as earlier days of their weekday.

When no day of a chart has a range, e.g., because the component has too few values, the chart says so under it.

### A run without Adobe's anomaly detection

When the credential may not use Adobe's anomaly detection, or the test of it failed, the run stores no expected values and no flags from Adobe. With `adobe`, the tiles then say "Anomaly detection not available", and the Alerts tab suggests a command that writes the report again with a local method, e.g., `ahd render --run 3 --anomaly-method weekday-median`. With a local method, the report has expected ranges and flags as usual, and the status section says that the report does not need Adobe's anomaly detection. See [Troubleshooting](troubleshooting.md#limited-access).

## Summary tiles

The tiles count yesterday's results:

- components checked
- values above and below the expected range
- large drops
- large spikes
- components that dropped to zero, and how many others never collected data
- problems in the run

An alert tile that counts at least one alert also says how many of them are new, which means that they started yesterday. See [New and ongoing alerts](#new-and-ongoing-alerts).

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
- **Show only flagged:** hides everything that is not an anomaly flag, a large drop, a large spike or a drop to zero.

### Alerts by day

A calendar shows the last 13 weeks. Each square is one day, and a darker square had more components with an alert:

- The anomaly method flagged the value above or below its expected range.
- The value dropped to 0, and the component had data on other days.

Large drops and spikes are not counted, because only the last 7 days have a weekday average. A day with no data at all is an outline.

A release that breaks tracking usually breaks many components on the same day, so it shows as one dark square. Choose a square, or move with the arrow keys, to list that day's components. The left and right keys move a week, and the up and down keys move a day. Choose a component in the list to see it on the metric or dimension chart. The text under the chart is then about that day.

"Days with the most alerts" lists the 5 days with the most alerts, with the count of each kind.

The calendar follows the Day picker. In the weekly view it outlines the week's 7 days, and the text beside it sums up the week, e.g., "Nov 2 to Nov 8, 2026: 6 components had an alert on at least one day." Its lists then hold every component with an alert on any of the 7 days. Choose a square to see one day. DuckDB finds the alerts for every day and component in one query. In a test with 410 components and 92 days, the query took about 10 milliseconds.

### Charts

Two charts show 91 days of one metric and one dimension. Each shows the actual values, the expected value and expected range of the anomaly method, and markers where it flagged a value. With a local method, the first 8 weeks have no range, or the first 9 weeks with `weekday-trend`. See [The first eight weeks have no range](#the-first-eight-weeks-have-no-range). Use the arrow keys on a chart to read each day.

The charts follow the Day picker:

- A thin vertical line and a dot mark the chosen day.
- A short bar marks the weekday average for that day. The weekday average is the average of the same weekday in the 12 weeks before the day.
- The line under the chart describes the chosen day until you move over the chart.

In the weekly view, a light band covers the week's 7 days, and the line under the chart gives the week's total and the average week, e.g., "Nov 2 to Nov 8, 2026: 9,853 in total, average week 10,465." The key then shows "Chosen week".

The key under each chart has two rows. The top row holds the lines, the band and the chosen day with its weekday average, or the chosen week. The second row holds the anomaly flags.

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

The flag is "Above range" or "Below range" from the anomaly method, or "Large drop" or "Large spike" when a slider flags it. A red "Zero data" badge marks a value of 0 on a component that has data on other days. A gray "never collected data" note marks a component that was 0 on every day. In the weekly view, the anomaly flags are counted by day, e.g., "Below range on 2 days", and "Show only flagged" keeps those components.

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

- **Unusual values.** The anomaly method flagged the value as outside the expected range. By default, that is Adobe's anomaly detection.
- **Drops of more than 30% below the weekday average.** The heading shows the drop slider's percent. The value was further below its weekday average than the slider allows, and the anomaly method did not flag it.
- **Spikes of more than 50% above the weekday average.** The heading shows the spike slider's percent. The value was further above its weekday average than the slider allows, and the anomaly method did not flag it.
- **Dropped to zero.** The value was 0, but the component had data on other days in the run.
- **Never collected data.** The value was 0 on every day in the run. These are often unused slots, so they are not alerts, even when the anomaly method flags the 0. They are in a closed list at the end.

### New and ongoing alerts

Each alert list has a "Since" column. It says on which day the alert started and for how many days it has held, up to the chosen day, e.g., "Nov 4, 3 days". An alert that started on the chosen day says "New". In each list, the new alerts come first. The heading of each list counts them, e.g., "3 components, 1 new", and so does each alert tile at the top, e.g., "1 new".

An alert follows one condition, and its streak is the unbroken run of days on which that condition held:

- For a value of 0, the value was 0 on each day. This holds for a 0 that the anomaly method flagged, too, so the alert keeps its start when its label changes, e.g., from an unusual value to "Dropped to zero".
- For an unusual value, the anomaly method flagged the value in the same direction on each day, e.g., above the expected range.
- For a large drop, the value was further below its weekday average than the slider allows on each day. A value of 0 counts too. The streak changes when you move the slider.
- For a large spike, the value was further above its weekday average than the spike slider allows on each day.

Sometimes the day before the streak cannot be judged, so the alert may have started earlier. The column then says "or earlier" and "at least", e.g., "Nov 5 or earlier, at least 7 days". This happens in these cases:

- The day before is not in the run's 91 days, or it has no value.
- For an unusual value, the anomaly method gave that day no expected range. Adobe's anomaly detection usually covers every day of the run. A local method has a range only for about the last 5 weeks. See [The first eight weeks have no range](#the-first-eight-weeks-have-no-range).
- For a large drop or spike, the day is older than the last 7 days. Only the last 7 days have a weekday average, so a drop or spike can be judged only on them. A drop that holds on all 7 days therefore says "at least 7 days".

A value of 0 can be judged on every day of the run, so a drop to zero has a known start even when it is older than 7 days. On Demo Data Evangelists, the data stopped during 2026-11-04. On that day most values were small but not 0, and Adobe flagged 236 of them. From 2026-11-05 on, the values were 0. On 2026-11-11, 330 of the 356 drops to zero said "Nov 5, 7 days", with every anomaly method. 19 said "Nov 4, 8 days", because they were already 0 on 2026-11-04. On Demo Data CJA, the values fall every day. 97 of its 99 alerts were large drops that said "Nov 5 or earlier, at least 7 days", and the other 2 were new drops to zero.

The `alerts()` macro gives the same answer in its columns `since`, `days`, `at_least` and `is_new`. See the [DuckDB guide](duckdb.md#alertsrun-threshold--030-spike_threshold--050-min_usual--10-for_day--null-method--adobe-sensitivity--normal).

### Suite-wide events

When the data of a whole suite stops, or its traffic jumps, most components get the same kind of alert on the same day. The lists then hold hundreds of rows that all say the same thing. So the tab starts with one line for such a group, a suite-wide event, and folds its rows under that line.

An event is a group of alerts that follow the same condition, the one that the Since column follows: a value of 0, a value below the expected range, a value above it, a large drop or a large spike. It needs at least 10 alerts, and they must be at least half of the components that collected data in the run.

- **Half of the components.** When most of a suite moves the same way on the same day, the cause is likely in the suite, e.g., its tracking or its traffic, and not in each component. With half as the limit, a day has at most two events.
- **Components that collected data.** A component that was 0 on every day of the run cannot alert. Many suites have a lot of unused slots, e.g., 20 of the 35 components of Adobe Store, so counting them would hide an event in such a suite.
- **At least 10 alerts.** A list of fewer than 10 rows is short enough to read row by row. In a very small suite, half can also be 2 or 3 components, which says little about the whole suite.

The start of an event is not part of the rule, because one stop can start on two days for different components. The line names the start that most alerts of the event share, e.g., "For 330 of them, this started on Nov 5. For the other 26, it started on other days." When two starts are shared by as many alerts, it names the earlier one.

The line is at the top of the tab, under "Suite-wide events". "Show the 356 alerts" opens the rows of the event, with the columns of the list that they belong to. The lists below leave these rows out, and a note says how many of their alerts are in an event. The heading of each list and the tiles at the top still count every alert. The copied alerts start with the line of each event and its first 5 alerts. Alerts outside an event are listed as before.

The events follow the chosen day, the anomaly method and the sliders. Moving the drop slider can make a group of large drops big enough for an event, or too small. Examples from Adobe's demo suites on 2026-11-11, with Adobe's anomaly detection:

- Demo Data Evangelists: "356 of the 356 components that collected data had a value of 0 on Wed, Nov 11 (yesterday). For 330 of them, this started on Nov 5. For the other 26, it started on other days." No alert is outside the event. Nov 4, the day the data stopped, is older than the 7 days of the report. There, `suite_events()` with `for_day` finds 234 values below the expected range, an event of its own.
- Demo Data CJA: "97 of the 99 components that collected data had a value more than 30% below the weekday average on Wed, Nov 11 (yesterday). For all of them, this started on Nov 5 or earlier." The 2 drops to zero stay in their list.
- Adobe Store: 10 of the 15 components that collected data had a value more than 50% above the weekday average, e.g., Visits rose 114 percent. The 3 unusual values stay in their list.
- Demo Data has 2 alerts, so it has no event.

The [`suite_events()` macro](duckdb.md#suite_eventsrun-) gives the same events for your own SQL, and the `event` column of `alerts()` says which event each alert belongs to. `ahd alerts` and the alert email give each event one line, too. See [Usage](usage.md#suite-wide-events-in-the-output).

### Copying

**Copy alerts** puts two versions on the clipboard at once:

- a formatted table, which Outlook, Gmail and Apple Mail paste
- plain text, which a chat or a text editor pastes

The copy follows the chosen day and the sliders. It says since when each alert has held, and lists the new alerts first in each group. A suite-wide event comes first, with its line and its first 5 alerts, and a line that counts the others. The groups after it leave out the alerts of the event. If the browser cannot copy, open "Show the alert summary as text" and copy the text from there.

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
- values above and below the expected range
- large drops and large spikes, at the sliders' percents
- drops to zero
- all alerts, with a bar to compare the runs
- problems in the run

DuckDB counts every run in one query over the stored data, by the same rules as the tiles. Every run is counted by this version's rules and with the anomaly method of this report, even a run that an older version of ahd made. Runs for later dates are not listed, and a run for the same date is listed only if it was made before this one. A failed or unfinished run is left out.

If you run ahd every day, the table shows when a problem started and whether it is getting better.

## How values are shown

- **Currency metrics** show in the report suite's currency, in the tables, the alerts and the charts. They keep the currency's own number of decimals, e.g., $1,234.56 for US dollars and ¥1,235 for yen. Without a currency code they show as plain numbers with two decimals.
- **Other metrics** show as plain numbers, with up to two decimals.
- **Weekday averages and expected values** are rounded: whole numbers from 100 up, one decimal below that. A currency value below 100 keeps the currency's own decimals.
- **A value that could not be read** says "No data". It is never shown as 0.

For how these numbers compare with the Excel workbook, see [Compared with the Excel workbook](excel-comparison.md).
