# Troubleshooting

[README](../README.md) · [Usage](usage.md) · [Configuration](configuration.md) · [The report](report.md) · [How runs are stored](runs.md) · [DuckDB guide](duckdb.md) · [Run it every day](scheduling.md) · [Email alerts](email-alerts.md) · [Slack alerts](slack-alerts.md) · **Troubleshooting** · [Compared with the Excel workbook](excel-comparison.md) · [Development](development.md)

Start with `ahd check --rsid <suite>`. It signs in, counts what the credential can see, tests anomaly detection, and explains any fix in plain words. The sections below cover each message in more detail.

## Contents

- [Sign-in problems](#sign-in-problems)
- [Limited access](#limited-access)
- [Fewer components than last time](#fewer-components-than-last-time)
- [Problems in the status section](#problems-in-the-status-section)
- [Anomaly detection options](#anomaly-detection-options)
- [Time zones](#time-zones)
- [Profiles and config files](#profiles-and-config-files)
- [The database](#the-database): a file from another version, no file, a file in use, an older `duckdb`, a run that stays "running", and runs that `ahd alerts` cannot read
- [Email and Slack alerts](#email-and-slack-alerts): a warning about an old run, `--test` and `--new-only`
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
| "did not come from Adobe's sign-in service" | Something between the computer and Adobe answered the sign-in request, e.g., a proxy or the sign-in page of a network. The credential may be fine. | Sign in to the network, e.g., the Wi-Fi of a hotel, or check the proxy settings. Then run `ahd check` again. |
| "This credential cannot see the login company" | `--company` names a login company the credential cannot see. | Use one of the companies the message lists. |

Changes in the Admin Console can take a while to reach Analytics. Wait a little and run `ahd check` again.

A message that says "This is a problem at Adobe, not with the credential" means that Adobe answered a sign-in request with a temporary error, e.g., HTTP 503, 3 times in a row. Sign-in makes two requests. The first gets a token from Adobe's sign-in service. The second is the Analytics discovery call, which finds the login company. The message names the one that failed. Wait a little and try again. Between the tries, the tool waits 1 second and then 3 seconds. When Adobe asks for a longer wait with a Retry-After header, the tool waits that long, up to 2 minutes.

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

## Anomaly detection options

- **"--anomaly-sensitivity works only with a local anomaly method"**: the command got `--anomaly-sensitivity low` or `high` with the method `adobe`, which is the default. Adobe returns its own expected range, so ahd cannot make it wider or narrower. Add a local method, e.g., `--anomaly-method weekday-median`, or leave out `--anomaly-sensitivity`. The command stops before it signs in or opens the database. The alert email script [`send_alerts.py`](email-alerts.md) stops with the same message and exit code 1. See [The report](report.md#the-sensitivity-of-the-local-methods).
  - When the sensitivity comes from the variable `AHD_ANOMALY_SENSITIVITY`, the message starts with "The anomaly sensitivity" and names the variable. Unset the variable, set it to `normal`, or choose a local method with `--anomaly-method` or `AHD_ANOMALY_METHOD`.
  - When the method `adobe` comes from the variable `AHD_ANOMALY_METHOD`, the message says so. Set the variable to a local method, or give `--anomaly-method` with a local method.
- **"variable is set to 'x', which is not an anomaly"**: the variable `AHD_ANOMALY_METHOD` or `AHD_ANOMALY_SENSITIVITY` holds a value that is not a method or a sensitivity, e.g., `weekday_median` with an underscore or `High` with a capital letter. The message names the variable and the values it takes. Fix the value, or unset the variable. An option on the command line wins over the variable, so the command does not read the variable then. The command stops with exit code 1 before it signs in or opens the database, and `send_alerts.py` stops in the same way. See [Set the anomaly method once](usage.md#set-the-anomaly-method-once).

## Time zones

- **"which this computer does not know"**: the time zone data is missing, e.g., on Windows after an incomplete install. Install the tool again, e.g., `uv tool install --reinstall aa-health-duckdb`, or run `uv sync` in a clone. To make a run anyway, give the date with `ahd run --date YYYY-MM-DD`.

## Profiles and config files

- **"Profile 'x' was not found"**: the file `~/.aa/orgs/x/config.json` does not exist. Check the name, or create the profile with `aa_auto_sdr --profile-add`.
- **"Profile name 'x' is not valid"**: a profile name holds only letters, digits, dots, dashes and underscores, and starts with a letter or a digit.
- **"is not valid UTF-8 JSON"**: the file has a syntax error, e.g., a missing comma or quote.
- **"must hold one JSON object"**: the file holds a list or a single value, not `{ ... }`.
- **"No credential was found"**: there is no profile, none of the variables `AHD_ORG_ID`, `AHD_CLIENT_ID`, `AHD_SECRET` and `AHD_SCOPES`, and no `config.json` in the current folder. See [Configuration](configuration.md).
- **"Use a profile or --config, not both"**: the command got a profile and `--config`. Remove `--profile` or `--config`. When the message says that the profile comes from the `AA_PROFILE` variable, remove `--config`, or unset the variable with `unset AA_PROFILE`. With both options on the command line, the command exits with 2 and prints its usage. With the variable, it exits with 1.
- **"Use --config or the four variables"**: all four of `AHD_ORG_ID`, `AHD_CLIENT_ID`, `AHD_SECRET` and `AHD_SCOPES` are set, and the command also got `--config`. The tool would have to ignore one of them, so it stops with exit code 1 before it signs in. Remove `--config` to use the variables, or unset them to use the file, e.g., `unset AHD_ORG_ID AHD_CLIENT_ID AHD_SECRET AHD_SCOPES`.
- **"Missing credential keys"**: one of `org_id`, `client_id`, `secret` or `scopes` is missing or empty. The message names the key and where the tool looked. When the tool read only the variables, the message names the variables instead, e.g., `AHD_SECRET, AHD_SCOPES`. Set the variables it names.

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

Three more messages name a file that is not usable. "holds no ahd runs" means the file is an empty DuckDB database. "is not an ahd database" means the file is a DuckDB database that another program made. The tool does not change it. "is not a DuckDB database" means the file is something else. Choose the right file with `--db`.

### The database is in use by another program

```
The database data/ahd.duckdb is in use by another program
```

Only one program can write to a DuckDB file at a time. While a program writes to it, no other program can open it. While a program reads it, nothing can write to it. Some examples:

- While `ahd run`, `ahd individual`, `ahd prune` or `ahd import` works on the file, wait for it to finish.
- A `duckdb` session that is still open on the file also blocks `ahd run`. Close it, then try again. `ahd runs`, `ahd render` and `ahd alerts` only read, so they work while a `duckdb -readonly` session is open.
- A scheduled `ahd alerts` or `send_alerts.py` that starts while `ahd run` still writes to the file stops with this message and exit code 1. Start it after `ahd run` has finished, as the jobs in [Run it every day](scheduling.md) do.

DuckDB's own message for this is "Could not set lock on file".

### An older duckdb cannot open the file

```
Trying to read a database file with version number 69, but we can only read versions between 64 and 68.
```

The `duckdb` command is older than 2.0. Update it, then check with `duckdb --version`. If an old version still shows, another copy comes first on your `PATH`. `which -a duckdb` lists every copy. See the [DuckDB guide](duckdb.md#before-you-start).

### A run stays "running"

A run that you stop with Ctrl-C is marked "failed". The requests that were already sent finish first, and the rest are not sent. A request that was waiting to try again is not tried again. The run gets the issue "The run stopped before this component was read." for each component that it did not read. Such a run stays "failed" when `ahd individual` adds a pair to it, so `ahd alerts` does not use it. Make a new run instead.

A run that stopped in another way, e.g., because you closed the window, keeps the status "running". Make a new run. To delete the old one, use `ahd prune --run N --force`.

### ahd alerts cannot read a run

`ahd alerts` reads only runs that ended "ok" or "partial". It says so in these cases:

- **"holds no finished run of"**: the database has no run of that report suite ID that ended "ok" or "partial", with the segment you chose or, without `--segment`, without a segment. Check the ID with `ahd runs`. `ahd alerts` takes the ID, not the name, and the case must match. The command still prints the alerts of the other suites, and exits with 1. With `--all-suites`, the message says that the database holds no finished run with that segment or without one.
- **"so it has no alerts"**: the run that `--run` names ended "failed". Choose another run.
- **"so its alerts are not complete"**: the run that `--run` names is still marked "running". Wait for it to finish, or see [A run stays "running"](#a-run-stays-running).
- **"--segment works only with --rsid or --all-suites"**: `--run` already names one run, with its own segment. Leave out `--segment`. The command exits with 2 and prints its usage, as for any wrong option.

## Email and Slack alerts

These messages come from `ahd alerts` and from the example scripts [`send_alerts.py`](email-alerts.md) and [`send_slack.py`](slack-alerts.md):

- **"The latest finished run of ... has the alerts of"**: the latest run of the suite that ended "ok" or "partial" is for a day before yesterday in the time zone of the suite. When the message goes on with "later runs of it failed", the runs after it failed. Run `ahd runs` to see them, and read the log of the daily job for the reason, e.g., a sign-in that failed. When no later run is named, ahd stored no run after it. This happens when the daily job stopped, when the sign-in failed, because ahd stores a run only after it signed in, or when a run stays "running". Read the log of the daily job to find which, and see [Run it every day](scheduling.md). The scripts send the warning also without alerts, and exit with 1.
- **"--test needs SMTP_HOST, MAIL_FROM and MAIL_TO"**: the email script needs these three settings to send a test message. The message names the ones that are missing. See [Email alerts](email-alerts.md#step-by-step).
- **"Sending needs MAIL_FROM and MAIL_TO when SMTP_HOST is set"**: the email script sends only to an address that you set. The message names the settings that are missing. Set them, or leave out `SMTP_HOST` to write `alerts.eml` instead. The script exits with 1. See [Email alerts](email-alerts.md#step-by-step).
- **"--test needs SLACK_WEBHOOK_URL"**: set the webhook URL of the channel. See [Slack alerts](slack-alerts.md#make-a-webhook).
- **"There is no finished run of ... in the database"**: you gave the scripts a report suite ID that has no run that ended "ok" or "partial". With `--segment`, the line says "with the segment" and the segment ID, and the run must have that segment. The email or the Slack message is then a warning, not an all-clear, also with `--always`. The scripts send the warning also when no suite has alerts, and also without `--always`. They then exit with 1. Check the ID with `ahd runs`.
- **"must hold only ASCII characters"**: the mail login of the email script takes only ASCII characters, which are the English letters without accents, the digits and the usual signs, and `SMTP_USER` or `SMTP_PASSWORD` holds another one, e.g., `ä`. The line holds no part of the password. Ask the mail provider for a password without such letters, e.g., an app password. The script exits with 1.
- **"The mail server does not offer STARTTLS"**: the email script turns on encryption with STARTTLS, and the server on `SMTP_HOST` and `SMTP_PORT` does not offer it. Check the port. Port 465 does not work. Use 587. The script exits with 1.
- **"The mail server does not offer a login (AUTH)"**: `SMTP_USER` is set, but the server does not take a login. Leave out `SMTP_USER` and `SMTP_PASSWORD` when the server needs no login, e.g., a relay on the local network. The script exits with 1.
- **"The mail server does not take an address that is not ASCII (SMTPUTF8)"**: `MAIL_FROM` or `MAIL_TO` holds a letter that is not ASCII, e.g., `ü`, and the server does not take such addresses. Use an ASCII address. The script exits with 1.
- **"--new-only needs ahd 0.1.16 or newer"**: the `ahd` that the script found does not know `--new-only`. Update it with `uv tool upgrade aa-health-duckdb`, or leave out `--new-only`.

## Getting more detail

Add `--verbose` before the command to write debug detail to stderr, e.g., each retry:

```
ahd --verbose run --rsid my_suite
```

The debug output never includes a credential value or a token.
