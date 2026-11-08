"""The example scripts in docs/examples run against a real ahd database."""

import importlib.util
from datetime import date, timedelta
from pathlib import Path

import pytest

from ahd.api.reports import Column, DailyValue
from ahd.catalog import Component
from ahd.store import Store

EXAMPLES = Path(__file__).resolve().parent.parent / "docs" / "examples"
TODAY = date(2026, 11, 7)


def _load(name):
    spec = importlib.util.spec_from_file_location(name, EXAMPLES / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _db(tmp_path, yesterday_value):
    db = tmp_path / "ahd.duckdb"
    s = Store.open(db)
    rid = s.create_run("rs1", "Suite", None, TODAY, "UTC", "0.1")
    cid = "metrics/event1"
    s.add_components(
        rid, [Component(cid, "Big <event>", "metric", "custom_events", 1, Column(cid, cid), None)]
    )
    start = TODAY - timedelta(days=91)
    values = [
        DailyValue(cid, start + timedelta(days=n), 25_000_000.0, None, None, None, None)
        for n in range(91)
    ]
    values[-1] = DailyValue(cid, TODAY - timedelta(days=1), yesterday_value, None, None, None, None)
    s.add_daily_values(rid, values)
    s.finish_run(rid, "ok")
    s.con.close()
    return db


def test_send_alerts_writes_a_draft_without_smtp(tmp_path, monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.chdir(tmp_path)
    mod = _load("send_alerts")
    db = _db(tmp_path, 5_000_000.0)
    assert mod.main([str(db), "rs1"]) == 0
    eml = (tmp_path / "alerts.eml").read_text()
    assert "Subject: 1 Analytics Health alerts for rs1 on 2026-11-06" in eml
    assert "25,000,000" in eml and "e+07" not in eml
    assert "Big &lt;event&gt;" in eml  # names are escaped in the HTML part


def test_send_alerts_nothing_to_send(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    mod = _load("send_alerts")
    assert mod.main([str(_db(tmp_path, 24_000_000.0)), "rs1"]) == 2
    assert "Nothing to send" in capsys.readouterr().out
    assert not (tmp_path / "alerts.eml").exists()


def test_send_alerts_uses_smtp_when_configured(tmp_path, monkeypatch):
    mod = _load("send_alerts")
    sent = []

    class FakeSMTP:
        def __init__(self, host, port):
            sent.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            sent.append(("tls",))

        def login(self, user, password):
            sent.append(("login", user))

        def send_message(self, msg):
            sent.append(("send", msg["To"], msg["Subject"]))

    monkeypatch.setattr(mod.smtplib, "SMTP", FakeSMTP)
    for k, v in {
        "SMTP_HOST": "smtp.example.com",
        "SMTP_USER": "me",
        "SMTP_PASSWORD": "pw",
        "MAIL_TO": "team@example.com",
    }.items():
        monkeypatch.setenv(k, v)
    assert mod.main([str(_db(tmp_path, 1.0)), "rs1", "0.5"]) == 0
    assert sent[0] == ("connect", "smtp.example.com", 587)
    assert sent[-1][0:2] == ("send", "team@example.com")


@pytest.mark.parametrize("value", [None, 12.5])
def test_number_format(value):
    mod = _load("send_alerts")
    assert mod._number(value) == ("No data" if value is None else "12")
