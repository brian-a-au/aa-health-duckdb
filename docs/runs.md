# How runs are stored

[README](../README.md) · [Usage](usage.md) · [Configuration](configuration.md) · [The report](report.md) · **How runs are stored** · [DuckDB guide](duckdb.md) · [Troubleshooting](troubleshooting.md)

Each time you use `ahd run`, the tool saves a new run in a DuckDB file on your computer. This page explains how runs are kept, and what is inside the file.

## DuckDB runs inside ahd

DuckDB is a database that runs inside the `ahd` program itself. There is no database server to install, start or connect to. `uv sync` installs it with the other Python packages.

- **One local file.** The whole database is one file on your disk. Each run adds to it, and older runs stay in it.
- **Nothing is uploaded.** The tool never sends the file anywhere.
- **No credentials inside.** The file holds report data, e.g., component names and daily values. It never holds your credential. Treat it with the same care as the reports.

## Where the file is

| You ran `ahd` with | The file is |
|---|---|
| no profile | `./data/ahd.duckdb` |
| `--profile prod` or `AA_PROFILE=prod` | `~/.aa/orgs/prod/ahd.duckdb` |
| `--db some/file.duckdb` | that file |

With profiles, data from different organizations never shares a file. Runs for different report suites of one organization go into the same file. To keep them apart, give each suite its own file with `--db`.

## How runs work

- **Run numbers.** Runs are numbered 1, 2, 3 and so on, in the order you make them. The number appears in the report's file name, e.g., `ahd_my_suite_2026-11-07_run3.html`.
- **Each run is complete.** A run holds its own copy of everything its report needs, including all 92 days of data, even when those days overlap with an earlier run. Runs never share or merge data. If you run twice on the same day, you get two runs, and their numbers can differ a little if Adobe was still processing data in between.
- **A run never changes.** The tool never changes or overwrites an earlier run's data. The one thing you can add is another Individual Component Summary pair, with `ahd individual`.
- **Status.** A run starts as "running" and ends as "ok", "partial" or "failed". A run that you stop with Ctrl-C is "failed". A run that stopped in another way, e.g., because you closed the window, stays "running". `ahd individual` changes an "ok" run to "partial" when the new pair had problems, and a "failed" run to "partial" when the new pair stored data.
- **Listing and rebuilding.** `ahd runs` lists every stored run, newest first. `ahd render --run 3` rebuilds the report for run 3 from the file, with no calls to Adobe. A rebuilt report uses the current version of the page design.
- **Size.** One run of a suite with about 400 components takes about 3 MB. The file grows until you delete runs.

## Deleting runs

`ahd prune` deletes the runs you choose. See [Usage](usage.md#prune) for its options.

- It deletes each run from every table at once. If any part fails, nothing is deleted.
- The file does not get smaller afterwards, but DuckDB reuses the freed space for new runs.
- Run numbers are never reused, so a new run never gets the number of a deleted one.
- Reports that are already written stay in the reports folder.

To start over, delete the file.

## What is inside

Every row carries its run number, `run_id`. The tables are:

| Table | What it holds, for each run |
|---|---|
| `runs` | Report suite, segment, run date, time zone, currency, status, and start and end times |
| `components` | The events, eVars, props and solution components that were checked, with their names, groups and Adobe's metric types |
| `daily_values` | One row per component per day: the value, Adobe's expected value and range, and whether Adobe flagged it |
| `pairs` | The Individual Component Summary pairs: one dimension by one metric each. Pair 1 comes from `ahd run`, and `ahd individual` adds more |
| `top_items` | The top 100 values of each pair, for yesterday and for the 90 days before |
| `breakdowns`, `breakdown_totals` | The browser and device tables of each pair, with their totals |
| `issues` | Anything that could not be read, with the reason |
| `schema_version` | The version of this layout |

The file also holds two views and a table macro for your own SQL: `latest_runs`, `component_days` and `alerts()`. They follow the same rules as the report. The [DuckDB guide](duckdb.md) explains them, with recipes.

## Layout versions

When a new version of the tool changes the layout, it refuses a file made with the old layout and asks you to start a new file. It does not upgrade old files. See [Troubleshooting](troubleshooting.md#the-database-was-made-by-a-different-version).

## Looking inside

Open the file with the DuckDB command line tool, version 2.0 or newer:

```
duckdb -readonly data/ahd.duckdb -c "from latest_runs"
```

`-readonly` means a query can never change data. The [DuckDB guide](duckdb.md) covers this in depth.
