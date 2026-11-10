# How runs are stored

[README](../README.md) · [Usage](usage.md) · [Configuration](configuration.md) · [The report](report.md) · **How runs are stored** · [DuckDB guide](duckdb.md) · [Run it every day](scheduling.md) · [Troubleshooting](troubleshooting.md) · [Compared with the Excel workbook](excel-comparison.md) · [Development](development.md)

Each time you use `ahd run`, the tool saves a new run in a DuckDB file on your computer. This page explains how runs are kept, and what is inside the file.

## DuckDB runs inside ahd

DuckDB is a database that runs inside the `ahd` program itself. There is no database server to install, start or connect to. It is installed with `ahd`, so there is nothing more to install.

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

- **Run numbers.** Runs are numbered 1, 2, 3 and so on, in the order you make them. A run that `ahd import` copies into a file can get a new number, so a higher number does not always mean a newer run. The tool goes by the run date and the start time where the order counts, e.g., in `latest_runs`. The number appears in the report's file name, e.g., `ahd_my_suite_2026-11-07_run3.html`.
- **Each run is complete.** A run holds its own copy of everything its report needs, including all 92 days of data, even when those days overlap with an earlier run. Runs never share or merge data. If you run twice on the same day, you get two runs, and their numbers can differ a little if Adobe was still processing data in between.
- **A run never changes.** The tool never changes or overwrites an earlier run's data. The one thing you can add is another Individual Component Summary pair, with `ahd individual`.
- **Status.** A run starts as "running" and ends as "ok", "partial" or "failed". A run that you stop with Ctrl-C is "failed". A run that stopped in another way, e.g., because you closed the window, stays "running". `ahd individual` changes an "ok" run to "partial" when the new pair had problems, and a "failed" run to "partial" when the new pair stored data.
- **Listing and rebuilding.** `ahd runs` lists every stored run, with the highest run number first. `ahd render --run 3` rebuilds the report for run 3 from the file, with no calls to Adobe. A rebuilt report uses the current version of the page design.
- **Size.** One run of a suite with about 400 components takes about 3 MB. The file grows until you delete runs.
- **Earlier runs in the report.** Each report lists the earlier runs of the same suite and segment in the file, with their alerts. A report you rebuild with `ahd render` lists the runs in the file at that time, so a deleted run is not listed. See [The report](report.md#earlier-runs).

## Deleting runs

`ahd prune` deletes the runs you choose. See [Usage](usage.md#prune) for its options.

- It deletes each run from every table at once. If any part fails, nothing is deleted.
- The file does not get smaller afterwards, but DuckDB reuses the freed space for new runs.
- Run numbers are never reused, so a new run never gets the number of a deleted one. Only `ahd import` can bring a run with such a number back into the file.
- Reports that are already written stay in the reports folder.

To start over, delete the file. To keep a copy of the runs first, run `ahd export --out FOLDER`. See [Usage](usage.md#export-and-import).

## What is inside

Every row carries its run number, `run_id`. The tables are:

| Table | What it holds, for each run |
|---|---|
| `runs` | Report suite ID and name, segment, run date, whether `--date` set the run date, time zone, currency, the version of `ahd`, status, and start and end times |
| `components` | The events, eVars, props and solution components that were checked, with their names, groups and Adobe's metric types |
| `daily_values` | One row per component per day: the value, Adobe's expected value and range, and whether Adobe flagged it |
| `pairs` | The Individual Component Summary pairs: one dimension by one metric each. Pair 1 comes from `ahd run`, and `ahd individual` adds more |
| `top_items` | The top 100 values of each pair, for yesterday and for the 90 days before |
| `breakdowns`, `breakdown_totals` | The browser and device tables of each pair, with their totals |
| `issues` | Anything that could not be read, with the reason |
| `schema_version` | The version of this layout |
| `views_version` | The version of the views and the macro |

The file also holds two views and a table macro for your own SQL: `latest_runs`, `component_days` and `alerts()`. They follow the same rules as the report. The [DuckDB guide](duckdb.md) explains them, with recipes.

## Layout versions

When a new version of the tool changes the tables, it refuses a file made with the old tables. It never changes the tables of an old file. You start a new file and copy the old runs into it with `ahd import`:

```
mv data/ahd.duckdb data/ahd-old.duckdb
ahd import data/ahd-old.duckdb
```

On Windows, use `ren data\ahd.duckdb ahd-old.duckdb` for the first line. The old file stays as it was, so you can keep it as a backup or delete it.

- `ahd import` copies each table by column name. A column that only the new layout has stays empty for the old runs.
- It reads layout 7 and newer. Layout 7 came with version 0.1.2. A file from 0.1.0 or 0.1.1 cannot be imported.
- If a later layout cannot take the runs of an older one, the changelog for that release says so.

See [Usage](usage.md#export-and-import) for both commands, and [Troubleshooting](troubleshooting.md#the-database-was-made-by-a-different-version) for the message.

When a new version of the tool changes only the views or the `alerts()` macro, e.g., to fix an alert rule, you keep your file. The next `ahd run`, `ahd individual`, `ahd prune` or `ahd import` replaces the views and the macro, and your stored runs then follow the new rules. `ahd render` and `ahd runs` open the file read-only, so they do not replace them. If you go back to an older version of the tool, from 0.1.3 on, its next write puts back its own views and macro. 0.1.2 and earlier leave the views as they find them.

## Looking inside

Open the file with the DuckDB command line tool, version 2.0 or newer:

```
duckdb -readonly data/ahd.duckdb -c "from latest_runs"
```

`-readonly` means a query can never change data. The [DuckDB guide](duckdb.md) covers this in depth.
