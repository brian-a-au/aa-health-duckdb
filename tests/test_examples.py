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


def _db(tmp_path, yesterday_value, segments=(None,)):
    db = tmp_path / "ahd.duckdb"
    s = Store.open(db)
    for segment in segments:
        _add_run(s, yesterday_value, segment)
    s.con.close()
    return db


def _add_run(s, yesterday_value, segment, rsid="rs1", name="Suite"):
    rid = s.create_run(rsid, name, segment, TODAY, "UTC", "0.1")
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


def test_send_alerts_writes_a_draft_without_smtp(tmp_path, monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.chdir(tmp_path)
    mod = _load("send_alerts")
    db = _db(tmp_path, 5_000_000.0)
    assert mod.main([str(db), "rs1"]) == 0
    eml = (tmp_path / "alerts.eml").read_text(encoding="utf-8")
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
    assert mod.main([str(_db(tmp_path, 1.0)), "rs1", "--threshold", "0.5"]) == 0
    assert sent[0] == ("connect", "smtp.example.com", 587)
    assert sent[-1][0:2] == ("send", "team@example.com")
    assert ("login", "me") in sent

    # A server without a login, e.g. a relay on the local network.
    sent.clear()
    monkeypatch.delenv("SMTP_USER")
    monkeypatch.delenv("SMTP_PASSWORD")
    assert mod.main([str(_db(tmp_path / "x", 1.0)), "rs1"]) == 0
    assert not any(step[0] == "login" for step in sent)


def test_send_alerts_with_runs_with_and_without_a_segment(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.chdir(tmp_path)
    mod = _load("send_alerts")
    db = str(_db(tmp_path, 5_000_000.0, segments=(None, "s300_mobile")))
    assert mod.main([db, "rs1"]) == 0
    assert "Wrote alerts.eml" in capsys.readouterr().out
    assert mod.main([db, "rs1", "--segment", "s300_mobile"]) == 0
    assert mod.main([db, "rs1", "--segment", "s300_other"]) == 1
    assert "with the segment s300_other" in capsys.readouterr().err


def test_send_alerts_unknown_suite_is_an_error_not_a_quiet_day(tmp_path, capsys):
    mod = _load("send_alerts")
    assert mod.main([str(_db(tmp_path, 5_000_000.0)), "rs_typo"]) == 1
    assert "holds no finished run of rs_typo" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        ["db", "rs1", "--threshold", "30"],
        ["db", "rs1", "--threshold", "zero"],
        ["db"],
        ["db", "rs1", "--all"],
        [],
    ],
)
def test_send_alerts_bad_arguments_print_usage(argv, capsys):
    mod = _load("send_alerts")
    with pytest.raises(SystemExit) as e:
        mod.main(argv)
    assert e.value.code == 2
    assert "usage:" in capsys.readouterr().err


def _suites_db(tmp_path, values):
    """One finished run for each suite. values maps rsid to yesterday's value."""
    db = tmp_path / "ahd.duckdb"
    s = Store.open(db)
    for rsid, value in values.items():
        _add_run(s, value, None, rsid=rsid, name=f"Suite {rsid}")
    s.con.close()
    return str(db)


def test_send_alerts_puts_several_suites_in_one_email(tmp_path, monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.chdir(tmp_path)
    mod = _load("send_alerts")
    db = _suites_db(tmp_path, {"rs1": 5_000_000.0, "rs2": 1.0})
    assert mod.main([db, "rs1", "rs2"]) == 0
    eml = (tmp_path / "alerts.eml").read_text(encoding="utf-8")
    assert "Subject: 2 Analytics Health alerts for 2 report suites" in eml
    assert "Suite rs1 (rs1), 2026-11-06: 1 alerts" in eml
    assert "Suite rs2 (rs2), 2026-11-06: 1 alerts" in eml


def test_send_alerts_all_uses_every_suite_with_a_finished_run(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.chdir(tmp_path)
    mod = _load("send_alerts")
    db = _suites_db(tmp_path, {"rs1": 5_000_000.0, "rs2": 24_000_000.0, "rs3": 1.0})
    assert mod.main([db, "--all"]) == 0
    out = capsys.readouterr().out
    assert "No alerts for rs2." in out and "Wrote alerts.eml with 2 alerts" in out
    eml = (tmp_path / "alerts.eml").read_text(encoding="utf-8")
    assert "(rs1)" in eml and "(rs3)" in eml and "(rs2)" not in eml


def test_send_alerts_all_with_no_runs_is_an_error(tmp_path, capsys):
    mod = _load("send_alerts")
    db = str(_db(tmp_path, 5_000_000.0))
    assert mod.main([db, "--all", "--segment", "s300_none"]) == 1
    assert "holds no finished run with the segment s300_none" in capsys.readouterr().err


def test_send_alerts_sends_the_others_when_one_suite_is_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.chdir(tmp_path)
    mod = _load("send_alerts")
    db = _suites_db(tmp_path, {"rs1": 5_000_000.0})
    assert mod.main([db, "rs1", "rs_typo"]) == 1
    captured = capsys.readouterr()
    assert "holds no finished run of rs_typo" in captured.err
    eml = (tmp_path / "alerts.eml").read_text(encoding="utf-8")
    assert "Subject: 1 Analytics Health alerts for rs1 on 2026-11-06" in eml


def test_send_alerts_missing_or_locked_database(tmp_path, capsys):
    import subprocess
    import sys

    mod = _load("send_alerts")
    assert mod.main([str(tmp_path / "none.duckdb"), "rs1"]) == 1
    db = _db(tmp_path, 5_000_000.0)
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                f"import duckdb, time; c = duckdb.connect({str(db)!r}); "
                "print('ready', flush=True); time.sleep(30)"
            ),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "ready"
        assert mod.main([str(db), "rs1"]) == 1
        assert "Could not read" in capsys.readouterr().err
    finally:
        holder.kill()
        holder.wait()


@pytest.mark.parametrize("value", [None, 12.5])
def test_number_format(value):
    mod = _load("send_alerts")
    assert mod._number(value) == ("No data" if value is None else "12")
