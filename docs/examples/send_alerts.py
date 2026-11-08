"""Email the latest alerts for one report suite, but only when there are any.

Usage:
    uv run python docs/examples/send_alerts.py DATABASE RSID [THRESHOLD]

It reads the latest finished run of the suite with the alerts() macro that every ahd
database holds, and builds an HTML email with a plain text part.

To send, set SMTP_HOST, SMTP_PORT (default 587), SMTP_USER, SMTP_PASSWORD, MAIL_FROM and
MAIL_TO. Without SMTP_HOST, it writes the message to alerts.eml instead, so you can open it
and check it first. It exits with 0 when it sent or wrote a message, and with 2 when there
were no alerts, so a scheduler can tell the two apart.
"""

from __future__ import annotations

import html
import os
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path

import duckdb

QUERY = """
FROM alerts((SELECT run_id FROM latest_runs WHERE rsid = $rsid), threshold := $threshold)
SELECT alert, name, component_id, round(usual) AS usual, value,
    round(pct_vs_usual * 100, 1) AS pct, day
ORDER BY alert DESC, pct
"""

HEADERS = ["Alert", "Component", "ID", "Usual", "Actual", "Change"]


def _number(v: float | None) -> str:
    return "No data" if v is None else f"{v:,.0f}"


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


def main(argv: list[str]) -> int:
    database, rsid = argv[0], argv[1]
    threshold = float(argv[2]) if len(argv) > 2 else 0.30
    with duckdb.connect(database, read_only=True) as con:
        rows = con.execute(QUERY, {"rsid": rsid, "threshold": threshold}).fetchall()
    if not rows:
        print(f"No alerts for {rsid}. Nothing to send.")
        return 2
    msg = build(rows, rsid, threshold)
    host = os.environ.get("SMTP_HOST")
    if not host:
        Path("alerts.eml").write_bytes(bytes(msg))
        print(f"Wrote alerts.eml with {len(rows)} alerts. Set SMTP_HOST to send it.")
        return 0
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587"))) as smtp:
        smtp.starttls()
        smtp.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
        smtp.send_message(msg)
    print(f"Sent {len(rows)} alerts to {msg['To']}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
