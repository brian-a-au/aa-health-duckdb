# Run it every day

[README](../README.md) · [Usage](usage.md) · [Configuration](configuration.md) · [The report](report.md) · [How runs are stored](runs.md) · [DuckDB guide](duckdb.md) · **Run it every day** · [Troubleshooting](troubleshooting.md) · [Compared with the Excel workbook](excel-comparison.md) · [Development](development.md)

This page shows how to check every report suite each morning and get one email with the alerts. The schedule runs two commands:

1. `ahd run --all-suites` checks every suite the credential can see. It stores one run and writes one report for each suite.
2. [`docs/examples/send_alerts.py`](examples/send_alerts.py) emails the alerts of the latest runs. It sends nothing when no suite has alerts.

These steps run from a clone of this repository, because the email script is in it. [Development](development.md#work-from-a-clone) shows how to make one. The commands start with `uv run` for that reason.

## Before you start

- **Use a profile.** With `--profile prod`, the tool reads the credential from `~/.aa/orgs/prod/config.json` and keeps the database in `~/.aa/orgs/prod/ahd.duckdb`. Both paths stay the same, whatever folder the schedule starts in. See [Configuration](configuration.md#profiles).
- **Run both commands once by hand** from the project folder. Fix any problem before you schedule them.

  ```
  uv run ahd run --profile prod --all-suites
  uv run python docs/examples/send_alerts.py ~/.aa/orgs/prod/ahd.duckdb --all
  ```

- **Check the email first.** Without `SMTP_HOST`, the script writes `alerts.eml` in the current folder. Open it, and set the mail settings when it looks right. See [Send an alert email every morning](duckdb.md#send-an-alert-email-every-morning).
- **Keep the reports where you want them.** Reports go to `./reports/` in the folder the schedule starts in. To choose another folder, add `--out`, e.g., `--out ~/ahd-reports`.

The script with the commands holds your mail password when your server needs a login. Only your user account should be able to read it. The steps below show how.

## What happens when a suite fails

`ahd run` checks the other suites when one fails. Its exit code is then 1. The schedule below sends the email anyway, so the alerts of the other suites still reach you. The run's output, which the schedule keeps in a log file, names the suite that failed.

The email uses the latest run of each suite that ended "ok" or "partial". For a suite whose run failed today, that is an older run. Each section of the email gives the date of its alerts, so you can see this.

## macOS and Linux

Save the commands in a script, e.g., `~/aa-health-duckdb/daily.sh`. Change the paths and the mail settings to yours:

```sh
#!/bin/sh
cd "$HOME/aa-health-duckdb" || exit 1
export SMTP_HOST=smtp.example.com
export MAIL_FROM=ahd@example.com
export MAIL_TO=team@example.com
export SMTP_USER=ahd@example.com
export SMTP_PASSWORD='your mail password'
UV="$HOME/.local/bin/uv"
"$UV" run ahd run --profile prod --all-suites
"$UV" run python docs/examples/send_alerts.py "$HOME/.aa/orgs/prod/ahd.duckdb" --all
```

- A scheduled job does not read your shell settings, so it may not find `uv` by name. The script gives its full path. Run `command -v uv` to see where `uv` is on your computer, e.g., `/opt/homebrew/bin/uv` on a Mac with Homebrew.
- Make the script readable only by you: `chmod 700 ~/aa-health-duckdb/daily.sh`.

### With cron

Run `crontab -e` and add this line. It starts the script at 7:30 every morning and adds its output to a log file:

```
30 7 * * * /bin/sh "$HOME/aa-health-duckdb/daily.sh" >> "$HOME/ahd-daily.log" 2>&1
```

cron does not run a job while the computer sleeps or is off. On a Mac, use launchd instead, as shown next.

### With launchd on a Mac

launchd runs a job that it missed when the Mac was asleep, as soon as the Mac wakes. It does not run a job that it missed when the Mac was off. That job waits for the next 7:30. Save this file as `~/Library/LaunchAgents/local.ahd.daily.plist`. Change `you` to your user name:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>local.ahd.daily</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/sh</string>
    <string>/Users/you/aa-health-duckdb/daily.sh</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>7</integer>
    <key>Minute</key>
    <integer>30</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>/Users/you/ahd-daily.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/you/ahd-daily.log</string>
</dict>
</plist>
```

Then load it:

```
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.ahd.daily.plist
```

To start it once now and check the log, run `launchctl kickstart gui/$(id -u)/local.ahd.daily`. To remove the job, run `launchctl bootout gui/$(id -u)/local.ahd.daily`.

## Windows

Save the commands in a file, e.g., `C:\Users\you\aa-health-duckdb\daily.cmd`. Change the paths and the mail settings to yours:

```bat
@echo off
cd /d "%USERPROFILE%\aa-health-duckdb" || exit /b 1
set SMTP_HOST=smtp.example.com
set MAIL_FROM=ahd@example.com
set MAIL_TO=team@example.com
set SMTP_USER=ahd@example.com
set SMTP_PASSWORD=your mail password
set UV=%USERPROFILE%\.local\bin\uv.exe
(
  "%UV%" run ahd run --profile prod --all-suites
  "%UV%" run python docs\examples\send_alerts.py "%USERPROFILE%\.aa\orgs\prod\ahd.duckdb" --all
) >> "%USERPROFILE%\ahd-daily.log" 2>&1
```

- A scheduled task may not find `uv` by name, so the file gives its full path. The uv installer puts `uv.exe` in `%USERPROFILE%\.local\bin` by default. Run `where uv` in Command Prompt to see where it is on your computer.
- The file is in your user folder. By default, other users of the computer cannot read it, except administrators.
- The part in brackets adds the output of both commands to `ahd-daily.log` in your user folder.

Open Command Prompt and create a task that starts the file at 7:30 every morning:

```
schtasks /Create /TN "ahd daily" /SC DAILY /ST 07:30 /TR "%USERPROFILE%\aa-health-duckdb\daily.cmd"
```

- The task runs only while you are signed in to Windows. To run it when you are not signed in, open Task Scheduler, open the task, and choose "Run whether user is logged on or not".
- If the path has a space in it, put it in quotes inside the quotes, e.g., `/TR "\"C:\My Files\daily.cmd\""`.
- To start it once now and check the log, run `schtasks /Run /TN "ahd daily"`. To remove it, run `schtasks /Delete /TN "ahd daily"`.

## Check that it works

- Read the log file after the first scheduled run. Each suite has a line such as "Run 12 for my_suite finished with status: ok". The last line from `ahd run` counts the suites that ended "ok", "partial" and "failed". The lines from `send_alerts.py` come after it. When the credential sees only one suite, `ahd run` does not print the count line.
- Run `uv run ahd runs --profile prod` to list the stored runs.
- The database grows by one run for each suite each day. To delete old runs, see [Deleting runs](runs.md#deleting-runs). In a schedule, add `--yes` to `ahd prune`, because there is no input to read the answer from.
