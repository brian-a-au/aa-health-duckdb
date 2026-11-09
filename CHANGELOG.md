# Changelog

This file lists the changes in each release. The version numbers follow [Semantic Versioning](https://semver.org/). Until 1.0.0, a minor release, e.g., 0.2.0, may change the database layout or the command options. The tool then asks you to start a new database file, and this file says so.

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
