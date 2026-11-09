# Usage

[README](../README.md) · **Usage** · [Configuration](configuration.md) · [The report](report.md) · [How runs are stored](runs.md) · [DuckDB guide](duckdb.md) · [Run it every day](scheduling.md) · [Troubleshooting](troubleshooting.md) · [Compared with the Excel workbook](excel-comparison.md) · [Development](development.md)

This page describes every `ahd` command, with its options and exit codes. Add `--help` to any command to see its options. If you work from a clone of the repository instead of an installed tool, put `uv run` in front of each command, e.g., `uv run ahd check`.

## Contents

- [A typical session](#a-typical-session)
- [Options that every command shares](#options-that-every-command-shares)
- [How IDs and names are matched](#how-ids-and-names-are-matched): what `--rsid` and the other options accept
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
ahd check                          # does the sign-in work?
ahd suites                         # which report suites can it see?
ahd check --rsid "My Suite"        # does it have full access to this suite?
ahd run --rsid "My Suite"          # fetch, store and write the report
ahd individual --run 1 --dimension evar17 --metric "Cart Additions"
ahd runs                           # list what is stored
```

## Options that every command shares

| Option | What it does |
|---|---|
| `--profile NAME` | Uses the credential in `~/.aa/orgs/NAME/config.json`, and the database in the same folder. You can put it before or after the command. `AA_PROFILE` sets it too. See [Configuration](configuration.md). |
| `--config PATH` | Uses this credential file instead of `./config.json`. It cannot be used with a profile. For the commands that sign in. |
| `--company ID_OR_NAME` | Chooses the login company, by global company ID or by name, when the credential sees more than one. Without it, the tool uses the first one and prints its name. For the commands that sign in. |
| `--db PATH` | Uses this database file. For the commands that read or write a database. |
| `--verbose` | Writes debug detail to stderr. Put it before the command, e.g., `ahd --verbose run ...`. |

The commands that sign in are `check`, `suites`, `segments`, `run` and `individual`. Each of them first prints where the credential came from, e.g., "Signed in using profile prod". The others never call Adobe. `runs` and `render` only read the database. `prune` deletes runs from it. Only `run` creates a new database. The other commands stop when the file does not exist.

Where a command takes a report suite, segment, dimension or metric, it accepts an ID or a name. See [How IDs and names are matched](#how-ids-and-names-are-matched).

## How IDs and names are matched

`--rsid` and `--segment` take an ID or a name. The tool reads the list of report suites or segments from Adobe and picks one this way:

1. If the text is exactly the ID of an item, the tool uses that item. The case must match, e.g., `my_suite` does not match the ID `My_Suite`. Spaces before or after the text are not ignored here.
2. Otherwise, the tool looks for an item with that name. The case does not matter, and spaces before or after the name are ignored, e.g., `--rsid " my suite "` matches the suite named "My Suite".
3. If two or more items have that name, the tool stops and lists their IDs. Give one of the IDs instead.
4. If nothing matches, the tool stops. For a report suite, it tells you to run `ahd suites`. For a segment, it tells you to run `ahd segments --rsid` with the suite.

`--dimension` and `--metric` follow the same steps, with two differences:

- The ID matches in any case, and spaces before or after it are ignored.
- You can leave out the start of the ID, e.g., `evar17` for `variables/evar17`, or `cartadditions` for `metrics/cartadditions`.

When nothing matches a dimension or metric, the tool suggests an ID such as `evar1`, or the name shown in Analysis Workspace.

`--company` takes a global company ID or a name. Both match in any case, and spaces before or after the text are ignored. If two login companies match, the tool uses the first one that Adobe lists.

With several `--rsid` options, `ahd run` matches each one before it checks any suite. If one does not match, the command stops and checks no suite. A suite that you give twice, e.g., once by ID and once by name, is checked once.

The example script [`send_alerts.py`](duckdb.md#send-an-alert-email-every-morning) does not look up names. It takes only report suite IDs and segment IDs, and they must match exactly.

### Compared with aa_auto_sdr

[aa_auto_sdr](https://github.com/brian-a-au/aa_auto_sdr) also takes a report suite ID or a name. Both tools first look for the exact ID, with the case as you typed it. Then both look for the name in any case. They differ in these ways:

- When two suites have the same name, aa_auto_sdr uses all of them by default. `ahd` stops and asks for the ID.
- aa_auto_sdr has the option `--name-match` with the values `exact`, `insensitive` and `fuzzy`. The default is `insensitive`, which matches the name in any case. `exact` needs the same case, and `fuzzy` also accepts a name that is spelled almost the same. With `exact` or `fuzzy`, aa_auto_sdr also stops when two suites have the same name. `ahd` has no such option.
- `ahd` ignores spaces before or after the name. aa_auto_sdr does not.

## check

Tests the sign-in, and what the credential can see.

```
ahd check
ahd check --rsid "My Suite"
```

| Option | What it does |
|---|---|
| `--rsid` | A report suite, by ID or name, to check in more detail. See [How IDs and names are matched](#how-ids-and-names-are-matched). |

Without `--rsid`, it prints the login company and how many report suites the credential can see. With `--rsid`, it also counts the custom events, eVars and props and tests anomaly detection. When something is missing, it explains the fix in plain words. Run it before your first full run, and after any change in the Adobe Admin Console.

## suites and segments

List what the credential can see, with the ID and the name of each item, sorted by name.

```
ahd suites
ahd segments --rsid "My Suite"
```

| Option | What it does |
|---|---|
| `--rsid` | For `segments` only, and required. The report suite, by ID or name. See [How IDs and names are matched](#how-ids-and-names-are-matched). |

`segments` lists the segments you can use with that report suite, shared ones included.

## run

Fetches 92 days of data, stores it as a new run, and writes the HTML report.

```
ahd run --rsid "My Suite"
ahd run --rsid my_suite --segment "Mobile visits"
ahd run --rsid my_suite --dimension "Internal Search Term" --metric "Cart Additions"
ahd run --rsid my_suite --date 2026-11-07 --out ./reports/
ahd run --rsid my_suite --rsid "Other Suite"
ahd run --all-suites
```

| Option | What it does |
|---|---|
| `--rsid` | The report suite, by ID or name. See [How IDs and names are matched](#how-ids-and-names-are-matched). Give it more than once to check several suites. You need `--rsid` or `--all-suites`, but not both. |
| `--all-suites` | Checks every report suite the credential can see, in the order of their names. |
| `--segment` | A segment, by ID or name. Every request is filtered by it. With several suites, the tool looks up the segment again for each suite. |
| `--dimension`, `--metric` | The first Individual Component Summary pair. Each takes a full ID (`variables/evar17`), a short ID (`evar17`) or a name (`"Internal Search Term"`). Any dimension or metric of the suite works. The default is Page by Occurrences. |
| `--date` | Treats this date as today, e.g., `2026-11-07` to repeat an earlier day. Write it as YYYY-MM-DD. It cannot be after today in the report suite's time zone, because that day's data is not complete. By default, today is today in the report suite's time zone. With `--date`, the report says so under the run date, and it names the day before that date instead of saying "yesterday". |
| `--change-alert` | Where the report's large drop slider starts: 10 to 100, in steps of 5. The default is 30. The slider shows it as a negative change, e.g., 30 is -30%. See [The report](report.md#the-large-drop-and-spike-sliders). |
| `--spike-alert` | Where the report's large spike slider starts: 10 to 200, in steps of 10. The default is 50. See [The report](report.md#the-large-drop-and-spike-sliders). |
| `--out` | The folder for the HTML report. The default is `./reports/`. |
| `--batch-size` | How many columns go into one trend request, from 1 to 100. The default is 20. |
| `--workers` | How many requests run at the same time, from 1 to 16. The default is 8. The tool starts no more than 12 requests in any 6 seconds, which is Adobe's limit. |

While it runs, it prints its progress on stderr, e.g., "Daily trends: 12 of 21 requests done." When it ends, it prints the path of the report, the run's status and the number of alerts for yesterday, e.g.:

```
reports/ahd_my_suite_2026-11-08_run12.html
Run 12 for my_suite finished with status: ok
Alerts for 2026-11-07: 2 unusual values, 1 large drop, 0 large spikes, 0 drops to zero
```

The alerts line uses the `--change-alert` percent for large drops and the `--spike-alert` percent for large spikes. It is left out after a failed run.

If the run sees more than 20 percent fewer custom events, eVars or props than the run before it for the same suite, it prints a warning. The warning gives both counts and the usual cause. See [Troubleshooting](troubleshooting.md#fewer-components-than-last-time).

With several suites, the command signs in once and checks the suites one after another. Each suite gets its own run and its own report.

- Before each suite, it prints a line such as "Report suite 2 of 3: my_suite".
- A suite that fails does not stop the others. The command prints the error on stderr, e.g., "Error for my_suite: ...", and goes on with the next suite.
- A segment, dimension or metric that a suite does not have makes only that suite fail.
- At the end, the command prints one line with the number of suites that ended "ok", "partial" and "failed", e.g., "Checked 3 report suites: 2 ok, 0 partial, 1 failed."
- The exit code is the one of the worst suite, e.g., 1 when one suite failed.
- A wrong suite name stops the command before it checks any suite.
- If the tool has to sign in again during the run, and Adobe rejects the credential, the command stops. The suite it was checking is marked "failed". It does not check the rest, because each of them would fail the same way. The command then prints no count line.
- If Adobe's sign-in service only has a temporary problem, e.g., HTTP 503, the tool tries the sign-in 3 times. If it still fails, only the requests that needed it fail, and the run ends "partial". A suite that could not start fails, and the command goes on with the next suite.

With one suite, including `--all-suites` when the credential sees only one, the command prints neither the "Report suite 1 of 1" line nor the last line. To run it every day, see [Run it every day](scheduling.md).

A run of a suite with about 400 components takes about 1 minute with the default 8 workers. See the [measured times](excel-comparison.md#performance).

## individual

Adds a dimension by metric pair to a stored run, then writes the report again. It makes 5 requests and takes a few seconds, not a full run. It uses the run's report suite, segment and dates.

```
ahd individual --run 3 --dimension evar17 --metric revenue
ahd individual --run 3 --dimension "Internal Search Term" --metric "Cart Additions"
```

`--run`, `--dimension` and `--metric` are required. `--dimension` and `--metric` work as in `run`. The report's Dimension and Metric dropdowns then switch between all the pairs of the run. `--out`, `--change-alert` and `--spike-alert` work as in `run`.

It stops with an error in these cases:

- The run already holds the same pair.
- The run is still marked "running".
- There is no stored run with that number, or no database at the path.

## render

Rebuilds the report of a stored run from the database, with no calls to Adobe. A rebuilt report uses the current version of the page design.

```
ahd render --run 3
ahd render --run 3 --change-alert 50 --out ./shared/
```

`--run` is required. `--out`, `--change-alert` and `--spike-alert` work as in `run`. `render` only reads the database, so it works while a `duckdb -readonly` session has the file open.

## runs

Lists every stored run, newest first, with its number, report suite, date, status and how long it took.

```
ahd runs
AA_PROFILE=prod ahd runs
```

## prune

Deletes stored runs from the database. Give `--run` once for each run number, `--before` to delete every run whose run date is before a day, or both. Without either option, it stops with an error.

```
ahd prune --run 1 --run 4
ahd prune --before 2026-10-01 --yes
```

It lists the runs it will delete and asks you to type y first. `--yes` skips the question. When there is no input to read the answer from, e.g., in a scheduled job, it deletes nothing and exits with 1, so add `--yes` there. A run that is still marked "running" is kept unless you add `--force`. Reports that are already written stay in the reports folder. See [How runs are stored](runs.md#deleting-runs).

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Everything worked. |
| 1 | The command failed. The message on stderr says why. `run` also exits with 1 when the run ends "failed". `prune` also exits with 1 when you answer no. |
| 2 | `check` found limited access, or a check could not be run. `check` or `suites` found no report suites. Any command also exits with 2 when an option is wrong, e.g., `--change-alert 12`, and prints its usage. |
| 3 | `run` or `individual` finished, but some columns or requests had problems. The report's status section lists them. After a run of several suites, at least one suite ended "partial" and none failed. |
| 130 | You stopped the command with Ctrl-C. A run that `ahd run` had started is marked "failed". A pair that `ahd individual` was adding is removed. |

## Defaults

| Setting | Default |
|---|---|
| Database | `./data/ahd.duckdb`, or `~/.aa/orgs/<name>/ahd.duckdb` with a profile |
| Report folder | `./reports/` |
| Individual Component Summary pair | Page by Occurrences |
| Large drop slider | 30 percent |
| Large spike slider | 50 percent |
| Columns per trend request | 20 |
| Requests at the same time | 8 |
