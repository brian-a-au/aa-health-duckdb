"""Post the latest alerts for one or more report suites to Slack.

Usage:
    uv run python docs/examples/send_slack.py DATABASE RSID [RSID ...] [--segment ID]
        [--threshold 0.30] [--spike-threshold 0.50]
        [--anomaly-method adobe] [--anomaly-sensitivity normal] [--new-only] [--always]
    uv run python docs/examples/send_slack.py DATABASE --all [--segment ID]
    uv run python docs/examples/send_slack.py --test

It needs ahd 0.1.13 or newer, from the package aa-health-duckdb. Run it from a clone of the
aa-health-duckdb repository, where uv run already has ahd. Without a clone, download this file
and run it with ahd added, e.g., uv run --with aa-health-duckdb python send_slack.py ...
It gets its alerts from the command ahd alerts --format json, which holds the rules, the
anomaly methods and the variables below. The script looks for ahd in the Python that runs it
first, then for the ahd program on PATH. It needs nothing else, only Python itself.

It reads the latest run of each suite that ended "ok" or "partial". It takes report suite IDs
and a segment ID, not names. It posts one message for each suite that has alerts, a warning
or a suite-wide event, and nothing on a day without alerts unless --always asks for it. A
message has a table with the columns of the email of send_alerts.py. It lists the alerts that
are new on the day first, and says since when each alert has held, e.g., "since 2026-11-04,
8 days".
A chat message should stay short, so the table lists at most 10 alerts and the message counts
the others. ahd alerts lists them all.

When many components of a suite have the same kind of alert, ahd alerts calls them a
suite-wide event, e.g., when the data of the whole suite stopped. A message then starts with
one line for each event, e.g., "356 of the 356 components that collected data had a value of 0
on 2026-11-11". The table lists the other alerts, under "Other alerts".

The options are those of send_alerts.py. --all uses every suite that has a finished run in the
database. Without --segment it uses the latest runs without a segment. --threshold is the large
drop percent as a share, e.g., 0.30 for 30 percent, the default. --spike-threshold is the large
spike percent as a share, e.g., 0.50 for 50 percent, the default. --anomaly-method and
--anomaly-sensitivity choose the method that flags the unusual values, as on ahd run. Without
them, ahd alerts reads the variables AHD_ANOMALY_METHOD and AHD_ANOMALY_SENSITIVITY.

--new-only lists only the alerts that are new on the day. A suite-wide event that started
earlier keeps its line. It needs ahd 0.1.16 or newer. With ahd 0.1.16 or newer, a suite whose
latest finished run is old gets a warning in its message, also without alerts, and the script
then exits with 1. --always posts a short all-clear message when there are no alerts, so that a
quiet channel means that the job did not run. --test posts a test message to check the webhook.
It reads no alerts and does not need ahd.

To post, make an incoming webhook in Slack for the channel, and set SLACK_WEBHOOK_URL to its
URL. The URL is a secret: anyone who has it can post to the channel. The script never prints
it. Without SLACK_WEBHOOK_URL, it writes the messages to slack.json in the current folder
instead, so you can check them first. The script waits at most 30 seconds for Slack. When Slack
does not take a message, it prints one line that says why, posts no more messages and exits
with 1.

Exit codes, so a scheduler can tell the cases apart:
    0  it posted or wrote the messages
    1  something is wrong, e.g., a suite has no finished run, or the latest finished run of a
       suite is old. The other suites and the warnings are still posted. A wrong option also
       exits with 1 and prints the usage. So does a message that Slack did not take.
    2  there were no alerts, and --always was not given
"""

from __future__ import annotations

import argparse
import http.client
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation
from importlib import metadata
from pathlib import Path

HEADERS = ["Alert", "Component", "ID", "Weekday average", "Actual", "Change", "Since"]
# The table of a message lists at most this many alerts outside a suite-wide event.
SHOWN = 10

# Slack's limits: a section holds at most 3000 characters and a header at most 150. The cells
# of the tables of a message hold at most 10,000 characters together. With the header row and
# SHOWN rows of 7 cells, 120 characters a cell stays below it.
SECTION_LIMIT = 3000
HEADER_LIMIT = 150
# Slack takes at most 50 blocks in a message. A list of suites gets at most MAX_SECTIONS
# sections, so with its header it stays below that.
MAX_SECTIONS = 45
CELL_LIMIT = 120

# How long the script waits for Slack, in seconds, and how long between two messages. Slack
# takes about one message per second on a webhook.
SLACK_TIMEOUT = 30
PAUSE = 1.0

# The exit codes of ahd alerts.
AHD_ALERTS, AHD_ERROR, AHD_USAGE, AHD_NO_ALERTS = 0, 1, 2, 4

NEEDS_AHD = (
    "This script needs ahd 0.1.13 or newer, from the package aa-health-duckdb. Install it, "
    "e.g., with uv tool install aa-health-duckdb, or run the script with it added, e.g., "
    "uv run --with aa-health-duckdb python send_slack.py."
)
OLD_AHD = (
    "The ahd that this script found has no alerts command, so it is older than 0.1.11. "
    "Update it, e.g., with uv tool upgrade aa-health-duckdb."
)
NEW_ONLY_NEEDS = (
    "--new-only needs ahd 0.1.16 or newer. Update it, e.g., with uv tool upgrade aa-health-duckdb."
)


def _number(v: float | None) -> str:
    return "No data" if v is None else f"{v:,.0f}"


def _round(v: float | None, digits: int = 0) -> float | None:
    """v rounded as DuckDB's round() does it: half away from zero, e.g., 2.5 to 3."""
    if v is None or not math.isfinite(v):
        return v
    scale = 10.0**digits
    scaled = v * scale
    whole = math.floor(abs(scaled))
    if abs(scaled) - whole >= 0.5:
        whole += 1
    return math.copysign(whole, scaled) / scale


def _share(text: str, top: float = 1.0) -> str:
    """An argparse type for a share from above 0 to top. It keeps the text, so that ahd alerts
    gets the exact share, see _percent_text()."""
    try:
        value = float(text)
        _percent_text(text)
    except (ValueError, InvalidOperation):
        value = -1.0
    if not 0 < value <= top:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not a share between 0 and {top:g}. Write 0.30 for 30 percent."
        )
    return text


def _spike_share(text: str) -> str:
    return _share(text, 2.0)


def _percent(share: float) -> str:
    """The share as a percent, as ahd alerts writes it, e.g., 0.275 as "27.5%"."""
    return f"{share * 100:g}%"


def _percent_text(share: str) -> str:
    """The share as a percent, written as a decimal, e.g., "0.275" as "27.5".

    It moves the decimal point and does not round, so ahd alerts gets exactly the share that
    the option gave.
    """
    sign, digits, exponent = Decimal(share.strip()).as_tuple()
    return format(Decimal((sign, digits, int(exponent) + 2)), "f")


class _Parser(argparse.ArgumentParser):
    """Exits with 1 on a wrong option, not argparse's 2, which here means "no alerts"."""

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: error: {message}\n")


def _parser() -> argparse.ArgumentParser:
    p = _Parser(description="Post the latest ahd alerts for report suites to Slack.")
    p.add_argument("database", nargs="?", help="The ahd DuckDB file.")
    p.add_argument("rsids", nargs="*", metavar="RSID", help="A report suite ID.")
    p.add_argument("--all", action="store_true", help="Every suite with a finished run.")
    p.add_argument(
        "--threshold",
        type=_share,
        default="0.30",
        help="The large drop percent as a share (default 0.30).",
    )
    p.add_argument(
        "--spike-threshold",
        type=_spike_share,
        default="0.50",
        help="The large spike percent as a share, up to 2 (default 0.50).",
    )
    p.add_argument("--segment", help="Use the latest runs with this segment ID.")
    # ahd alerts checks these two, and reads the variables when they are not given.
    p.add_argument(
        "--anomaly-method",
        metavar="METHOD",
        help="The method that flags the unusual values (default AHD_ANOMALY_METHOD, else adobe).",
    )
    p.add_argument(
        "--anomaly-sensitivity",
        metavar="LEVEL",
        help="How wide the range of a local method is. It does not apply to adobe "
        "(default AHD_ANOMALY_SENSITIVITY, else normal).",
    )
    p.add_argument(
        "--new-only",
        action="store_true",
        help="List only the alerts that are new on the day. Needs ahd 0.1.16 or newer.",
    )
    p.add_argument(
        "--always",
        action="store_true",
        help="Post a short all-clear message when there are no alerts.",
    )
    p.add_argument(
        "--test",
        action="store_true",
        help="Post a test message to check the webhook. It reads no alerts.",
    )
    return p


def _ahd_command() -> list[str] | None:
    """How to start ahd: from the Python that runs this script, else the ahd program on PATH.

    The first keeps the script and ahd at the same version in a clone of the repository.
    """
    try:
        metadata.version("aa-health-duckdb")
    except metadata.PackageNotFoundError:
        found = shutil.which("ahd")
        return [found] if found else None
    return [sys.executable, "-m", "ahd.cli"]


def _ahd_options(args: argparse.Namespace) -> list[str]:
    """The options of ahd alerts for the options of this script. Each value follows its option
    after "=", so that a value that starts with a dash is not read as an option."""
    options = [f"--db={args.database}", "--format=json"]
    options += [f"--change-alert={_percent_text(args.threshold)}"]
    options += [f"--spike-alert={_percent_text(args.spike_threshold)}"]
    if args.all:
        options.append("--all-suites")
    options += [f"--rsid={rsid}" for rsid in args.rsids]
    if args.segment is not None:
        options.append(f"--segment={args.segment}")
    if args.anomaly_method is not None:
        options.append(f"--anomaly-method={args.anomaly_method}")
    if args.anomaly_sensitivity is not None:
        options.append(f"--anomaly-sensitivity={args.anomaly_sensitivity}")
    if args.new_only:
        options.append("--new-only")
    return options


def _flagged_by(method: str, label: str, sensitivity: str) -> str:
    """The sentences that say which method flagged the unusual values. label is the name of
    the method that ahd alerts gives, e.g., "Weekday median"."""
    if method == "adobe":
        return "Adobe's anomaly detection flagged the unusual values."
    level = "" if sensitivity == "normal" else f" with {sensitivity} sensitivity"
    return (
        f"The {label} method{level} flagged the unusual values. ahd computes this method from "
        "the daily values of the run."
    )


def _since(a: dict) -> str:
    """Since when an alert has held, e.g., "since 2026-11-04, 8 days" or "new on 2026-11-11"."""
    if a["is_new"]:
        return f"new on {a['since']}"
    days = "1 day" if a["days"] == 1 else f"{a['days']} days"
    if a["at_least"]:
        return f"since {a['since']} or earlier, at least {days}"
    return f"since {a['since']}, {days}"


def _cells(a: dict) -> list[str]:
    """One alert as the cells of a table row, in the order of HEADERS."""
    pct = None if a["pct_vs_usual"] is None else _round(a["pct_vs_usual"] * 100, 1)
    return [
        a["alert"],
        a["name"],
        a["component_id"],
        _number(_round(a["usual"])),
        _number(a["value"]),
        "Not known" if pct is None else f"{pct:+.1f}%",
        _since(a),
    ]


def _mrkdwn(text: str) -> str:
    """Text for a field that Slack reads as markup. Slack asks for &, < and > as entities, so
    that a name from Adobe cannot mention a channel or make a link."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _plain(text: str, limit: int = SECTION_LIMIT) -> dict:
    """A plain text object. Slack shows it as it is, without markup, links or mentions."""
    return {"type": "plain_text", "text": _cut(text, limit), "emoji": False}


def _count(n: int, one: str, many: str) -> str:
    return one if n == 1 else many.format(n)


def _sections(lines: list[str], one: str, many: str) -> list[dict]:
    """Section blocks that hold the lines, each with whole lines and at most SECTION_LIMIT
    characters. A line longer than that is cut. Past MAX_SECTIONS sections, the last section
    ends with a line that counts the lines left out: one for a single line, e.g., "1 more suite
    has no alerts.", else many with the count, e.g., "{} more suites have no alerts."."""
    groups: list[list[str]] = []
    size = 0
    for n, line in enumerate(lines):
        if groups and size + 1 + len(line) <= SECTION_LIMIT:
            groups[-1].append(line)
            size += 1 + len(line)
            continue
        if len(groups) == MAX_SECTIONS:
            last, left = groups[-1], len(lines) - n
            while last and len("\n".join([*last, _count(left, one, many)])) > SECTION_LIMIT:
                last.pop()
                left += 1
            last.append(_count(left, one, many))
            break
        groups.append([line])
        size = len(line)
    return [{"type": "section", "text": _plain("\n".join(group))} for group in groups]


def _note(text: str) -> dict:
    return {"type": "context", "elements": [_plain(text)]}


def _cell(text: str) -> str:
    """The text of a cell. Slack refuses an empty text, e.g., for a component without a name."""
    return _cut(text, CELL_LIMIT) if text else "(empty)"


def _table(alerts: list[dict]) -> dict:
    """A table block with a header row and one row for each alert. Its cells are raw text, which
    Slack shows as it is, so a name from Adobe stays plain text."""
    rows = [HEADERS, *(_cells(a) for a in alerts)]
    return {
        "type": "table",
        "column_settings": [{"is_wrapped": True}] * 3
        + [{"align": "right"}] * 3
        + [{"is_wrapped": True}],
        "rows": [[{"type": "raw_text", "text": _cell(c)} for c in row] for row in rows],
    }


def message(run: dict, rules: str, new_only: bool = False) -> dict:
    """The Slack message for one run with alerts, a warning or a suite-wide event. ahd alerts
    gives the new alerts first.

    A warning of an old run comes first. Each suite-wide event gets one line. A table lists the
    other alerts, at most SHOWN. new_only says that the run holds only the new alerts."""
    alerts = run["alerts"]
    name = run["rsid_name"] or run["rsid"]
    if not alerts:
        heading = f"{name}: no {'new ' if new_only else ''}alerts"
    elif new_only:
        heading = f"{name}: {len(alerts)} new alerts"
    else:
        heading = f"{name}: {len(alerts)} alerts, {sum(1 for a in alerts if a['is_new'])} new"
    blocks: list[dict] = [
        {"type": "header", "text": _plain(heading, HEADER_LIMIT)},
        _note(f"Report suite {run['rsid']}, alerts of {run['day']}. {rules}"),
    ]
    if run.get("warning"):
        blocks.append({"type": "section", "text": _plain(f"Warning: {run['warning']}")})
    events = run["events"]
    for event in events:
        said = f"Suite-wide event: {event['text']}"
        if not any(a["event"] == event["event"] for a in alerts):
            # With new_only: the event holds since an earlier day.
            said += f" None of its alerts are new on {run['day']}."
        blocks.append({"type": "section", "text": _plain(said)})
    others = [a for a in alerts if not events or a["event"] is None]
    if others:
        if events:
            blocks.append({"type": "section", "text": _plain("Other alerts")})
        blocks.append(_table(others[:SHOWN]))
        if len(others) > SHOWN:
            blocks.append(_note(f"{len(others) - SHOWN} more alerts are not listed."))
    if events or len(others) > SHOWN:
        blocks.append(_note("ahd alerts --every-alert lists every alert."))
    # Slack shows the text in the notification, and reads it as markup.
    if alerts:
        new = "new " if new_only else ""
        text = (
            f"{len(alerts)} {new}Analytics Health alerts for {name} ({run['rsid']}) on {run['day']}"
        )
    elif run.get("warning"):
        text = f"Analytics Health warning for {name} ({run['rsid']})"
    else:
        # With new_only: only suite-wide events that hold since an earlier day.
        text = f"Analytics Health: a suite-wide event goes on in {name} ({run['rsid']})"
    return {"text": _mrkdwn(text), "blocks": blocks}


def missing_line(rsid: str) -> str:
    """The line for a suite that has no finished run in the database."""
    return (
        f"There is no finished run of {rsid} in the database. Check the report suite ID, or run "
        "ahd runs to list the runs."
    )


def _missing(args: argparse.Namespace, runs: list[dict]) -> list[str]:
    """The suites that were asked for and have no finished run."""
    found = {run["rsid"] for run in runs}
    return [rsid for rsid in dict.fromkeys(args.rsids) if rsid not in found]


def missing_message(missing: list[str]) -> dict:
    """The message for the suites that have no finished run."""
    where = missing[0] if len(missing) == 1 else f"{len(missing)} report suites"
    warning = "warning" if len(missing) == 1 else "warnings"
    return {
        "text": _mrkdwn(f"Analytics Health {warning} for {where}"),
        "blocks": [
            {"type": "header", "text": _plain(f"Analytics Health {warning}", HEADER_LIMIT)},
            *_sections(
                [missing_line(r) for r in missing],
                "1 more suite has no finished run.",
                "{} more suites have no finished run.",
            ),
        ],
    }


def all_clear(runs: list[dict], new_only: bool, missing: list[str] | None = None) -> dict:
    """The short message for a day without alerts, with one line for each suite. A suite in
    missing has no finished run, so the message is then a warning and not an all-clear."""
    new = "new " if new_only else ""
    missing = missing or []
    lines = [missing_line(rsid) for rsid in missing] + [
        f"No {new}alerts for {run['rsid_name'] or run['rsid']} ({run['rsid']}) on {run['day']}."
        for run in runs
    ]
    if missing:
        payload = missing_message(missing)
        # The suites without a finished run come first, so the count can hold both kinds.
        payload["blocks"][1:] = _sections(
            lines, "1 more suite is not listed.", "{} more suites are not listed."
        )
        return payload
    where = runs[0]["rsid"] if len(runs) == 1 else f"{len(runs)} report suites"
    return {
        "text": _mrkdwn(f"No {new}Analytics Health alerts for {where}"),
        "blocks": [
            {"type": "header", "text": _plain(f"No {new}Analytics Health alerts", HEADER_LIMIT)},
            *_sections(
                lines,
                f"1 more suite has no {new}alerts.",
                "{} more suites have no " + new + "alerts.",
            ),
        ],
    }


def test_payload() -> dict:
    """The message of --test."""
    return {
        "text": "ahd can post Analytics Health alerts to this channel. This is a test message "
        "from send_slack.py."
    }


# The errors of a post. A ValueError, e.g., http.client.InvalidURL for a URL with a space,
# holds the URL in its text, so the script names only its type.
POST_ERRORS = (OSError, ValueError, http.client.HTTPException)


def _slack_problem(error: Exception) -> str:
    """One plain line about why Slack did not take a message. It never holds the URL."""
    if isinstance(error, urllib.error.HTTPError):
        if error.code == 429:
            return "Slack asked the script to send fewer messages (429). Try again later."
        try:
            said = error.read(200).decode("utf-8", "replace").strip()
        except OSError:
            said = ""
        # Slack answers with a short code, e.g., no_service or invalid_payload.
        code = f" {said}" if re.fullmatch(r"[a-z_]{1,64}", said) else ""
        return f"Slack refused the message ({error.code}{code}). Check SLACK_WEBHOOK_URL."
    if isinstance(error, TimeoutError):
        return f"Slack did not answer within {SLACK_TIMEOUT} seconds."
    return f"Could not reach Slack ({type(error).__name__}). Check SLACK_WEBHOOK_URL."


def _post(url: str, payload: dict) -> None:
    """Posts one message to the webhook. Raises one of POST_ERRORS when Slack does not take
    it."""
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=SLACK_TIMEOUT) as response:
        response.read()


def _warnings(n: int) -> str:
    return "the warning" if n == 1 else "the warnings"


def _events(n: int) -> str:
    return "the event message" if n == 1 else "the event messages"


def _send_test() -> int:
    """--test: posts the test message. It needs SLACK_WEBHOOK_URL."""
    url = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    if not url:
        print(
            "--test needs SLACK_WEBHOOK_URL. Set it to the webhook URL of the channel.",
            file=sys.stderr,
        )
        return 1
    if not url.startswith("https://"):
        print("SLACK_WEBHOOK_URL must start with https://.", file=sys.stderr)
        return 1
    try:
        _post(url, test_payload())
    except POST_ERRORS as error:
        print(_slack_problem(error), file=sys.stderr)
        return 1
    print("Posted a test message to Slack.")
    return 0


def _send(payloads: list[dict], count: int, what: str | None = None) -> bool:
    """Posts the messages, or writes slack.json without SLACK_WEBHOOK_URL. what names what they
    hold when they hold no alerts, e.g., "the all-clear message". False when Slack did not take
    a message."""
    url = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    if not url:
        Path("slack.json").write_text(json.dumps(payloads, indent=2) + "\n", encoding="utf-8")
        held = what or f"{len(payloads)} messages and {count} alerts"
        print(f"Wrote slack.json with {held}. Set SLACK_WEBHOOK_URL to post them.")
        return True
    if not url.startswith("https://"):
        print("SLACK_WEBHOOK_URL must start with https://.", file=sys.stderr)
        return False
    for n, payload in enumerate(payloads):
        if n:
            time.sleep(PAUSE)
        try:
            _post(url, payload)
        except POST_ERRORS as error:
            print(
                f"{_slack_problem(error)} Posted {n} of {len(payloads)} messages.",
                file=sys.stderr,
            )
            return False
    if what:
        print(f"Posted {what} to Slack.")
    else:
        print(f"Posted {len(payloads)} messages with {count} alerts to Slack.")
    return True


def main(argv: list[str]) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.test:
        return _send_test()
    if args.database is None:
        parser.error("give the ahd DuckDB file")
    if bool(args.rsids) == args.all:
        parser.error("give one or more report suite IDs, or --all, but not both")
    command = _ahd_command()
    if command is None:
        print(NEEDS_AHD, file=sys.stderr)
        return 1
    done = subprocess.run(
        [*command, "alerts", *_ahd_options(args)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if done.returncode == AHD_USAGE:
        if "invalid choice: 'alerts'" in done.stderr:
            print(OLD_AHD, file=sys.stderr)
            return 1
        if "unrecognized arguments: --new-only" in done.stderr:
            print(NEW_ONLY_NEEDS, file=sys.stderr)
            return 1
        # A wrong --anomaly-method or --anomaly-sensitivity: ahd names the values it takes.
        last = done.stderr.strip().splitlines()[-1] if done.stderr.strip() else ""
        parser.error(last.partition("error: ")[2] or last)
    sys.stderr.write(done.stderr)
    if done.returncode not in (AHD_ALERTS, AHD_ERROR, AHD_NO_ALERTS):
        return 1
    if done.returncode == AHD_ERROR and not done.stdout.strip():
        # e.g., the database is in use, or --all found no finished run. ahd said why.
        return 1
    try:
        runs = json.loads(done.stdout)["runs"]
    except (ValueError, KeyError, TypeError):
        print(
            "ahd alerts did not give its alerts as JSON. Run it by hand to see why.",
            file=sys.stderr,
        )
        return 1
    if any(run["alerts"] and "events" not in run for run in runs):
        print(NEEDS_AHD, file=sys.stderr)
        return 1
    payloads = []
    count = 0
    new = "new " if args.new_only else ""
    for run in runs:
        if not run["alerts"]:
            print(f"No {new}alerts for {run['rsid']}.")
        # ahd 0.1.15 and older give no warning. An event without new alerts, with --new-only,
        # still gets its message.
        if not (run["alerts"] or run.get("warning") or run["events"]):
            continue
        rules = (
            f"A large drop is more than {_percent(float(args.threshold))} below the weekday "
            f"average, and a large spike is more than {_percent(float(args.spike_threshold))} "
            "above it. The weekday average is the average of the same weekday in the 12 weeks "
            "before. " + _flagged_by(run["method"], run["method_name"], run["sensitivity"])
        )
        payloads.append(message(run, rules, args.new_only))
        count += len(run["alerts"])
    stale = any(run.get("stale") for run in runs)
    missing = _missing(args, runs)
    sent = False
    if payloads and missing:
        payloads.append(missing_message(missing))
    if payloads:
        if count:
            what = None
        elif missing or any(run.get("warning") for run in runs):
            what = _warnings(len(payloads))
        else:
            what = _events(len(payloads))
        if not _send(payloads, count, what):
            return 1
        sent = True
    elif runs and args.always:
        what = _warnings(len(missing)) if missing else "the all-clear message"
        if not _send([all_clear(runs, args.new_only, missing)], 0, what):
            return 1
        sent = True
    elif runs:
        print("Nothing to send.")
    if done.returncode == AHD_ERROR or stale:
        return 1
    return 0 if sent else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
