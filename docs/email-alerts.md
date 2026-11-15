# Email alerts

[README](../README.md) · [Usage](usage.md) · [Configuration](configuration.md) · [The report](report.md) · [How runs are stored](runs.md) · [DuckDB guide](duckdb.md) · [Run it every day](scheduling.md) · **Email alerts** · [Slack alerts](slack-alerts.md) · [Troubleshooting](troubleshooting.md) · [Compared with the Excel workbook](excel-comparison.md) · [Development](development.md)

The example script [`examples/send_alerts.py`](examples/send_alerts.py) emails the latest alerts for one or more report suites, with an HTML table and a plain text part. The email has one section for each suite that has alerts. It sends nothing when no suite has alerts. That way an empty inbox means healthy suites. To post the alerts to a Slack channel instead, see [Slack alerts](slack-alerts.md).

## Step by step

These steps take you from nothing to a first email. The commands use the profile `prod` and the report suite ID `my_suite`. Put your own profile and suite ID in their place. The sections after this one explain each part in more detail.

1. **Get the script.** Clone this repository and install the tool:

   ```
   git clone https://github.com/brian-a-au/aa-health-duckdb.git
   cd aa-health-duckdb
   uv sync
   ```

   Run every command below from the `aa-health-duckdb` folder. To use the script without a clone, see [Run it](#run-it).

2. **Check the suite once.** The script emails the alerts of runs that are already in the database, so make a run first:

   ```
   uv run ahd run --profile prod --rsid my_suite
   ```

   With a profile, the database is `~/.aa/orgs/prod/ahd.duckdb`. Without a profile, it is `./data/ahd.duckdb`.

3. **Find the report suite ID.** The script takes IDs, not names. List the stored runs and copy the ID from the report suite column:

   ```
   uv run ahd runs --profile prod
   ```

   The run must have the status "ok" or "partial". The script skips a run that failed.

4. **Write the email to a file.** Run the script without any mail settings:

   ```
   uv run python docs/examples/send_alerts.py ~/.aa/orgs/prod/ahd.duckdb my_suite
   ```

   It prints "Wrote alerts.eml with 13 alerts. Set SMTP_HOST to send it." and writes `alerts.eml` in the current folder. When it prints "Nothing to send.", the suite has no alerts today and there is no file to check. Use `--all` in place of `my_suite` to include every suite in the file.

5. **Open `alerts.eml`.** Double click it, or open it with your mail program, e.g., Apple Mail, Outlook or Thunderbird. Check that the table and the counts look right.

6. **Get the settings of your mail server.** You need these from your mail provider or your IT team:

   - The host name of the outgoing mail server, e.g., `smtp.example.com`.
   - The port. The script uses 587 unless you set another one. The server must accept STARTTLS on that port. Port 465 does not work.
   - The user name and password, when the server needs a login. Some providers want an app password here in place of your normal password, e.g., Gmail.
   - The address the email comes from, and the address it goes to. To send to several people, separate their addresses with commas in `MAIL_TO`.

7. **Set the mail settings.** On macOS or Linux:

   ```sh
   export SMTP_HOST='smtp.example.com'
   export SMTP_PORT='587'
   export SMTP_USER='alerts@example.com'
   export SMTP_PASSWORD='your password'
   export MAIL_FROM='alerts@example.com'
   export MAIL_TO='team@example.com'
   ```

   On Windows, in the Command Prompt, use `set` in place of `export` and leave out the quotes, e.g., `set SMTP_HOST=smtp.example.com`. In PowerShell, write `$env:SMTP_HOST = 'smtp.example.com'`. Leave out `SMTP_USER` and `SMTP_PASSWORD` when the server needs no login. The settings last until you close the terminal.

8. **Send the email.** Run the same command as in step 4:

   ```
   uv run python docs/examples/send_alerts.py ~/.aa/orgs/prod/ahd.duckdb my_suite
   ```

   It prints "Sent 13 alerts to team@example.com." When it cannot send, it prints one line that says why and sends nothing, e.g., "The mail server refused the login. Check SMTP_USER and SMTP_PASSWORD. Nothing was sent to smtp.example.com." Fix the setting and run it again.

9. **Run it every day.** To run the check and the email each morning, follow [Run it every day](scheduling.md). The scheduled script holds the same mail settings.

## What the email holds

Each section lists the alerts that are new on the day first, and its heading counts them, e.g., "Adobe Store (dgeo1xxpnwcidadobestore), 2026-11-11: 13 alerts, 1 new". Each alert says since when it has held, e.g., "since 2026-11-09, 3 days". The Since column needs `ahd` 0.1.12 or newer. With 0.1.11, it stays empty.

When a suite has a [suite-wide event](duckdb.md#suite_eventsrun-), its section starts with one line for the event, e.g., "356 of the 356 components that collected data had a value of 0 on 2026-11-11". A table of the first 5 alerts of the event follows, and a line counts the others. Then "Other alerts" lists the alerts outside the event. So when the data of a whole suite stops, the email has 5 rows for it, not 356. The subject still counts every alert. This needs `ahd` 0.1.13 or newer. With an older `ahd`, the email lists every alert.

## Run it

The script gets its alerts from `ahd alerts --format json`, and builds and sends the email itself. So it needs `ahd` 0.1.11 or newer. Before 0.1.11, it needed only DuckDB, and it read the file with its own copy of the rules for the anomaly options. The script is in this repository. Run it from a clone, as below, where `uv run` already has `ahd`. Without a clone, download the file and run it with `ahd` added, e.g., `uv run --with aa-health-duckdb python send_alerts.py ...`. The script looks for `ahd` in the Python that runs it first, then for the `ahd` program on your `PATH`. Without either, it stops with exit code 1 and says how to install it.

```
uv run python docs/examples/send_alerts.py ~/.aa/orgs/prod/ahd.duckdb my_suite
uv run python docs/examples/send_alerts.py ~/.aa/orgs/prod/ahd.duckdb my_suite other_suite
uv run python docs/examples/send_alerts.py ~/.aa/orgs/prod/ahd.duckdb --all
```

## Options

It reads the latest run of each suite without a segment, from `latest_runs`. `--all` uses every suite that has such a run in the file. Add `--segment ID` for the latest runs with that segment. The script takes only report suite IDs and segment IDs, not names, and they must match exactly. `--threshold` sets the large drop percent as a share, e.g., `--threshold 0.20` for 20 percent. The default is `0.30`. `--spike-threshold` sets the large spike percent the same way, up to `2`. The default is `0.50`. The script gives each share to `ahd alerts` as the exact percent, e.g., `0.275` as `--change-alert 27.5`, so the email lists the same alerts as before.

`--anomaly-method` chooses the anomaly method that flags the unusual values, and `--anomaly-sensitivity` sets how wide the range of a local method is. The script gives both to `ahd alerts`, so they take the same values as on `ahd run`, and the defaults are the same, `adobe` and `normal`. So by default the script uses Adobe's flags. With `adobe`, `low` or `high` stops the script with exit code 1 and the message of `ahd`, because Adobe returns its own range. The email says which method flagged the unusual values, and it names the sensitivity when it is not `normal`, e.g., "The Weekday median method with high sensitivity flagged the unusual values."

Without these two options, `ahd alerts` reads the variables `AHD_ANOMALY_METHOD` and `AHD_ANOMALY_SENSITIVITY`, as `ahd run` does. Set them once for a scheduled job, and the reports and the email use the same method. An option wins over its variable, and an empty variable counts as not set. A value that is not a method or a sensitivity stops the script with exit code 1, and the message names the variable. See [Set the anomaly method once](usage.md#set-the-anomaly-method-once).

`ahd alerts` uses the rules of the installed `ahd`, also on a file that an older version wrote, so every method and sensitivity works on any file. Before 0.1.11, the script used the macro in the file, and on a file that an older version had written last, it stopped and said to run `ahd run` once.

## Send it

Without `SMTP_HOST`, it writes `alerts.eml` in the current folder, which you can open and check first. To send, set `SMTP_HOST`, `MAIL_FROM` and `MAIL_TO`. Set `SMTP_PORT` when the server does not use 587. Set `SMTP_USER` and `SMTP_PASSWORD` when the server needs a login. The script connects without encryption and then turns it on with the STARTTLS command. It does not work with a server that needs encryption from the start, e.g., on port 465.

The script checks the certificate of the server and that it was made for the name in `SMTP_HOST`. It waits at most 60 seconds for an answer from the server. When it cannot send, e.g., the server refuses the login or `SMTP_PORT` is not a number, it prints one line that says why and exits with 1. The line never holds the password.

## Exit codes

It exits with these codes:

- 0 when it sent or wrote a message
- 1 when something is wrong, e.g., the file holds no finished run of a suite. The alerts of the other suites are still sent. A wrong option also exits with 1 and prints the usage. So does an email that the script could not send.
- 2 when there was nothing to send

To run the check and the email every morning on Windows, macOS or Linux, see [Run it every day](scheduling.md). The [DuckDB guide](duckdb.md#email-recipes) has SQL recipes for your own emails, e.g., only the alerts that are new, or each alert to the owner of its component.
