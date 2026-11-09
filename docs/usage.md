# Usage

[README](../README.md) · **Usage** · [Configuration](configuration.md) · [The report](report.md) · [How runs are stored](runs.md) · [DuckDB guide](duckdb.md) · [Run it every day](scheduling.md) · [Troubleshooting](troubleshooting.md)

This page describes every `ahd` command, with its options and exit codes. Run every command with `uv run ahd` from the project folder, or add `--help` to any command to see its options.

## Contents

- [A typical session](#a-typical-session)
- [Options that every command shares](#options-that-every-command-shares)
- [check](#check): test the sign-in and access
- [suites and segments](#suites-and-segments): list what the credential can see
- [run](#run): fetch data and write the report
- [individual](#individual): add a dimension by metric pair to a stored run
- [render](#render): rebuild a report with no API calls
- [runs](#runs): list stored runs
- [prune](#prune): delete stored runs
- [Exit codes](#exit-codes)
- [Defaults](#defaults)

## A typical session

```
uv run ahd check                          # does the sign-in work?
uv run ahd suites                         # which report suites can it see?
uv run ahd check --rsid "My Suite"        # does it have full access to this suite?
uv run ahd run --rsid "My Suite"          # fetch, store and write the report
uv run ahd individual --run 1 --dimension evar17 --metric "Cart Additions"
uv run ahd runs                           # list what is stored
```

## Options that every command shares

| Option | What it does |
|---|---|
| `--profile NAME` | Uses the credential in `~/.aa/orgs/NAME/config.json`, and the database in the same folder. You can put it before or after the command. `AA_PROFILE` sets it too. See [Configuration](configuration.md). |
| `--config PATH` | Uses this credential file instead of `./config.json`. It cannot be used with a profile. For the commands that sign in. |
| `--company ID_OR_NAME` | Chooses the login company, by global company ID or by name, when the credential sees more than one. Without it, the tool uses the first one and prints its name. For the commands that sign in. |
| `--db PATH` | Uses this database file. For the commands that read or write a database. |
| `--verbose` | Writes debug detail to stderr. Put it before the command, e.g., `ahd --verbose run ...`. |

The commands that sign in are `check`, `suites`, `segments`, `run` and `individual`. The others never call Adobe. `runs` and `render` only read the database. `prune` deletes runs from it. Only `run` creates a new database. The other commands stop when the file does not exist.

Where a command takes a report suite, segment, dimension or metric, it accepts an ID or a name, in any case. If two items have the same name, the tool lists their IDs and asks you to use one.

## check

Tests the sign-in, and what the credential can see.

```
uv run ahd check
uv run ahd check --rsid "My Suite"
```

Without `--rsid`, it prints the login company and how many report suites the credential can see. With `--rsid`, it also counts the custom events, eVars and props and tests anomaly detection. When something is missing, it explains the fix in plain words. Run it before your first full run, and after any change in the Adobe Admin Console.

## suites and segments

List what the credential can see, with the ID and the name of each item.

```
uv run ahd suites
uv run ahd segments --rsid "My Suite"
```

`segments` lists the segments you can use with that report suite, shared ones included.

## run

Fetches 92 days of data, stores it as a new run, and writes the HTML report.

```
uv run ahd run --rsid "My Suite"
uv run ahd run --rsid my_suite --segment "Mobile visits"
uv run ahd run --rsid my_suite --dimension "Internal Search Term" --metric "Cart Additions"
uv run ahd run --rsid my_suite --date 2026-11-07 --out ./reports/
uv run ahd run --rsid my_suite --rsid "Other Suite"
uv run ahd run --all-suites
```

| Option | What it does |
|---|---|
| `--rsid` | The report suite, by ID or name. Give it more than once to check several suites. You need `--rsid` or `--all-suites`. |
| `--all-suites` | Checks every report suite the credential can see. |
| `--segment` | A segment, by ID or name. Every request is filtered by it. |
| `--dimension`, `--metric` | The first Individual Component Summary pair. Each takes a full ID (`variables/evar17`), a short ID (`evar17`) or a name (`"Internal Search Term"`). Any dimension or metric of the suite works. The default is Page by Occurrences. |
| `--date` | Treats this date as today, e.g., to repeat an earlier day. By default, today is today in the report suite's time zone. |
| `--change-alert` | Where the report's large drop slider starts: 10 to 100, in steps of 5. The default is 30. See [The report](report.md#the-large-drop-slider). |
| `--out` | The folder for the HTML report. The default is `./reports/`. |
| `--batch-size` | How many columns go into one trend request, from 1 to 100. The default is 20. |
| `--workers` | How many requests run at the same time, from 1 to 16. The default is 8. The tool starts no more than 12 requests in any 6 seconds, which is Adobe's limit. |

While it runs, it prints its progress on stderr, e.g., "Daily trends: 12 of 21 requests done". When it ends, it prints the path of the report, the run's status and the number of alerts for yesterday, e.g.:

```
reports/ahd_my_suite_2026-11-08_run12.html
Run 12 for my_suite finished with status: ok
Alerts for 2026-11-07: 2 unusual values, 1 large drop, 0 drops to zero
```

The alerts line uses the `--change-alert` percent for large drops. It is left out after a failed run. If the run sees far fewer components than the run before it, it prints a warning.

With several suites, the command signs in once and checks the suites one after another. Each suite gets its own run and its own report. A suite that fails does not stop the others. At the end, the command prints one line with the number of suites that ended "ok", "partial" and "failed". The exit code is the one of the worst suite, e.g., 1 when one suite failed. A wrong suite name stops the command before it checks any suite. To run it every day, see [Run it every day](scheduling.md). See [Troubleshooting](troubleshooting.md#fewer-components-than-last-time).

A run of a suite with about 400 components takes about 2 minutes.

## individual

Adds a dimension by metric pair to a stored run, then writes the report again. It makes 5 requests and takes a few seconds, not a full run. It uses the run's report suite, segment and dates.

```
uv run ahd individual --run 3 --dimension evar17 --metric revenue
uv run ahd individual --run 3 --dimension "Internal Search Term" --metric "Cart Additions"
```

`--dimension` and `--metric` work as in `run`. The report's Dimension and Metric dropdowns then switch between all the pairs of the run. A run cannot hold the same pair twice. `--out` and `--change-alert` work as in `run`.

## render

Rebuilds the report of a stored run from the database, with no calls to Adobe. A rebuilt report uses the current version of the page design.

```
uv run ahd render --run 3
uv run ahd render --run 3 --change-alert 50 --out ./shared/
```

## runs

Lists every stored run, newest first, with its number, report suite, date, status and how long it took.

```
uv run ahd runs
AA_PROFILE=prod uv run ahd runs
```

## prune

Deletes stored runs from the database. Give `--run` once for each run number, `--before` to delete every run dated before a day, or both.

```
uv run ahd prune --run 1 --run 4
uv run ahd prune --before 2026-10-01 --yes
```

It lists the runs it will delete and asks you to type y first. `--yes` skips the question. A run that is still marked "running" is kept unless you add `--force`. Reports that are already written stay in the reports folder. See [How runs are stored](runs.md#deleting-runs).

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Everything worked. |
| 1 | The command failed. The message on stderr says why. `prune` also exits with 1 when you answer no. |
| 2 | `check` found limited access, or a check could not be run. `suites` found no report suites. Any command also exits with 2 when an option is wrong, e.g., `--change-alert 12`, and prints its usage. |
| 3 | `run` or `individual` finished, but some columns or requests had problems. The report's status section lists them. After a run of several suites, at least one suite ended "partial" and none failed. |
| 130 | You stopped the command with Ctrl-C. A run that `ahd run` had started is marked "failed". A pair that `ahd individual` was adding is removed. |

## Defaults

| Setting | Default |
|---|---|
| Database | `./data/ahd.duckdb`, or `~/.aa/orgs/<name>/ahd.duckdb` with a profile |
| Report folder | `./reports/` |
| Individual Component Summary pair | Page by Occurrences |
| Large drop slider | 30 percent |
| Columns per trend request | 20 |
| Requests at the same time | 8 |
