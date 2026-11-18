# Slack alerts

[README](../README.md) · [Usage](usage.md) · [Configuration](configuration.md) · [The report](report.md) · [How runs are stored](runs.md) · [DuckDB guide](duckdb.md) · [Run it every day](scheduling.md) · [Email alerts](email-alerts.md) · **Slack alerts** · [Troubleshooting](troubleshooting.md) · [Compared with the Excel workbook](excel-comparison.md) · [Development](development.md)

The example script [`examples/send_slack.py`](examples/send_slack.py) posts the latest alerts for one or more report suites to a Slack channel. It posts one message for each suite that has alerts. It posts nothing when no suite has alerts, so a quiet channel means healthy suites. It takes the same options as the [email script](email-alerts.md) and has the same exit codes.

## Step by step

These steps take you from nothing to a first message in Slack. The commands use the profile `prod` and the report suite ID `my_suite`. Put your own profile and suite ID in their place. The sections after this one explain each part in more detail.

1. **Install the tool and download the script.** You do not need a clone of this repository. Install `ahd` from PyPI, and download the one script file into a folder of your choice, e.g., `~/ahd`. The link holds a version, `v0.1.17`, so you get the script of that release:

   ```
   uv tool install aa-health-duckdb
   curl -fLO https://raw.githubusercontent.com/brian-a-au/aa-health-duckdb/v0.1.17/docs/examples/send_slack.py
   ```

   In Windows PowerShell, download it with `Invoke-WebRequest https://raw.githubusercontent.com/brian-a-au/aa-health-duckdb/v0.1.17/docs/examples/send_slack.py -OutFile send_slack.py`. You can also open the link in a browser and save the page as `send_slack.py`.

   The script needs `ahd` 0.1.13 or newer. If you installed `ahd` before, update it with `uv tool upgrade aa-health-duckdb`. If the terminal then says it cannot find `ahd`, run `uv tool update-shell` and open a new terminal. With pipx, install the tool with `pipx install aa-health-duckdb`, and run the script with `python3 send_slack.py` in place of `uv run python send_slack.py`.

   When you install a newer `ahd`, download the script again with the new version in the link, e.g., `v0.1.18`. `uv tool list` shows the version you have. The script and the tool then come from the same release.

   Run every command below in the folder that holds the script. To work from a clone instead, see [Run it](#run-it).

2. **Check the suite once.** The script posts the alerts of runs that are already in the database, so make a run first:

   ```
   ahd run --profile prod --rsid my_suite
   ```

   With a profile, the database is `~/.aa/orgs/prod/ahd.duckdb`. Without a profile, it is `./data/ahd.duckdb`.

3. **Find the report suite ID.** The script takes IDs, not names. List the stored runs and copy the ID from the report suite column:

   ```
   ahd runs --profile prod
   ```

   The script uses the latest run of the suite that has the status "ok" or "partial". A run that failed does not count.

4. **Write the messages to a file.** Run the script without `SLACK_WEBHOOK_URL`:

   ```
   uv run python send_slack.py ~/.aa/orgs/prod/ahd.duckdb my_suite
   ```

   It prints a line that counts the messages and the alerts. It writes `slack.json` in the current folder and posts nothing. When it prints "No alerts for my_suite." and "Nothing to send.", the latest run of the suite has no alerts. The script then writes no file and exits with 2. Use `--all` in place of `my_suite` to include every suite in the file.

5. **Look at a message.** Open `slack.json` in a text editor. It holds a list with one message for each suite. Copy one message, from its `{` to its matching `}`, and paste it into Slack's [Block Kit Builder](https://app.slack.com/block-kit-builder). The builder shows the message as Slack will show it.

6. **Make a Slack app.** Open [api.slack.com/apps](https://api.slack.com/apps) and sign in to your workspace. Choose **Create New App**, then **From scratch**. Give it a name, e.g., "Analytics alerts", and choose the workspace. You can also use an app your workspace already has.

7. **Add a webhook for the channel.** In the settings of the app, open **Incoming Webhooks** and turn them on. Choose **Add New Webhook to Workspace**, pick the channel that should get the alerts, and choose **Allow**. When your workspace needs an admin to approve apps, Slack asks the admin first, and the webhook works after the approval.

8. **Copy the webhook URL.** Slack lists it under **Webhook URLs for Your Workspace**. It starts with `https://hooks.slack.com/`. Keep it secret, because anyone who has it can post to the channel. See [Make a webhook](#make-a-webhook).

9. **Set the URL.** On macOS or Linux:

   ```sh
   export SLACK_WEBHOOK_URL='https://hooks.slack.com/services/...'
   ```

   On Windows, in the Command Prompt, write `set "SLACK_WEBHOOK_URL=https://hooks.slack.com/services/..."`, with the quotes around the name, the `=` and the URL. In a `.cmd` file, write each `%` of the URL as `%%`. In PowerShell, write `$env:SLACK_WEBHOOK_URL = 'https://hooks.slack.com/services/...'`. The setting lasts until you close the terminal.

10. **Check the webhook.** Post a test message:

    ```
    uv run python send_slack.py --test
    ```

    It prints "Posted a test message to Slack." and the message appears in the channel. It reads no alerts and needs no database. Without `SLACK_WEBHOOK_URL`, it says "--test needs SLACK_WEBHOOK_URL. Set it to the webhook URL of the channel." When Slack does not take the message, it prints one line that says why. `--test` needs a script from 0.1.16 or newer.

11. **Post the messages.** Run the same command as in step 4:

    ```
    uv run python send_slack.py ~/.aa/orgs/prod/ahd.duckdb my_suite
    ```

    It prints a line that counts the messages and the alerts, e.g., "Posted 2 messages with 20 alerts to Slack." with `--all` and two suites. The messages appear in the channel. When Slack does not take a message, the script prints one line that says why. See [When Slack does not take a message](#when-slack-does-not-take-a-message).

12. **Run it every day.** To post the alerts each morning, put the script in the daily job of [Run it every day](scheduling.md). Set `SLACK_WEBHOOK_URL` in the scheduled script, and make sure only your user account can read that script.

## What a message holds

A message starts with the name of the suite and counts its alerts, e.g., "Adobe Store: 13 alerts, 1 new". A line below gives the report suite ID, the day of the alerts and the rules. Then a table lists the alerts, with the columns of the email: Alert, Component, ID, Weekday average, Actual, Change and Since. The new alerts come first, and the Since column says since when each alert has held, e.g., "since 2026-11-09, 3 days".

Slack does not show HTML, so the script cannot post the HTML table of the email. It uses Slack's own table instead. A chat message should stay short, so the table lists at most 10 alerts, and a line below counts the others, e.g., "3 more alerts are not listed." Slack also limits the text of a table, so a cell longer than 120 characters ends with "...". When an alert has no change, e.g., because its weekday average is 0, the Change cell says "Not known".

When a suite has a [suite-wide event](duckdb.md#suite_eventsrun-), the message has one line for the event below the rules, e.g., "Suite-wide event: 356 of the 356 components that collected data had a value of 0 on 2026-11-11". Its alerts are not in the table. "Other alerts" and the table then list up to 10 alerts outside the event. `ahd alerts --every-alert` lists every alert. See [Print the alerts with ahd alerts](duckdb.md#print-the-alerts-with-ahd-alerts).

The names of components come from Adobe. The script posts them as plain text, so Slack shows them as they are. A name cannot mention a channel, make a link or change the formatting.

## Make a webhook

The script posts with an incoming webhook. A webhook is a URL that Slack makes for one channel. A message sent to the URL appears in that channel.

Steps 6 to 8 of [Step by step](#step-by-step) show how to make one. In short, make a Slack app at [api.slack.com/apps](https://api.slack.com/apps), turn on **Incoming Webhooks** for it, add a webhook for the channel, and copy its URL. The URL starts with `https://hooks.slack.com/`.

The URL is a secret. Anyone who has it can post to the channel. Keep it out of the repository and out of shared files. The script reads it from the variable `SLACK_WEBHOOK_URL` and never prints it, also not in an error message.

## Run it

The script needs `ahd` 0.1.13 or newer, and nothing else. It gets its alerts from `ahd alerts --format json`, as the email script does. You can run the script in two ways:

- Download only the script and install `ahd` from PyPI, as in [Step by step](#step-by-step). The script then uses the `ahd` program that `uv tool install` put on your `PATH`. To run it without installing `ahd`, add it to the command, e.g., `uv run --with aa-health-duckdb python send_slack.py ...`.
- Run it from a clone of this repository, where `uv run` already has `ahd`. The commands below are for a clone.

The script looks for `ahd` in the Python that runs it first, then for the `ahd` program on your `PATH`. Without either, or with an older `ahd`, it stops with exit code 1 and says how to install it.

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

On Windows, set the variable as step 9 of [Step by step](#step-by-step) shows.

## Options

The options are those of the email script. [Email alerts](email-alerts.md#options) explains each of them.

- `RSID` or `--all`: the report suites. `--all` uses every suite that has a finished run in the file.
- `--segment ID`: the latest runs with this segment. Without it, the latest runs without a segment. The header and the notification text then name the segment, e.g., "Adobe Store, segment s300_x: 3 alerts, 1 new".
- `--threshold` and `--spike-threshold`: the large drop and the large spike percent, as shares. The defaults are `0.30` and `0.50`.
- `--anomaly-method` and `--anomaly-sensitivity`: the method that flags the unusual values, as on `ahd run`. Without them, `ahd alerts` reads `AHD_ANOMALY_METHOD` and `AHD_ANOMALY_SENSITIVITY`.
- `--new-only`: lists only the alerts that are new on the day. The header then says "new", e.g., "Adobe Store: 2 new alerts". A suite-wide event keeps its line also when none of its alerts is new, so a suite whose data stopped days ago is still named every day. The notification text of such a message then says, e.g., "Analytics Health: a suite-wide event goes on in Adobe Store (my_suite)". It needs `ahd` 0.1.16 or newer. With an older `ahd`, the script stops with exit code 1 and says how to update it.
- `--always`: posts a short message when there are no alerts, e.g., "No Analytics Health alerts" with one line for each suite. Then a quiet channel no longer means healthy suites. It means that the daily job did not run, so you notice a job that stopped. When a report suite ID that you gave has no finished run, the message names it, e.g., "There is no finished run of my_suite in the database.", and is a warning, not an all-clear. The script posts this warning also when no suite has alerts, and also without `--always`. It then exits with 1.
- `--test`: posts a test message to check the webhook, see step 10 of [Step by step](#step-by-step). It does not read the database and does not need `ahd`.

### When the latest run is old

A run is old when its alerts are for a day before yesterday in the time zone of the suite, e.g., because the runs after it failed or the daily job stopped. Its message then has a warning below the note with the rules, e.g., "Warning: The latest finished run of my_suite has the alerts of 2026-11-13, but yesterday was 2026-11-16 in the time zone of the suite. 2 later runs of it failed." The message is posted also when the suite has no alerts, and the script then exits with 1. This needs `ahd` 0.1.16 or newer. With an older `ahd`, the message has no warning.

## When Slack does not take a message

The script waits at most 30 seconds for Slack, and about 1 second between two messages, because Slack takes about one message per second on a webhook. When Slack does not take a message, the script prints one line that says why, posts no more messages and exits with 1. The line says how many messages it posted before, e.g.:

- "Slack refused the message (404 no_service). Check SLACK_WEBHOOK_URL. Posted 0 of 2 messages." The webhook was removed, or the URL is wrong.
- "Slack refused the message (410 channel_is_archived). ..." The channel of the webhook was archived.
- "Slack asked the script to send fewer messages (429). Try again later."
- "Slack did not answer within 30 seconds."
- "Could not reach Slack (URLError). Check SLACK_WEBHOOK_URL." The computer cannot connect to Slack, e.g., because it is offline. The script names only the kind of error, because the text of the error can hold the URL.
- "SLACK_WEBHOOK_URL must start with https://." The script does not send the URL over a connection without encryption.

## Exit codes

- 0 when it posted or wrote the messages
- 1 when something is wrong, e.g., the file holds no finished run of a suite, or the latest finished run of a suite is old. The alerts of the other suites and the warnings are still posted. A wrong option also exits with 1 and prints the usage. So does a message that Slack did not take.
- 2 when there was nothing to send. With `--always`, the script posts the all-clear message and exits with 0 instead.

To post the alerts every morning, put the script in the daily job of [Run it every day](scheduling.md) in place of the email script, or next to it.
