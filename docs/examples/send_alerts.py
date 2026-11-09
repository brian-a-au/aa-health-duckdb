"""Email the latest alerts for one or more report suites, but only when there are any.

Usage:
    uv run python docs/examples/send_alerts.py DATABASE RSID [RSID ...] [--threshold 0.30]
        [--spike-threshold 0.50]
    uv run python docs/examples/send_alerts.py DATABASE --all [--segment ID]

It reads the latest run of each suite that ended "ok" or "partial", with the alerts() macro
that every ahd database holds. It takes report suite IDs and a segment ID, not names. It
builds one HTML email with a plain text part, with one section for each suite that has
alerts. --all uses every suite that has a finished run in the database.
Without --segment it uses the latest runs without a segment. --threshold is the large drop
percent as a share, e.g. 0.30 for 30 percent, the default. --spike-threshold is the large spike
percent as a share, e.g. 0.50 for 50 percent, the default.

To send, set SMTP_HOST, SMTP_PORT (default 587), MAIL_FROM and MAIL_TO, and SMTP_USER and
SMTP_PASSWORD when the server needs a login. Without SMTP_HOST, it writes the message to
alerts.eml in the current folder instead, so you can open it and check it first.

Exit codes, so a scheduler can tell the cases apart:
    0  it sent or wrote a message
    1  something is wrong, e.g. a suite has no finished run. The other suites are still sent.
       A wrong option also exits with 1 and prints the usage.
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

# Every suite with a finished run, with the segment or without one, by name.
ALL_QUERY = """
SELECT rsid FROM latest_runs
WHERE segment_id IS NOT DISTINCT FROM $segment
ORDER BY lower(rsid_name), rsid
"""

NAME_QUERY = "SELECT rsid_name FROM runs WHERE run_id = $run"

QUERY = """
FROM alerts($run, threshold := $threshold, spike_threshold := $spike)
SELECT alert, name, component_id, round(usual) AS usual, value,
    round(pct_vs_usual * 100, 1) AS pct, day
ORDER BY alert DESC, pct
"""

HEADERS = ["Alert", "Component", "ID", "Weekday average", "Actual", "Change"]


def _number(v: float | None) -> str:
    return "No data" if v is None else f"{v:,.0f}"


def _share(text: str, top: float = 1.0) -> float:
    try:
        value = float(text)
    except ValueError:
        value = -1.0
    if not 0 < value <= top:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not a share between 0 and {top:g}. Write 0.30 for 30 percent."
        )
    return value


def _spike_share(text: str) -> float:
    return _share(text, 2.0)


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
        default=0.30,
        help="The large drop percent as a share (default 0.30).",
    )
    p.add_argument(
        "--spike-threshold",
        type=_spike_share,
        default=0.50,
        help="The large spike percent as a share, up to 2 (default 0.50).",
    )
    p.add_argument("--segment", help="Use the latest runs with this segment ID.")
    return p


def _table(rows: list[tuple]) -> tuple[list[str], str]:
    """The plain text lines and the HTML table for one suite's alerts."""
    text = []
    cells = []
    for alert, name, cid, usual, value, pct, _day in rows:
        change = "" if pct is None else f"{pct:+.1f}%"
        shown = [_number(usual), _number(value)]
        text.append(
            f"- {alert}: {name} ({cid}), weekday average {shown[0]}, actual {shown[1]} {change}"
        )
        cells.append([alert, name, cid, *shown, change])
    th = "".join(f"<th style='text-align:left;padding:4px 8px'>{h}</th>" for h in HEADERS)
    trs = "".join(
        "<tr>"
        + "".join(f"<td style='padding:4px 8px'>{html.escape(str(c))}</td>" for c in row)
        + "</tr>"
        for row in cells
    )
    return text, f"<table style='border-collapse:collapse'><tr>{th}</tr>{trs}</table>"


def build(
    sections: list[tuple[str, str, list[tuple]]], threshold: float, spike: float = 0.50
) -> EmailMessage:
    """One email. Each section is (rsid, suite name, alert rows), for a suite with alerts."""
    total = sum(len(rows) for _, _, rows in sections)
    if len(sections) == 1:
        rsid, _name, rows = sections[0]
        subject = f"{total} Analytics Health alerts for {rsid} on {rows[0][-1]}"
    else:
        subject = f"{total} Analytics Health alerts for {len(sections)} report suites"
    text = [subject, ""]
    parts = [
        (
            f"<p>{html.escape(subject)}. A large drop is more than {threshold:.0%} below the "
            f"weekday average, and a large spike is more than {spike:.0%} above it. The weekday "
            "average is the average of the same weekday in the 12 weeks before.</p>"
        )
    ]
    for rsid, name, rows in sections:
        heading = f"{name} ({rsid}), {rows[0][-1]}: {len(rows)} alerts"
        lines, table = _table(rows)
        text += [heading, *lines, ""]
        parts.append(f"<h3>{html.escape(heading)}</h3>{table}")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.environ.get("MAIL_FROM", "ahd@localhost")
    msg["To"] = os.environ.get("MAIL_TO", "you@example.com")
    msg.set_content("\n".join(text).rstrip() + "\n")
    msg.add_alternative("".join(parts), subtype="html")
    return msg


def _read(args: argparse.Namespace) -> tuple[list[str], list[tuple[str, str, list[tuple]]]]:
    """The suites without a finished run, and (rsid, name, alerts) for each suite with one."""
    missing: list[str] = []
    found: list[tuple[str, str, list[tuple]]] = []
    with duckdb.connect(args.database, read_only=True) as con:
        rsids = args.rsids
        if args.all:
            rows = con.execute(ALL_QUERY, {"segment": args.segment}).fetchall()
            rsids = [r[0] for r in rows]
        for rsid in dict.fromkeys(rsids):
            run = con.execute(RUN_QUERY, {"rsid": rsid, "segment": args.segment}).fetchone()[0]
            if run is None:
                missing.append(rsid)
                continue
            name = con.execute(NAME_QUERY, {"run": run}).fetchone()[0] or rsid
            alerts = con.execute(
                QUERY, {"run": run, "threshold": args.threshold, "spike": args.spike_threshold}
            ).fetchall()
            found.append((rsid, name, alerts))
    return missing, found


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
    if not Path(args.database).is_file():
        print(f"There is no database at {args.database}.", file=sys.stderr)
        return 1
    try:
        missing, found = _read(args)
    except duckdb.Error as e:
        # e.g. "Could not set lock on file" while ahd run writes to it.
        print(f"Could not read {args.database}: {e}", file=sys.stderr)
        return 1
    which = f"with the segment {args.segment}" if args.segment else "without a segment"
    for rsid in missing:
        print(
            f"{args.database} holds no finished run of {rsid} {which}. Check the report "
            "suite ID, or run ahd runs to list the runs.",
            file=sys.stderr,
        )
    if args.all and not found:
        print(f"{args.database} holds no finished run {which}.", file=sys.stderr)
        return 1
    sections = [f for f in found if f[2]]
    for rsid, _name, _rows in found:
        if not _rows:
            print(f"No alerts for {rsid}.")
    if sections:
        count = sum(len(rows) for _, _, rows in sections)
        _send(build(sections, args.threshold, args.spike_threshold), count)
    elif found:
        print("Nothing to send.")
    if missing:
        return 1
    return 0 if sections else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
