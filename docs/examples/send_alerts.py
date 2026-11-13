"""Email the latest alerts for one or more report suites, but only when there are any.

Usage:
    uv run python docs/examples/send_alerts.py DATABASE RSID [RSID ...] [--segment ID]
        [--threshold 0.30] [--spike-threshold 0.50]
        [--anomaly-method adobe] [--anomaly-sensitivity normal]
    uv run python docs/examples/send_alerts.py DATABASE --all [--segment ID]

It needs ahd 0.1.11 or newer, from the package aa-health-duckdb. Run it from a clone of the
aa-health-duckdb repository, where uv run already has ahd. Without a clone, download this file
and run it with ahd added, e.g., uv run --with aa-health-duckdb python send_alerts.py ...
Before 0.1.11, the script needed only DuckDB. It now gets its alerts from the command
ahd alerts --format json, which holds the rules, the anomaly methods and the variables below.
The script looks for ahd in the Python that runs it first, then for the ahd program on PATH.

It reads the latest run of each suite that ended "ok" or "partial". It takes report suite IDs
and a segment ID, not names. It builds one HTML email with a plain text part, with one section
for each suite that has alerts. Each section lists the alerts that are new on the day first,
and says since when each alert has held, e.g., "since 2026-11-04, 8 days". The Since column
needs ahd 0.1.12 or newer. With ahd 0.1.11 it stays empty.

When many components of a suite have the same kind of alert, ahd alerts calls them a
suite-wide event, e.g., when the data of the whole suite stopped. A section then starts with
one line for each event, e.g., "356 of the 356 components that collected data had a value of 0
on 2026-11-11", and lists only the first 5 alerts of the event. The other alerts follow under
"Other alerts". The subject still counts every alert. This needs ahd 0.1.13 or newer. With an
older ahd, the email lists every alert.

--all uses every suite that has a finished run in the database.
Without --segment it uses the latest runs without a segment. --threshold is the large drop
percent as a share, e.g., 0.30 for 30 percent, the default. --spike-threshold is the large spike
percent as a share, e.g., 0.50 for 50 percent, the default. The script gives them to ahd alerts
as the exact percent, e.g., 0.275 as 27.5.

--anomaly-method chooses the method that flags the unusual values, as on ahd run. The default
is adobe, which uses the flags that Adobe returned. weekday-median, weekday-iqr and
weekday-trend are computed by ahd from the daily values of the run. --anomaly-sensitivity is
low, normal or high, as on ahd run. The default is normal. It works only with a local method,
so with adobe, low or high stops the script with exit code 1. The email names the method, and
the sensitivity when it is not normal.

Without these two options, ahd alerts reads the variables AHD_ANOMALY_METHOD and
AHD_ANOMALY_SENSITIVITY, as ahd run does. Set them once in a scheduled job, so that the reports
and the email use the same method. An option wins over its variable, and an empty variable
counts as not set. An unknown value in a variable stops the script with exit code 1.

To send, set SMTP_HOST, SMTP_PORT (default 587), MAIL_FROM and MAIL_TO, and SMTP_USER and
SMTP_PASSWORD when the server needs a login. Without SMTP_HOST, it writes the message to
alerts.eml in the current folder instead, so you can open it and check it first.

Exit codes, so a scheduler can tell the cases apart:
    0  it sent or wrote a message
    1  something is wrong, e.g., a suite has no finished run. The other suites are still sent.
       A wrong option also exits with 1 and prints the usage.
    2  there were no alerts
"""

from __future__ import annotations

import argparse
import html
import json
import math
import os
import shutil
import smtplib
import subprocess
import sys
from decimal import Decimal, InvalidOperation
from email.message import EmailMessage
from importlib import metadata
from pathlib import Path

HEADERS = ["Alert", "Component", "ID", "Weekday average", "Actual", "Change", "Since"]
# The email lists this many alerts of each suite-wide event, as ahd alerts does.
EVENT_SHOWN = 5

# The exit codes of ahd alerts.
AHD_ALERTS, AHD_ERROR, AHD_USAGE, AHD_NO_ALERTS = 0, 1, 2, 4

NEEDS_AHD = (
    "This script needs ahd 0.1.11 or newer, from the package aa-health-duckdb. Install it, "
    "e.g., with uv tool install aa-health-duckdb, or run the script with it added, e.g., "
    "uv run --with aa-health-duckdb python send_alerts.py."
)
OLD_AHD = (
    "The ahd that this script found has no alerts command, so it is older than 0.1.11. "
    "Update it, e.g., with uv tool upgrade aa-health-duckdb."
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
    p = _Parser(description="Email the latest ahd alerts for report suites.")
    p.add_argument("database", help="The ahd DuckDB file.")
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
    """Since when an alert has held, e.g., "since 2026-11-04, 8 days" or "new on 2026-11-11".
    It is empty with ahd 0.1.11, which does not give it."""
    if "is_new" not in a:
        return ""
    if a["is_new"]:
        return f"new on {a['since']}"
    days = "1 day" if a["days"] == 1 else f"{a['days']} days"
    if a["at_least"]:
        return f"since {a['since']} or earlier, at least {days}"
    return f"since {a['since']}, {days}"


def _rows(run: dict) -> list[tuple]:
    """The rows of one suite's alerts for the email: alert, name, ID, weekday average, actual,
    change in percent, since when it held, whether it is new, its suite-wide event, and day.
    The new alerts come first. Then the alerts come by kind, and the large drops and spikes come
    with the largest drop first. The event is None outside an event, and with ahd 0.1.12 and
    older, which do not give it."""
    rows = []
    for a in run["alerts"]:
        pct = None if a["pct_vs_usual"] is None else _round(a["pct_vs_usual"] * 100, 1)
        usual = _round(a["usual"])
        new = bool(a.get("is_new"))
        rows.append(
            (a["alert"], a["name"], a["component_id"], usual, a["value"], pct, _since(a), new)
            + (a.get("event"), run["day"])
        )
    rows.sort(key=lambda r: (r[5] is None, r[5] or 0.0))
    rows.sort(key=lambda r: r[0], reverse=True)
    rows.sort(key=lambda r: not r[7])
    return rows


def _table(rows: list[tuple]) -> tuple[list[str], str]:
    """The plain text lines and the HTML table for one suite's alerts."""
    text = []
    cells = []
    for alert, name, cid, usual, value, pct, since, _new, _event, _day in rows:
        change = "" if pct is None else f"{pct:+.1f}%"
        shown = [_number(usual), _number(value)]
        line = f"- {alert}: {name} ({cid}), weekday average {shown[0]}, actual {shown[1]} {change}"
        text.append(f"{line}, {since}" if since else line)
        cells.append([alert, name, cid, *shown, change, since])
    th = "".join(f"<th style='text-align:left;padding:4px 8px'>{h}</th>" for h in HEADERS)
    trs = "".join(
        "<tr>"
        + "".join(f"<td style='padding:4px 8px'>{html.escape(str(c))}</td>" for c in row)
        + "</tr>"
        for row in cells
    )
    return text, f"<table style='border-collapse:collapse'><tr>{th}</tr>{trs}</table>"


def build(
    sections: list[tuple[str, str, list[tuple]]],
    threshold: float,
    spike: float,
    flagged_by: str,
    events: dict[str, list[dict]] | None = None,
) -> EmailMessage:
    """One email. Each section is (rsid, suite name, alert rows), for a suite with alerts.
    flagged_by is the sentence that names the anomaly method. events gives the suite-wide
    events of each suite by its rsid, as ahd alerts gives them. A section with events starts
    with each event and its first EVENT_SHOWN alerts, and then lists the other alerts."""
    events = events or {}
    total = sum(len(rows) for _, _, rows in sections)
    if len(sections) == 1:
        rsid, _name, rows = sections[0]
        subject = f"{total} Analytics Health alerts for {rsid} on {rows[0][-1]}"
    else:
        subject = f"{total} Analytics Health alerts for {len(sections)} report suites"
    rules = (
        f"A large drop is more than {threshold:.0%} below the weekday average, and a large "
        f"spike is more than {spike:.0%} above it. The weekday average is the average of the "
        f"same weekday in the 12 weeks before. {flagged_by}"
    )
    text = [subject, "", rules, ""]
    parts = [f"<p>{html.escape(subject)}. {html.escape(rules, quote=False)}</p>"]
    for rsid, name, rows in sections:
        heading = f"{name} ({rsid}), {rows[0][-1]}: {len(rows)} alerts"
        if any(r[6] for r in rows):
            heading += f", {sum(1 for r in rows if r[7])} new"
        text.append(heading)
        parts.append(f"<h3>{html.escape(heading)}</h3>")
        mine = events.get(rsid, [])
        for event in mine:
            said = f"Suite-wide event: {event['text']}"
            these = [r for r in rows if r[8] == event["event"]]
            lines, table = _table(these[:EVENT_SHOWN])
            text += [said, *lines]
            parts.append(f"<p>{html.escape(said, quote=False)}</p>{table}")
            # An event has at least 10 alerts, so at least 5 are left out.
            more = len(these) - EVENT_SHOWN
            if more > 0:
                line = (
                    f"{more} more alerts of this event are not listed. ahd alerts --every-alert "
                    "lists them all."
                )
                text.append(line)
                parts.append(f"<p>{html.escape(line, quote=False)}</p>")
        others = [r for r in rows if not mine or r[8] is None]
        if others:
            lines, table = _table(others)
            if mine:
                text.append("Other alerts:")
                parts.append("<p>Other alerts</p>")
            text += lines
            parts.append(table)
        text.append("")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.environ.get("MAIL_FROM", "ahd@localhost")
    msg["To"] = os.environ.get("MAIL_TO", "you@example.com")
    msg.set_content("\n".join(text).rstrip() + "\n")
    msg.add_alternative("".join(parts), subtype="html")
    return msg


def _send(msg: EmailMessage, count: int) -> None:
    host = os.environ.get("SMTP_HOST")
    if not host:
        Path("alerts.eml").write_bytes(bytes(msg))
        print(f"Wrote alerts.eml with {count} alerts. Set SMTP_HOST to send it.")
        return
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587"))) as smtp:
        smtp.starttls()
        if os.environ.get("SMTP_USER"):
            smtp.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
        smtp.send_message(msg)
    print(f"Sent {count} alerts to {msg['To']}.")


def main(argv: list[str]) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
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
    sections = []
    events = {}
    for run in runs:
        if run["alerts"]:
            sections.append((run["rsid"], run["rsid_name"] or run["rsid"], _rows(run)))
            # ahd 0.1.12 and older give no events.
            events[run["rsid"]] = run.get("events", [])
        else:
            print(f"No alerts for {run['rsid']}.")
    if sections:
        first = runs[0]
        said = _flagged_by(first["method"], first["method_name"], first["sensitivity"])
        msg = build(sections, float(args.threshold), float(args.spike_threshold), said, events)
        _send(msg, sum(len(rows) for _, _, rows in sections))
    elif runs:
        print("Nothing to send.")
    if done.returncode == AHD_ERROR:
        return 1
    return 0 if sections else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
