# Troubleshooting

[README](../README.md) · [Usage](usage.md) · [Configuration](configuration.md) · [The report](report.md) · [How runs are stored](runs.md) · [DuckDB guide](duckdb.md) · [Run it every day](scheduling.md) · **Troubleshooting** · [Compared with the Excel workbook](excel-comparison.md) · [Development](development.md)

Start with `ahd check --rsid <suite>`. It signs in, counts what the credential can see, tests anomaly detection, and explains any fix in plain words. The sections below cover each message in more detail.

## Contents

- [Sign-in problems](#sign-in-problems)
- [Limited access](#limited-access)
- [Fewer components than last time](#fewer-components-than-last-time)
- [Problems in the status section](#problems-in-the-status-section)
- [Time zones](#time-zones)
- [Profiles and config files](#profiles-and-config-files)
- [The database](#the-database): a file from another version, no file, a file in use, an older `duckdb`, and a run that stays "running"
- [Getting more detail](#getting-more-detail)

## Sign-in problems

When sign-in fails, the tool says what is wrong and what to fix. The fixes happen in the [Adobe Developer Console](https://developer.adobe.com/console/) or the [Adobe Admin Console](https://adminconsole.adobe.com/). [Configuration](configuration.md#create-the-credential) shows how the credential is set up. The tool tells these causes apart:

| The message says | Cause | Fix |
|---|---|---|
| "Adobe does not know this client ID" | The client ID is wrong. | Copy it again from the credential in the Adobe Developer Console. Check for extra spaces. |
| "the client secret is wrong" | The secret is wrong, or it was rotated. | Copy the current secret from the Developer Console. |
| "the org ID does not belong to this credential" | The org ID is from another organization. | Copy the organization ID shown on the same credential. It ends with `@AdobeOrg`. |
| "the scopes are wrong" | A required scope is missing. Adobe still gives a token, but the token sees no Analytics company. | Copy the full scopes list from the Developer Console. The message names what is missing. |
| "this credential sees no Adobe Analytics company" | The credential is not in an Analytics product profile. | In the Adobe Admin Console, add the credential's technical account to an Adobe Analytics product profile. |
| "This credential signs in but sees no report suites" | The product profile includes no report suites. `ahd check` and `ahd suites` exit with 2. `ahd run --all-suites` exits with 1. | Add the report suites to the product profile in the Admin Console. |
| "This credential cannot see the login company" | `--company` names a login company the credential cannot see. | Use one of the companies the message lists. |

Changes in the Admin Console can take a while to reach Analytics. Wait a little and run `ahd check` again.

A message that says "This is a problem at Adobe, not with the credential" means that Adobe's sign-in service answered with a temporary error, e.g., HTTP 503, 3 times in a row. Wait a little and try again.

## Limited access

`ahd check --rsid <suite>` exits with 2 when access is limited:

- **"No custom events, eVars or props are visible"** means the product profile hides them. Add the report suite's metrics and dimensions to the product profile.
- **"Anomaly detection: not allowed"** means the product profile lacks the Report Suite Tools item "Anomaly Detection". Runs still work, but the report has no expected values and no anomaly flags.
- **"could not be tested"** means the test request failed, e.g., because of a network problem. Run the check again.

In both cases, a run stores no expected values and no flags from Adobe. The report can still flag unusual values with a local anomaly method, which ahd computes from the run's own daily values and which needs no permission in Adobe. Give the option to the run, e.g., `ahd run --rsid my_suite --anomaly-method weekday-median`. For a run that is already stored, write its report again, e.g., `ahd render --run 3 --anomaly-method weekday-median`. This makes no requests to Adobe. The local methods are simpler than Adobe's anomaly detection and will flag different days. For a component that grows or shrinks over the weeks, `weekday-trend` may suit it better. See [The report](report.md#anomaly-detection-methods).

See [Configuration](configuration.md#permissions) for the full list of permissions.

## Fewer components than last time

After each run, the tool compares the number of custom events, eVars and props with the run before it. That is the run of the same report suite that was made last before this one and stored any components. The run number does not decide it, e.g., an old run that `ahd import` gave a higher number is not the run before. That run can have another segment. If any of them fell by more than 20 percent, the run output and the report show a warning.

Data did not stop. This run simply cannot see the missing components, so the report does not check them. The usual cause is one of these:

- a change to the credential's product profile in the Adobe Admin Console
- a run made with a different credential

## Problems in the status section

A run that finished with status "partial" lists what it could not read. `ahd run` and `ahd individual` exit with 3 in that case. Common causes:

- **"Some metrics or dimensions are not visible to this credential."** The product profile hides those components. The list under the heading names them.
- **"Some parts of the Individual Component Summary came back empty."** The product profile may hide the chosen dimension, or it had no data.
- **"Some requests failed because of network problems."** Running again may fix it.
- **"Some requests failed because Adobe's sign-in service had a temporary problem."** The tool signs in again when its token runs out. Adobe's sign-in service answered with a temporary error 3 times. Running again may fix it.
- **"Adobe sent an answer that could not be read for some requests."** Adobe answered with valid JSON that was not a report, e.g., with an empty value where a number belongs. Running again may fix it.
- **"Adobe returned an error for some requests."** Adobe answered with an error that has no code of its own. The list under the heading gives the HTTP status of each.
- **"Anomaly detection could not be tested"** or **"Anomaly bands are not available."** See [Limited access](#limited-access).
- A report with a local anomaly method says about the same cases that **"This report does not need it, because it uses"** that method, e.g., "the Weekday median method". The report still has flags, so nothing needs a fix.

A command can also stop with one line that says "Adobe sent an answer for ... in a form that the tool could not read", e.g., for the list of metrics. Adobe then sent a whole answer in a form that the tool does not know, before the run stored anything. Run the command again. In a run of several suites, only that suite fails.

When a request fails in a way that may pass, the tool waits and tries again. How long it waits depends on the kind of error:

- **A rate limit (HTTP 429) or Adobe's temporary error 403025.** Adobe is busy. The tool tries up to 6 more times and waits about 30 seconds to 1 minute in all.
- **A server error (HTTP 500 to 599) or a network error.** The tool tries up to 3 more times and waits about 3 to 7 seconds in all. An Adobe outage or a computer with no network shows within seconds, e.g., in `ahd check`.

Adobe allows 12 requests in any 6 seconds for each user. The tool starts no more than that, even with many workers, so a run by itself should not get a rate limit. Two `ahd` commands at the same time with the same credential each keep their own count, so together they can go over the limit.

When Adobe asks for a longer wait with a Retry-After header, the tool waits that long, up to 2 minutes. The tool also waits at most 10 seconds for a connection to Adobe. It records a problem only when the last try also fails.

**Copy problems** in the status section copies the list for an email or a ticket.

## Time zones

- **"which this computer does not know"**: the time zone data is missing, e.g., on Windows after an incomplete install. Install the tool again, e.g., `uv tool install --reinstall aa-health-duckdb`, or run `uv sync` in a clone. To make a run anyway, give the date with `ahd run --date YYYY-MM-DD`.

## Profiles and config files

- **"Profile 'x' was not found"**: the file `~/.aa/orgs/x/config.json` does not exist. Check the name, or create the profile with `aa_auto_sdr --profile-add`.
- **"Profile name 'x' is not valid"**: a profile name holds only letters, digits, dots, dashes and underscores, and starts with a letter or a digit.
- **"is not valid UTF-8 JSON"**: the file has a syntax error, e.g., a missing comma or quote.
- **"must hold one JSON object"**: the file holds a list or a single value, not `{ ... }`.
- **"No credential was found"**: there is no profile, no `AHD_*` variable and no `config.json` in the current folder. See [Configuration](configuration.md).
- **"Use a profile or --config, not both"**: remove `--config`, or unset `AA_PROFILE` with `unset AA_PROFILE`.
- **"Missing credential keys"**: one of `org_id`, `client_id`, `secret` or `scopes` is missing or empty. The message names the key and where the tool looked.

## The database

### The database was made by a different version

```
The database data/ahd.duckdb was made by a different version of ahd.
```

The rest of the message says what to do. There are three cases:

- **The file is from an older version, and its runs can be kept.** The message names two steps: rename the file, then run `ahd import` with the renamed file. The tool then makes a new file and copies the old runs into it. See [How runs are stored](runs.md#layout-versions).
- **The file is from a newer version.** Update the tool, or choose another file with `--db`.
- **The file is too old to import.** This holds for files from 0.1.0 and 0.1.1. Delete the file, or choose another one with `--db`, and run again.

### There is no database

```
There is no database at data/ahd.duckdb.
```

Only `ahd run` and `ahd import` make a new file. Every other command needs a file that holds runs. Make a run first, or name the file with `--db` or a profile. [How runs are stored](runs.md#where-the-file-is) says where the file is.

Two more messages name a file that is not usable. "holds no ahd runs" means the file is an empty DuckDB database. "is not a DuckDB database" means the file is something else. Choose the right file with `--db`.

### The database is in use by another program

```
The database data/ahd.duckdb is in use by another program
```

Only one program can write to a DuckDB file at a time. While a program writes to it, no other program can open it. While a program reads it, nothing can write to it. Some examples:

- While `ahd run`, `ahd individual`, `ahd prune` or `ahd import` works on the file, wait for it to finish.
- A `duckdb` session that is still open on the file also blocks `ahd run`. Close it, then try again. `ahd runs` and `ahd render` only read, so they work while a `duckdb -readonly` session is open.

DuckDB's own message for this is "Could not set lock on file".

### An older duckdb cannot open the file

```
Trying to read a database file with version number 69, but we can only read versions between 64 and 68.
```

The `duckdb` command is older than 2.0. Update it, then check with `duckdb --version`. If an old version still shows, another copy comes first on your `PATH`. `which -a duckdb` lists every copy. See the [DuckDB guide](duckdb.md#before-you-start).

### A run stays "running"

A run that you stop with Ctrl-C is marked "failed". The requests that were already sent finish first, and the rest are not sent. A request that was waiting to try again is not tried again.

A run that stopped in another way, e.g., because you closed the window, keeps the status "running". Make a new run. To delete the old one, use `ahd prune --run N --force`.

## Getting more detail

Add `--verbose` before the command to write debug detail to stderr, e.g., each retry:

```
ahd --verbose run --rsid my_suite
```

The debug output never includes a credential value or a token.
