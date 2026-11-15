# Slack alerts

[README](../README.md) · [Usage](usage.md) · [Configuration](configuration.md) · [The report](report.md) · [How runs are stored](runs.md) · [DuckDB guide](duckdb.md) · [Run it every day](scheduling.md) · [Email alerts](email-alerts.md) · **Slack alerts** · [Troubleshooting](troubleshooting.md) · [Compared with the Excel workbook](excel-comparison.md) · [Development](development.md)

The example script [`examples/send_slack.py`](examples/send_slack.py) posts the latest alerts for one or more report suites to a Slack channel. It posts one message for each suite that has alerts. It posts nothing when no suite has alerts, so a quiet channel means healthy suites. It takes the same options as the [email script](email-alerts.md) and has the same exit codes.

## What a message holds

A message starts with the name of the suite and counts its alerts, e.g., "Adobe Store: 13 alerts, 1 new". A line below gives the report suite ID, the day of the alerts and the rules. Then a table lists the alerts, with the columns of the email: Alert, Component, ID, Weekday average, Actual, Change and Since. The new alerts come first, and the Since column says since when each alert has held, e.g., "since 2026-11-09, 3 days".

Slack does not show HTML, so the script cannot post the HTML table of the email. It uses Slack's own table instead. A chat message should stay short, so the table lists at most 10 alerts, and a line below counts the others, e.g., "3 more alerts are not listed." Slack also limits the text of a table, so a cell longer than 120 characters ends with "...". When an alert has no change, e.g., because its weekday average is 0, the Change cell says "Not known".

When a suite has a [suite-wide event](duckdb.md#suite_eventsrun-), the message starts with one line for the event, e.g., "356 of the 356 components that collected data had a value of 0 on 2026-11-11". Its alerts are not in the table. "Other alerts" and the table then list up to 10 alerts outside the event. `ahd alerts --every-alert` lists every alert. See [Print the alerts with ahd alerts](duckdb.md#print-the-alerts-with-ahd-alerts).

The names of components come from Adobe. The script posts them as plain text, so Slack shows them as they are. A name cannot mention a channel, make a link or change the formatting.
## Make a webhook

The script posts with an incoming webhook. A webhook is a URL that Slack makes for one channel. A message sent to the URL appears in that channel.

1. Make a Slack app for your workspace at [api.slack.com/apps](https://api.slack.com/apps), or use one your workspace already has. You may need an admin of the workspace to approve it.
2. Under **Incoming Webhooks**, turn them on, and add a webhook for the channel that should get the alerts.
3. Copy the webhook URL. It starts with `https://hooks.slack.com/`.

The URL is a secret. Anyone who has it can post to the channel. Keep it out of the repository and out of shared files. The script reads it from the variable `SLACK_WEBHOOK_URL` and never prints it, also not in an error message.

## Run it

The script needs `ahd` 0.1.13 or newer, and nothing else. It gets its alerts from `ahd alerts --format json`, as the email script does. Run it from a clone of this repository, where `uv run` already has `ahd`. Without a clone, download the file and run it with `ahd` added, e.g., `uv run --with aa-health-duckdb python send_slack.py ...`. The script looks for `ahd` in the Python that runs it first, then for the `ahd` program on your `PATH`. Without either, or with an older `ahd`, it stops with exit code 1 and says how to install it.

First run it without `SLACK_WEBHOOK_URL`. It then writes the messages to `slack.json` in the current folder and posts nothing:

```
uv run python docs/examples/send_slack.py ~/.aa/orgs/prod/ahd.duckdb my_suite
uv run python docs/examples/send_slack.py ~/.aa/orgs/prod/ahd.duckdb --all
```

`slack.json` holds a list with one message for each suite. To see how a message will look, paste one of them into Slack's [Block Kit Builder](https://app.slack.com/block-kit-builder). When it looks right, set the variable and run it again:

```sh
export SLACK_WEBHOOK_URL='https://hooks.slack.com/services/...'
uv run python docs/examples/send_slack.py ~/.aa/orgs/prod/ahd.duckdb --all
```

On Windows, use `set SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...` in place of `export`.

## Options

The options are those of the email script. [Email alerts](email-alerts.md#options) explains each of them.

- `RSID` or `--all`: the report suites. `--all` uses every suite that has a finished run in the file.
- `--segment ID`: the latest runs with this segment. Without it, the latest runs without a segment.
- `--threshold` and `--spike-threshold`: the large drop and the large spike percent, as shares. The defaults are `0.30` and `0.50`.
- `--anomaly-method` and `--anomaly-sensitivity`: the method that flags the unusual values, as on `ahd run`. Without them, `ahd alerts` reads `AHD_ANOMALY_METHOD` and `AHD_ANOMALY_SENSITIVITY`.

## When Slack does not take a message

The script waits at most 30 seconds for Slack, and about 1 second between two messages, because Slack takes about one message per second on a webhook. When Slack does not take a message, the script prints one line that says why, posts no more messages and exits with 1. The line says how many messages it posted before, e.g.:

- "Slack refused the message (404 no_service). Check SLACK_WEBHOOK_URL. Posted 0 of 2 messages." The webhook was removed, or the URL is wrong.
- "Slack refused the message (410 channel_is_archived). ..." The channel of the webhook was archived.
- "Slack asked the script to send fewer messages (429). Try again later."
- "SLACK_WEBHOOK_URL must start with https://." The script does not send the URL over a connection without encryption.

## Exit codes

- 0 when it posted or wrote the messages
- 1 when something is wrong, e.g., the file holds no finished run of a suite. The alerts of the other suites are still posted. A wrong option also exits with 1 and prints the usage. So does a message that Slack did not take.
- 2 when there was nothing to send

To post the alerts every morning, put the script in the daily job of [Run it every day](scheduling.md) in place of the email script, or next to it.
