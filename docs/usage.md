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
- [alerts](#alerts): print the alerts of stored runs as text, JSON or CSV
- [runs](#runs): list stored runs
- [prune](#prune): delete stored runs
- [export and import](#export-and-import): back up stored runs, or copy them into another file
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

The commands that sign in are `check`, `suites`, `segments`, `run` and `individual`. Each of them first prints where the credential came from, e.g., "Signed in using profile prod". The others never call Adobe. `runs`, `render`, `alerts` and `export` only read the database. `prune` deletes runs from it, and `import` adds runs to it. Only `run` and `import` create a new database. The other commands stop when the file does not exist.

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

`ahd alerts` and the example script [`send_alerts.py`](duckdb.md#send-an-alert-email-every-morning) do not look up names, because they never call Adobe. They take only report suite IDs and segment IDs, and they must match exactly.

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
| `--anomaly-method` | How the report finds unusual values: `adobe`, `weekday-median`, `weekday-iqr` or `weekday-trend`. The default is `adobe`, which uses Adobe's anomaly detection. The other three compute the expected range from the daily values of the run, with no extra requests to Adobe. The variable `AHD_ANOMALY_METHOD` can set another default. See [The report](report.md#anomaly-detection-methods) and [Set the anomaly method once](#set-the-anomaly-method-once). |
| `--anomaly-sensitivity` | How wide the expected range of a local anomaly method is: `low`, `normal` or `high`. The default is `normal`. `low` makes the range wider, so fewer values are flagged. `high` makes it narrower, so more values are flagged. It works only with `weekday-median`, `weekday-iqr` or `weekday-trend`. With `adobe`, any value other than `normal` stops the command with an error, because Adobe returns its own range. The variable `AHD_ANOMALY_SENSITIVITY` can set another default. See [The report](report.md#the-sensitivity-of-the-local-methods). |
| `--out` | The folder for the HTML report. The default is `./reports/`. |
| `--batch-size` | How many columns go into one trend request, from 1 to 100. The default is 20. |
| `--workers` | How many requests run at the same time, from 1 to 16. The default is 8. The tool starts no more than 12 requests in any 6 seconds, which is Adobe's limit. |

While it runs, it prints its progress on stderr, e.g., "Daily trends: 12 of 21 requests done." When it ends, it prints the path of the report, the run's status and the number of alerts for yesterday, e.g.:

```
reports/ahd_my_suite_2026-11-08_run12.html
Run 12 for my_suite finished with status: ok
Alerts for 2026-11-07: 2 unusual values, 1 large drop, 0 large spikes, 0 drops to zero
```

The alerts line uses the `--change-alert` percent for large drops, the `--spike-alert` percent for large spikes and the `--anomaly-method` and `--anomaly-sensitivity` for unusual values. It is left out after a failed run.

### Set the anomaly method once

Two environment variables set the defaults of the two anomaly options:

| Variable | Sets the default of | Values |
|---|---|---|
| `AHD_ANOMALY_METHOD` | `--anomaly-method` | `adobe`, `weekday-median`, `weekday-iqr` or `weekday-trend` |
| `AHD_ANOMALY_SENSITIVITY` | `--anomaly-sensitivity` | `low`, `normal` or `high` |

```
export AHD_ANOMALY_METHOD=weekday-median
export AHD_ANOMALY_SENSITIVITY=high
ahd run --rsid my_suite
```

- `ahd run`, `ahd individual`, `ahd render` and `ahd alerts` read them. The alert email script [`send_alerts.py`](duckdb.md#send-an-alert-email-every-morning) gets its alerts from `ahd alerts`, so it uses them too. A scheduled job that sets them once gives the reports and the email the same method. See [Run it every day](scheduling.md).
- An option on the command line wins over its variable, e.g., `--anomaly-sensitivity normal` with `AHD_ANOMALY_SENSITIVITY=high` gives `normal`.
- Without the option and the variable, the defaults are `adobe` and `normal`. An empty variable counts as not set.
- A value that is not in the list stops the command with exit code 1, before it signs in or opens the database. The message names the variable and the values it takes.
- The sensitivity still works only with a local method, wherever each value comes from. With the method `adobe`, a sensitivity of `low` or `high` stops the command, and the message names the variable that gave it.
- The tool reads them each time a command runs. They are not stored with the run. A report says which method and sensitivity it used, in the header fact "Anomaly detection".

If the run sees more than 20 percent fewer custom events, eVars or props than the run before it for the same suite, it prints a warning. The run before is the run of that suite that was made last before this one, also when its run number is higher, e.g., after `ahd import`. The warning gives both counts and the usual cause. See [Troubleshooting](troubleshooting.md#fewer-components-than-last-time).

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

`--run`, `--dimension` and `--metric` are required. `--dimension` and `--metric` work as in `run`. The report's Dimension and Metric dropdowns then switch between all the pairs of the run. `--out`, `--change-alert`, `--spike-alert`, `--anomaly-method` and `--anomaly-sensitivity` work as in `run`.

It stops with an error in these cases:

- The run already holds the same pair, and the pair stored some data. A pair that stored nothing, e.g., the first pair of a failed run, is tried again.
- The run is still marked "running".
- There is no stored run with that number, or no database at the path.

## render

Rebuilds the report of a stored run from the database, with no calls to Adobe. A rebuilt report uses the current version of the page design.

```
ahd render --run 3
ahd render --run 3 --change-alert 50 --out ./shared/
ahd render --run 3 --anomaly-method weekday-median
ahd render --run 3 --anomaly-method weekday-median --anomaly-sensitivity low
```

`--run` is required. `--out`, `--change-alert`, `--spike-alert`, `--anomaly-method` and `--anomaly-sensitivity` work as in `run`.

The anomaly method and its sensitivity are chosen each time a report is written. They are not stored with the run. So you can write the report of any stored run again with another method or sensitivity, e.g., to compare the flags of `weekday-median` with Adobe's, and no new requests go to Adobe. This also works for a run whose credential may not use Adobe's anomaly detection. See [Troubleshooting](troubleshooting.md#limited-access). `render` only reads the database, so it works while a `duckdb -readonly` session has the file open.

## alerts

Prints the alerts of stored runs, with no calls to Adobe. It finds them by the same rules as the report and the alert line of `ahd run`, because it reads them with the [`alerts()` macro](duckdb.md#alertsrun-threshold--030-spike_threshold--050-min_usual--10-for_day--null-method--adobe-sensitivity--normal). The alerts are for the day before the run date. Use it to read the alerts in a terminal, to put them in an email, or to give them to another program.

```
ahd alerts --run 2
ahd alerts --rsid my_suite
ahd alerts --rsid my_suite --rsid other_suite --segment s300_mobile
ahd alerts --all-suites --format json
ahd alerts --all-suites --format csv > alerts.csv
ahd alerts --rsid my_suite --change-alert 27.5 --anomaly-method weekday-median
```

| Option | What it does |
|---|---|
| `--run` | Prints the alerts of this stored run. The run must have ended "ok" or "partial". |
| `--rsid` | A report suite ID, as `ahd runs` lists it. Prints the alerts of the latest run of the suite that ended "ok" or "partial", which is the run that the view [`latest_runs`](duckdb.md#latest_runs) holds. Give it more than once for several suites. They come in the order you give them, and each suite comes once. |
| `--all-suites` | Prints the alerts of the latest run that ended "ok" or "partial" of every suite in the database, in the order of the suite names. |
| `--segment` | With `--rsid` or `--all-suites`, uses the latest runs with this segment ID. Without it, the command uses the runs without a segment. It does not work with `--run`, because a run already has its segment. |
| `--format` | `text`, the default, for a person or the body of a plain email. `json` or `csv` for another program. See [The formats](#the-formats). |
| `--change-alert` | A large drop is a value more than this percent below the weekday average. The default is 30. Any percent above 0 and up to 100 works, e.g., `27.5`, because there is no slider here. |
| `--spike-alert` | A large spike is a value more than this percent above the weekday average. The default is 50. Any percent above 0 and up to 200 works. |
| `--anomaly-method`, `--anomaly-sensitivity` | Work as in `run`, with the same defaults and the same variables `AHD_ANOMALY_METHOD` and `AHD_ANOMALY_SENSITIVITY`. With `adobe`, `low` or `high` stops the command with exit code 1. See [Set the anomaly method once](#set-the-anomaly-method-once). |

You need one of `--run`, `--rsid` or `--all-suites`. When a suite has no finished run, the command says so on stderr, e.g., "holds no finished run of my_suite without a segment". It still prints the alerts of the other suites, and exits with 1.

The command opens the database read-only, so it works while a `duckdb -readonly` session has the file open. While `ahd run` or another command writes to the file, it stops with "is in use by another program". It uses the rules of the version of `ahd` that you run, also on a file that an older version wrote. Such a file keeps the views and macros of that version, e.g., an `alerts()` macro without `sensitivity`. The command makes the current views and macros for its own use and leaves the file as it is.

### The formats

`text` starts with the rules in use. Then it gives each run a heading with its number, suite and status, the same alert line that `ahd run` prints, and one line for each alert. A run without alerts has only its heading and its alert line. A character in a name that could change how a terminal shows the text, e.g., a line break, shows as `?`. For the Adobe Store suite, shortened:

```
A large drop is more than 30% below the weekday average, and a large spike is more than 50% above it. The weekday average is the average of the same weekday in the 12 weeks before. Adobe's anomaly detection flags the unusual values.

Run 2, Adobe Store (dgeo1xxpnwcidadobestore), status ok
Alerts for 2026-11-11: 3 unusual values, 0 large drops, 10 large spikes, 0 drops to zero
- unusual: Exit Link Instances (metrics/exitlinkinstances), weekday average 2.42, actual 8, expected 2.51, change +231.0%
- unusual: Cart Additions (metrics/cartadditions), weekday average 140.08, actual 230, expected 77.06, change +64.2%
- unusual: Orders (metrics/orders), weekday average 18.33, actual 35, expected 20.91, change +90.9%
- large spike: Custom Link Instances (metrics/customlinkinstances), weekday average 12.08, actual 41, change +239.3%
- large spike: Page (variables/page), weekday average 4,848.50, actual 8,172, change +68.5%
```

`json` gives one object with these fields:

- `format_version`: the version of this layout, now 1. It changes only when a field is removed or changes its meaning.
- `runs`: one object for each run, in the order of the text.

Each run has these fields:

- `run_id`, `rsid`, `rsid_name`, `segment_id` and `status`, as in the `runs` table. `segment_id` is `null` for a run without a segment.
- `day`: the day of the alerts, as YYYY-MM-DD.
- `method` and `sensitivity`: the anomaly method and sensitivity in use, e.g., `weekday-median` and `high`. `method_name` is the name that the report gives the method, e.g., "Weekday median".
- `threshold` and `spike_threshold`: the percents for a large drop and a large spike, as shares, e.g., `0.3` for 30 percent, as the `alerts()` macro takes them.
- `alerts`: one object for each alert, in the order of the `alerts()` macro. It has the columns of the macro: `alert`, `anomaly`, `component_id`, `name`, `group`, `metric_type`, `value`, `usual`, `expected` and `pct_vs_usual`. The run gives the `run_id` and the `day` of the macro. The [DuckDB guide](duckdb.md#alertsrun-threshold--030-spike_threshold--050-min_usual--10-for_day--null-method--adobe-sensitivity--normal) explains each column. A run without alerts has an empty list.

Later versions may add fields to the object, to a run and to an alert, e.g., the day since an alert has held. A program that reads the JSON should ignore the fields it does not know. A number that is not finite, e.g., a value that Adobe sent as infinity, is `null`. Every character outside ASCII is written as an escape, e.g., `日` for 日, so the output reads the same in any text encoding. For the same suite, shortened:

```json
{
  "format_version": 1,
  "runs": [
    {
      "run_id": 2,
      "rsid": "dgeo1xxpnwcidadobestore",
      "rsid_name": "Adobe Store",
      "segment_id": null,
      "status": "ok",
      "day": "2026-11-11",
      "method": "adobe",
      "method_name": "Adobe",
      "sensitivity": "normal",
      "threshold": 0.3,
      "spike_threshold": 0.5,
      "alerts": [
        {
          "alert": "unusual",
          "anomaly": "high",
          "component_id": "metrics/exitlinkinstances",
          "name": "Exit Link Instances",
          "group": "Reserved Props",
          "metric_type": "int",
          "value": 8.0,
          "usual": 2.4166666666666665,
          "expected": 2.505854996828865,
          "pct_vs_usual": 2.310344828
        }
      ]
    }
  ]
}
```

`csv` gives one header row and one row for each alert. Each row starts with the fields of its run, as in the JSON, and goes on with the fields of the alert. A run without alerts has no row. A value with a comma, a quote or a line break is put in quotes, as CSV does it. For the same suite, shortened:

```
run_id,rsid,rsid_name,segment_id,status,day,method,method_name,sensitivity,threshold,spike_threshold,alert,anomaly,component_id,name,group,metric_type,value,usual,expected,pct_vs_usual
2,dgeo1xxpnwcidadobestore,Adobe Store,,ok,2026-11-11,adobe,Adobe,normal,0.3,0.5,unusual,high,metrics/exitlinkinstances,Exit Link Instances,Reserved Props,int,8.0,2.4166666666666665,2.505854996828865,2.310344828
2,dgeo1xxpnwcidadobestore,Adobe Store,,ok,2026-11-11,adobe,Adobe,normal,0.3,0.5,unusual,high,metrics/cartadditions,Cart Additions,Reserved Purchase Events,int,230.0,140.08333333333334,77.06421405092522,0.641879833
```

### Exit codes of alerts

| Code | Meaning |
|---|---|
| 0 | It printed at least one alert. |
| 4 | The chosen runs have no alerts. |
| 1 | Something is wrong, e.g., there is no such run, or the file is in use. When a suite has no finished run, the command still prints the alerts of the others. |
| 2 | An option is wrong. |

So a scheduled job can send the alerts only when there are any, e.g., with a `mail` program:

```
ahd alerts --profile prod --all-suites > alerts.txt && mail -s "Analytics Health alerts" team@example.com < alerts.txt
```

The example script [`send_alerts.py`](duckdb.md#send-an-alert-email-every-morning) builds an HTML email from `ahd alerts --format json`.

## runs

Lists every stored run with its number, report suite, run date, status and how long it took. The run with the highest number comes first. That is the run made last, except after an `ahd import` that gave old runs new numbers.

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

## export and import

`export` writes every stored run to a folder, as one Parquet file for each table and a `manifest.json` file. Parquet is a file format for tables that DuckDB, Python and most data tools read. The folder must be new or empty.

```
ahd export --out backup/2026-11
```

`import` copies the runs of another ahd database, or of a folder that `export` wrote, into the database that `--db` names. It makes that database when it does not exist.

```
ahd import data/ahd-old.duckdb
ahd import backup/2026-11 --db data/other.duckdb
```

- A run that the database already holds is left out, so you can import the same source again.
- The runs keep their numbers when those numbers are free. If one is taken, each imported run gets the next free number, and the command lists the changes.
- If any part fails, nothing is imported.
- Neither command changes the source, and neither calls Adobe.

`export` also reads a file that this version of the tool refuses because of its layout. `import` is how you keep your runs when a new version changes the layout. See [How runs are stored](runs.md#layout-versions).

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Everything worked. `alerts` printed at least one alert. |
| 1 | The command failed. The message on stderr says why. `run` also exits with 1 when the run ends "failed". `prune` also exits with 1 when you answer no. `alerts` also exits with 1 when a suite has no finished run. |
| 2 | `check` found limited access, or a check could not be run. `check` or `suites` found no report suites. Any command also exits with 2 when an option is wrong, e.g., `--change-alert 12`, and prints its usage. |
| 3 | `run` or `individual` finished, but some columns or requests had problems. The report's status section lists them. After a run of several suites, at least one suite ended "partial" and none failed. |
| 4 | `alerts` found no alerts in the chosen runs. See [Exit codes of alerts](#exit-codes-of-alerts). |
| 130 | You stopped the command with Ctrl-C. A run that `ahd run` had started is marked "failed". A pair that `ahd individual` was adding is removed. |

## Defaults

| Setting | Default |
|---|---|
| Database | `./data/ahd.duckdb`, or `~/.aa/orgs/<name>/ahd.duckdb` with a profile |
| Report folder | `./reports/` |
| Individual Component Summary pair | Page by Occurrences |
| Large drop slider, and the large drop of `alerts` | 30 percent |
| Large spike slider, and the large spike of `alerts` | 50 percent |
| Anomaly method | `AHD_ANOMALY_METHOD` when it is set, else `adobe`, Adobe's anomaly detection |
| Anomaly sensitivity | `AHD_ANOMALY_SENSITIVITY` when it is set, else `normal` |
| Columns per trend request | 20 |
| Requests at the same time | 8 |
