"""Email the latest alerts for one report suite, but only when there are any.

Usage:
    uv run python docs/examples/send_alerts.py DATABASE RSID [THRESHOLD] [--segment ID]

It reads the latest finished run of the suite with the alerts() macro that every ahd
database holds, and builds an HTML email with a plain text part. Without --segment it uses
the latest run without a segment. THRESHOLD is the large drop percent as a share, e.g.
0.30 for 30 percent, the default.

To send, set SMTP_HOST, SMTP_PORT (default 587), MAIL_FROM and MAIL_TO, and SMTP_USER and
SMTP_PASSWORD when the server needs a login. Without SMTP_HOST, it writes the message to
alerts.eml in the current folder instead, so you can open it and check it first.

Exit codes, so a scheduler can tell the cases apart:
    0  it sent or wrote a message
    1  something is wrong, e.g. the database has no finished run of the suite
    2  there were no alerts
"""

from __future__ import annotations

import argparse
import html
import os
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path

import duckdb

RUN_QUERY = """
SELECT max(run_id) FROM latest_runs
WHERE rsid = $rsid AND segment_id IS NOT DISTINCT FROM $segment
"""

QUERY = """
FROM alerts($run, threshold := $threshold)
SELECT alert, name, component_id, round(usual) AS usual, value,
    round(pct_vs_usual * 100, 1) AS pct, day
ORDER BY alert DESC, pct
"""

HEADERS = ["Alert", "Component", "ID", "Usual", "Actual", "Change"]


def _number(v: float | None) -> str:
    return "No data" if v is None else f"{v:,.0f}"


def _share(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        value = -1.0
    if not 0 < value <= 1:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not a share between 0 and 1. Write 0.30 for 30 percent."
        )
    return value


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Email the latest ahd alerts for one report suite.")
    p.add_argument("database", help="The ahd DuckDB file.")
    p.add_argument("rsid", help="The report suite ID.")
    p.add_argument(
        "threshold",
        nargs="?",
        type=_share,
        default=0.30,
        help="The large drop percent as a share (default 0.30).",
    )
    p.add_argument("--segment", help="Use the latest run with this segment ID.")
    return p


def build(rows: list[tuple], rsid: str, threshold: float) -> EmailMessage:
    day = rows[0][-1]
    subject = f"{len(rows)} Analytics Health alerts for {rsid} on {day}"
    text = [subject, ""]
    cells = []
    for alert, name, cid, usual, value, pct, _day in rows:
        change = "" if pct is None else f"{pct:+.1f}%"
        shown = [_number(usual), _number(value)]
        text.append(f"- {alert}: {name} ({cid}), usual {shown[0]}, actual {shown[1]} {change}")
        cells.append([alert, name, cid, *shown, change])
    th = "".join(f"<th style='text-align:left;padding:4px 8px'>{h}</th>" for h in HEADERS)
    trs = "".join(
        "<tr>"
        + "".join(f"<td style='padding:4px 8px'>{html.escape(str(c))}</td>" for c in row)
        + "</tr>"
        for row in cells
    )
    body = (
        f"<p>{html.escape(subject)}. A large drop is more than {threshold:.0%} below the usual "
        f"value for the weekday.</p><table style='border-collapse:collapse'><tr>{th}</tr>{trs}"
        "</table>"
    )
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.environ.get("MAIL_FROM", "ahd@localhost")
    msg["To"] = os.environ.get("MAIL_TO", "you@example.com")
    msg.set_content("\n".join(text))
    msg.add_alternative(body, subtype="html")
    return msg


def _read(args: argparse.Namespace) -> tuple[int | None, list[tuple]]:
    """The run number and its alerts. The run is None when the suite has no finished run."""
    with duckdb.connect(args.database, read_only=True) as con:
        run = con.execute(RUN_QUERY, {"rsid": args.rsid, "segment": args.segment}).fetchone()[0]
        if run is None:
            return None, []
        params = {"run": run, "threshold": args.threshold}
        return run, con.execute(QUERY, params).fetchall()


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    if not Path(args.database).is_file():
        print(f"There is no database at {args.database}.", file=sys.stderr)
        return 1
    try:
        run, rows = _read(args)
    except duckdb.Error as e:
        # e.g. "Could not set lock on file" while ahd run writes to it.
        print(f"Could not read {args.database}: {e}", file=sys.stderr)
        return 1
    if run is None:
        which = f"with the segment {args.segment}" if args.segment else "without a segment"
        print(
            f"{args.database} holds no finished run of {args.rsid} {which}. Check the report "
            "suite ID, or run ahd runs to list the runs.",
            file=sys.stderr,
        )
        return 1
    if not rows:
        print(f"No alerts for {args.rsid} in run {run}. Nothing to send.")
        return 2
    msg = build(rows, args.rsid, args.threshold)
    host = os.environ.get("SMTP_HOST")
    if not host:
        Path("alerts.eml").write_bytes(bytes(msg))
        print(f"Wrote alerts.eml with {len(rows)} alerts. Set SMTP_HOST to send it.")
        return 0
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587"))) as smtp:
        smtp.starttls()
        if os.environ.get("SMTP_USER"):
            smtp.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
        smtp.send_message(msg)
    print(f"Sent {len(rows)} alerts to {msg['To']}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
