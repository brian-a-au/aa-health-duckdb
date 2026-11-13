"""The example scripts in docs/examples run against a real ahd database."""

import email
import email.policy
import importlib.util
import json
import math
import random
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import duckdb
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


def _add_run(s, yesterday_value, segment, rsid="rs1", name="Suite", earlier=None):
    """One finished run with one metric. earlier gives the value of day n of the run, from 0,
    and is 25 million on every day by default."""
    rid = s.create_run(rsid, name, segment, TODAY, "UTC", "0.1")
    cid = "metrics/event1"
    s.add_components(
        rid, [Component(cid, "Big <event>", "metric", "custom_events", 1, Column(cid, cid), None)]
    )
    start = TODAY - timedelta(days=91)
    values = [
        DailyValue(
            cid,
            start + timedelta(days=n),
            25_000_000.0 if earlier is None else earlier(n),
            None,
            None,
            None,
            None,
        )
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
    # Read the parts decoded, so that a line break in the encoded file does not matter.
    msg = email.message_from_string(eml, policy=email.policy.default)
    text = msg.get_body(("plain",)).get_content()
    page = msg.get_body(("html",)).get_content()
    assert "25,000,000" in text and "e+07" not in text
    assert "weekday average 25,000,000, actual 5,000,000" in text
    assert "<th style='text-align:left;padding:4px 8px'>Weekday average</th>" in page
    assert "Big &lt;event&gt;" in page  # names are escaped in the HTML part


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


def test_send_alerts_lists_a_large_spike(tmp_path, monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.chdir(tmp_path)
    mod = _load("send_alerts")
    # 40 million against a weekday average of 25 million is a spike of 60 percent.
    db = str(_db(tmp_path, 40_000_000.0))
    assert mod.main([db, "rs1"]) == 0
    mail = email.message_from_bytes(
        (tmp_path / "alerts.eml").read_bytes(), policy=email.policy.default
    )
    text = mail.get_body(("plain",)).get_content()
    page = mail.get_body(("html",)).get_content()
    assert "large spike" in text and "+60.0" in text
    assert "a large spike is more than 50% above it" in page
    # A spike setting of 0.7 leaves it out, so there is nothing to send: exit code 2.
    (tmp_path / "alerts.eml").unlink()
    assert mod.main([db, "rs1", "--spike-threshold", "0.7"]) == 2
    assert not (tmp_path / "alerts.eml").exists()


def test_send_alerts_unknown_suite_is_an_error_not_a_quiet_day(tmp_path, capsys):
    mod = _load("send_alerts")
    assert mod.main([str(_db(tmp_path, 5_000_000.0)), "rs_typo"]) == 1
    assert "holds no finished run of rs_typo" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        ["db", "rs1", "--threshold", "30"],
        ["db", "rs1", "--threshold", "zero"],
        ["db", "rs1", "--spike-threshold", "3"],
        ["db", "rs1", "--anomaly-method", "rolling-median"],
        ["db", "rs1", "--anomaly-method", "weekday-median", "--anomaly-sensitivity", "extreme"],
        ["db"],
        ["db", "rs1", "--all"],
        [],
    ],
)
def test_send_alerts_bad_arguments_print_usage(argv, capsys):
    # 1, not 2: a scheduler must not take a typo in an option for a day with no alerts.
    mod = _load("send_alerts")
    with pytest.raises(SystemExit) as e:
        mod.main(argv)
    assert e.value.code == 1
    assert "usage:" in capsys.readouterr().err


def test_send_alerts_help_still_exits_with_0(capsys):
    mod = _load("send_alerts")
    with pytest.raises(SystemExit) as e:
        mod.main(["--help"])
    assert e.value.code == 0
    assert "usage:" in capsys.readouterr().out


def _weeks_db(tmp_path, yesterday_value):
    """A run whose metric is 27.5 million in even weeks and 22.5 million in odd weeks.

    Yesterday's weekday then has 12 earlier values, 6 of each, so the weekday average and the
    median are 25 million, and the spread is 1.4826 times 2.5 million. The range of the Weekday
    median method is 13.88 to 36.12 million with normal, and 17.59 to 32.41 million with high.
    """
    db = tmp_path / "ahd.duckdb"
    s = Store.open(db)
    _add_run(s, yesterday_value, None, earlier=lambda n: 27_500_000.0 - n // 7 % 2 * 5_000_000)
    s.con.close()
    return str(db)


def _mail(tmp_path):
    """The plain text and the HTML of alerts.eml."""
    mail = email.message_from_bytes(
        (tmp_path / "alerts.eml").read_bytes(), policy=email.policy.default
    )
    return mail.get_body(("plain",)).get_content(), mail.get_body(("html",)).get_content()


def test_send_alerts_uses_adobe_by_default_and_says_so(tmp_path, monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.chdir(tmp_path)
    mod = _load("send_alerts")
    # 16 million is 36 percent below the weekday average. The run has no flags from Adobe.
    assert mod.main([_weeks_db(tmp_path, 16_000_000.0), "rs1"]) == 0
    text, page = _mail(tmp_path)
    assert "- large drop: Big <event>" in text
    said = "Adobe's anomaly detection flagged the unusual values."
    assert said in text and said in page
    assert "sensitivity" not in text


@pytest.mark.parametrize(
    ("sensitivity", "alert", "named"),
    [
        # Inside the range of normal, so it stays a large drop.
        (None, "large drop", "The Weekday median method flagged the unusual values."),
        ("normal", "large drop", "The Weekday median method flagged the unusual values."),
        # Below the range of high, so the method flags it.
        ("high", "unusual", "The Weekday median method with high sensitivity flagged the"),
    ],
)
def test_send_alerts_takes_the_anomaly_method_and_sensitivity(
    tmp_path, monkeypatch, sensitivity, alert, named
):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.chdir(tmp_path)
    mod = _load("send_alerts")
    argv = [_weeks_db(tmp_path, 16_000_000.0), "rs1", "--anomaly-method", "weekday-median"]
    if sensitivity:
        argv += ["--anomaly-sensitivity", sensitivity]
    assert mod.main(argv) == 0
    text, page = _mail(tmp_path)
    assert f"- {alert}: Big <event>" in text
    assert named in text and named in page
    assert "ahd computes this method from the daily values of the run." in text
    assert "Adobe" not in text


def test_send_alerts_refuses_a_sensitivity_with_adobe(tmp_path, monkeypatch, capsys):
    # A scheduled job that ignored the setting would send other alerts than the user chose.
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.chdir(tmp_path)
    mod = _load("send_alerts")
    db = _weeks_db(tmp_path, 16_000_000.0)
    # ahd alerts refuses it, as every ahd command does, with exit code 1 and without the usage.
    assert mod.main([db, "rs1", "--anomaly-sensitivity", "high"]) == 1
    err = capsys.readouterr().err
    assert "Error: --anomaly-sensitivity works only with a local anomaly method" in err
    assert not (tmp_path / "alerts.eml").exists()
    # normal is the default, so it is accepted with adobe, as on the command line.
    assert mod.main([db, "rs1", "--anomaly-sensitivity", "normal"]) == 0
    assert (tmp_path / "alerts.eml").is_file()


def test_send_alerts_reads_the_anomaly_variables_of_ahd(tmp_path, monkeypatch):
    # A scheduled job sets the two variables once, for ahd run and for this script.
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.chdir(tmp_path)
    mod = _load("send_alerts")
    db = _weeks_db(tmp_path, 16_000_000.0)
    monkeypatch.setenv("AHD_ANOMALY_METHOD", "weekday-median")
    monkeypatch.setenv("AHD_ANOMALY_SENSITIVITY", "high")
    assert mod.main([db, "rs1"]) == 0
    text, page = _mail(tmp_path)
    assert "- unusual: Big <event>" in text
    named = "The Weekday median method with high sensitivity flagged the"
    assert named in text and named in page
    # An option wins over the variable.
    assert mod.main([db, "rs1", "--anomaly-sensitivity", "normal"]) == 0
    text, _page = _mail(tmp_path)
    assert "- large drop: Big <event>" in text
    assert "The Weekday median method flagged the unusual values." in text
    assert mod.main([db, "rs1", "--anomaly-method", "weekday-iqr"]) == 0
    assert "The Weekday quartile range method with high sensitivity" in _mail(tmp_path)[0]
    # An empty variable, or one with only spaces, is not set.
    monkeypatch.setenv("AHD_ANOMALY_METHOD", "")
    monkeypatch.setenv("AHD_ANOMALY_SENSITIVITY", " ")
    assert mod.main([db, "rs1"]) == 0
    assert "Adobe's anomaly detection flagged the unusual values." in _mail(tmp_path)[0]


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        (
            "AHD_ANOMALY_METHOD",
            "weekday_median",
            (
                "Error: The AHD_ANOMALY_METHOD variable is set to 'weekday_median', which is not an "
                "anomaly method. Use adobe, weekday-median, weekday-iqr or weekday-trend, or unset "
                "the variable.\n"
            ),
        ),
        (
            "AHD_ANOMALY_SENSITIVITY",
            "Medium",
            (
                "Error: The AHD_ANOMALY_SENSITIVITY variable is set to 'Medium', which is not an "
                "anomaly sensitivity. Use low, normal or high, or unset the variable.\n"
            ),
        ),
    ],
)
def test_send_alerts_stops_for_an_unknown_value_in_a_variable(
    tmp_path, monkeypatch, capsys, name, value, message
):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.chdir(tmp_path)
    mod = _load("send_alerts")
    db = _weeks_db(tmp_path, 16_000_000.0)
    monkeypatch.setenv(name, value)
    # Exit code 1, as for a wrong option, but without the usage: no option is wrong.
    assert mod.main([db, "rs1"]) == 1
    assert capsys.readouterr().err == message
    assert not (tmp_path / "alerts.eml").exists()
    # An option on the command line wins, so the variable is not read.
    option = "--anomaly-method" if name == "AHD_ANOMALY_METHOD" else "--anomaly-sensitivity"
    chosen = "weekday-median" if name == "AHD_ANOMALY_METHOD" else "normal"
    assert mod.main([db, "rs1", option, chosen]) == 0


@pytest.mark.parametrize(
    ("variables", "options", "message"),
    [
        (
            {"AHD_ANOMALY_SENSITIVITY": "high"},
            [],
            (
                "Error: The anomaly sensitivity high from the AHD_ANOMALY_SENSITIVITY variable works "
                "only with a local anomaly method, e.g., --anomaly-method weekday-median or "
                "AHD_ANOMALY_METHOD=weekday-median. The adobe method uses the expected range that "
                "Adobe returned, which ahd does not change. To use adobe, unset "
                "AHD_ANOMALY_SENSITIVITY or set it to normal.\n"
            ),
        ),
        (
            {"AHD_ANOMALY_METHOD": "adobe"},
            ["--anomaly-sensitivity", "low"],
            (
                "Error: --anomaly-sensitivity works only with a local anomaly method, e.g., "
                "--anomaly-method weekday-median or AHD_ANOMALY_METHOD=weekday-median. The adobe "
                "method uses the expected range that Adobe returned, which ahd does not change. The "
                "method adobe comes from the AHD_ANOMALY_METHOD variable.\n"
            ),
        ),
    ],
)
def test_send_alerts_refuses_a_sensitivity_with_adobe_from_a_variable(
    tmp_path, monkeypatch, capsys, variables, options, message
):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.chdir(tmp_path)
    mod = _load("send_alerts")
    db = _weeks_db(tmp_path, 16_000_000.0)
    for name, value in variables.items():
        monkeypatch.setenv(name, value)
    assert mod.main([db, "rs1", *options]) == 1
    assert capsys.readouterr().err == message
    assert not (tmp_path / "alerts.eml").exists()


def test_send_alerts_uses_the_rules_of_ahd_also_on_a_file_with_older_macros(
    tmp_path, monkeypatch, capsys
):
    """The file keeps the alerts() macro of the version of ahd that last wrote to it. ahd alerts
    uses the macros of the installed ahd instead, so every method works on such a file."""
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.chdir(tmp_path)
    mod = _load("send_alerts")
    db = _db(tmp_path, 5_000_000.0)
    with duckdb.connect(str(db)) as con:
        con.execute(
            "CREATE OR REPLACE MACRO alerts(run_number, threshold := 0.30, "
            "spike_threshold := 0.50) AS TABLE "
            "SELECT 'large drop' AS alert, 'Old macro' AS name, 'metrics/event1' AS component_id, "
            "1.0 AS usual, 0.5 AS value, -0.5 AS pct_vs_usual, DATE '2026-11-06' AS day"
        )
    for options in ([], ["--anomaly-method", "weekday-trend", "--anomaly-sensitivity", "low"]):
        assert mod.main([str(db), "rs1", *options]) == 0
        text, _page = _mail(tmp_path)
        assert "Old macro" not in text and "Big <event>" in text


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
    mod = _load("send_alerts")
    assert mod.main([str(tmp_path / "none.duckdb"), "rs1"]) == 1
    assert "There is no database at" in capsys.readouterr().err
    db = _db(tmp_path, 5_000_000.0)
    with subprocess.Popen(
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
    ) as holder:
        try:
            ready = holder.stdout.readline().strip()
            assert ready == "ready"
            assert mod.main([str(db), "rs1"]) == 1
            assert "is in use by another program" in capsys.readouterr().err
        finally:
            holder.kill()


@pytest.mark.parametrize("value", [None, 12.5])
def test_number_format(value):
    mod = _load("send_alerts")
    assert mod._number(value) == ("No data" if value is None else "12")


def test_send_alerts_reads_the_newest_run_date_not_the_highest_run_number(
    tmp_path, monkeypatch, capsys
):
    """A run made later with ahd run --date for an old date is not the latest run."""
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.chdir(tmp_path)
    mod = _load("send_alerts")
    db = tmp_path / "ahd.duckdb"
    s = Store.open(db)
    _add_run(s, 24_000_000.0, None)  # the newest run date, without alerts
    _add_run(s, 5_000_000.0, None)  # a large drop
    s.con.execute("UPDATE runs SET today = today - 30 WHERE run_id = 2")
    s.con.execute("UPDATE daily_values SET day = day - 30 WHERE run_id = 2")
    s.con.close()
    assert mod.main([str(db), "rs1"]) == 2
    assert "No alerts for rs1." in capsys.readouterr().out
    assert not (tmp_path / "alerts.eml").exists()


GUIDE = EXAMPLES.parent / "duckdb.md"


def _guide_blocks(language):
    """The code blocks of the DuckDB guide in this language, with the line each starts on.

    The language "" gives the blocks without one, e.g., the commands.
    """
    blocks = []
    opened = None
    for number, line in enumerate(GUIDE.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.startswith("```"):
            if opened is not None:
                opened[2].append(line)
        elif opened is None:
            opened = (number, line[3:].strip(), [])
        else:
            if opened[1] == language:
                blocks.append((opened[0], "\n".join(opened[2]) + "\n"))
            opened = None
    assert opened is None, "a code block in docs/duckdb.md is not closed"
    return blocks


def test_every_sql_example_in_the_duckdb_guide_runs(tmp_path, monkeypatch):
    """Each SQL block of the guide runs on a read-only file, as the guide tells the reader to.

    Two kinds of block are left out. A block with INSTALL needs the network, and a block with
    ATTACH needs the files of other profiles.
    """
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "ahd.duckdb"
    s = Store.open(db)
    _add_run(s, 5_000_000.0, None, rsid="my_suite")
    _add_run(s, 24_000_000.0, None, rsid="my_suite")
    s.con.close()
    [owners] = [b for _, b in _guide_blocks("") if b.startswith("pattern,owner")]
    (tmp_path / "owners.csv").write_text(owners, encoding="utf-8")
    ran = 0
    for line, sql in _guide_blocks("sql"):
        if "INSTALL" in sql or "ATTACH" in sql:
            continue
        with duckdb.connect(str(db), read_only=True) as con:
            try:
                con.execute(sql)
            except duckdb.Error as e:
                pytest.fail(f"The SQL example on line {line} of docs/duckdb.md fails: {e}")
        ran += 1
    assert ran >= 10
    assert (tmp_path / "days.parquet").is_file() and (tmp_path / "alerts.csv").is_file()


def test_the_notebook_command_in_the_guide_does_not_open_the_session_read_only():
    """duckdb -readonly -ui shows "Initialization Error", because the notebook cannot save its
    own state. The guide attaches the ahd file read-only instead."""
    commands = [
        line
        for _, block in _guide_blocks("")
        for line in block.splitlines()
        if line.startswith("duckdb") and " -ui" in line
    ]
    assert commands, "the guide should show how to start the notebook"
    for command in commands:
        assert "-readonly" not in command and "(READ_ONLY)" in command


# ---- the script gets its alerts from ahd alerts ------------------------------------------


def test_send_alerts_runs_ahd_alerts_with_its_options(tmp_path, monkeypatch):
    """The script passes its own options on, and the shares as exact percents."""
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.chdir(tmp_path)
    mod = _load("send_alerts")
    calls = []
    real = mod.subprocess.run

    def run(argv, **kw):
        calls.append(argv)
        return real(argv, **kw)

    monkeypatch.setattr(mod.subprocess, "run", run)
    db = str(_db(tmp_path, 5_000_000.0))
    argv = [db, "rs1", "rs1", "--segment", "s1", "--threshold", "0.275"]
    argv += ["--spike-threshold", "1.5", "--anomaly-method", "weekday-iqr"]
    assert mod.main(argv) == 1  # no run with the segment s1
    [call] = calls
    assert call[:3] == [sys.executable, "-m", "ahd.cli"]
    assert call[3:] == [
        "alerts",
        f"--db={db}",
        "--format=json",
        "--change-alert=27.5",
        "--spike-alert=150",
        "--rsid=rs1",
        "--rsid=rs1",
        "--segment=s1",
        "--anomaly-method=weekday-iqr",
    ]
    calls.clear()
    assert mod.main([db, "--all"]) == 0
    assert calls[0][5:] == [
        "--format=json",
        "--change-alert=30",
        "--spike-alert=50",
        "--all-suites",
    ]


@pytest.mark.parametrize(
    "share",
    [
        "0.3",
        "0.30",
        ".3",
        "3e-1",
        "0.275",
        "1",
        "0.05",
        "0.333333333333333333333333333333333",
        "0.1_5",
        " 0.4 ",
        "0.0000001",
    ],
)
def test_a_share_becomes_a_percent_that_gives_the_same_share_in_ahd(share):
    from ahd.cli import _percent_share

    mod = _load("send_alerts")
    percent = mod._percent_text(share)
    assert _percent_share(100)(percent) == float(share)


def test_the_percent_of_any_share_gives_the_same_share_in_ahd():
    from ahd.cli import _percent_share

    mod = _load("send_alerts")
    rng = random.Random(7)
    for _ in range(2000):
        share = repr(rng.uniform(1e-9, 2.0))
        assert _percent_share(200)(mod._percent_text(share)) == float(share), share


def test_the_rounding_of_the_script_is_the_rounding_of_duckdb():
    """The email showed values that DuckDB rounded, e.g., round(usual). The script rounds them
    itself now, in the same way: half away from zero."""
    mod = _load("send_alerts")
    rng = random.Random(11)
    values = [0.5, 1.5, 2.5, -0.5, -2.5, 0.49999999999999994, 12.25, 12.35, -12.25, 4849.25]
    values += [rng.uniform(-1e6, 1e6) for _ in range(500)]
    values += [round(rng.uniform(-1000, 1000), 2) + 0.05 for _ in range(500)]
    with duckdb.connect() as con:
        for v in values:
            query = "SELECT round(?::DOUBLE), round(?::DOUBLE, 1)"
            whole, tenth = con.execute(query, [v, v]).fetchone()
            assert (mod._round(v), mod._round(v, 1)) == (whole, tenth), v
    assert mod._round(math.inf) == math.inf and mod._round(None) is None


OLD_QUERY = """
FROM alerts($run, threshold := $threshold, spike_threshold := $spike, method := $method,
    sensitivity := $sensitivity)
SELECT alert, name, component_id, round(usual) AS usual, value,
    round(pct_vs_usual * 100, 1) AS pct, day
ORDER BY alert DESC, pct
"""


def _rich_db(tmp_path):
    """Two suites, each with 40 metrics that vary from day to day, so that every kind of alert
    turns up and the values have fractions."""
    db = tmp_path / "ahd.duckdb"
    s = Store.open(db)
    rng = random.Random(3)
    for rsid in ("rs1", "rs2"):
        rid = s.create_run(rsid, f"Suite {rsid}", None, TODAY, "UTC", "0.1")
        comps, values = [], []
        for n in range(1, 41):
            cid = f"metrics/event{n}"
            comps.append(
                Component(cid, f"Event {n}", "metric", "custom_events", n, Column(cid, cid), None)
            )
            base = rng.choice([0.0, 5.0, 40.0, 1000.0])
            for d in range(91):
                day = TODAY - timedelta(days=91 - d)
                v = base * rng.uniform(0.6, 1.4)
                if d == 90:
                    v = rng.choice([0.0, base * 0.3, base * 2.5, v])
                flagged = d == 90 and n % 7 == 0
                values.append(
                    DailyValue(cid, day, round(v, 2), base, base * 1.3, base * 0.7, flagged)
                )
        s.add_components(rid, comps)
        s.add_daily_values(rid, values)
        s.finish_run(rid, "ok")
    s.con.close()
    return db


@pytest.mark.parametrize(
    "options",
    [
        {"threshold": 0.3, "spike": 0.5, "method": "adobe", "sensitivity": "normal"},
        {"threshold": 0.275, "spike": 1.2, "method": "weekday-median", "sensitivity": "high"},
    ],
)
def test_the_email_lists_the_same_rows_as_the_old_query(tmp_path, monkeypatch, options):
    """Before 0.1.11 the script read the rows with OLD_QUERY. The rows from ahd alerts, rounded
    and ordered by the script, are the same, with the new alerts first since 0.1.12."""
    mod = _load("send_alerts")
    db = _rich_db(tmp_path)
    argv = [str(db), "rs1", "rs2", "--threshold", str(options["threshold"])]
    argv += ["--spike-threshold", str(options["spike"]), "--anomaly-method", options["method"]]
    argv += ["--anomaly-sensitivity", options["sensitivity"]]
    built = []
    monkeypatch.setattr(mod, "_send", lambda msg, count: None)
    real_build = mod.build

    def build(sections, *a, **k):
        built.append(sections)
        return real_build(sections, *a, **k)

    monkeypatch.setattr(mod, "build", build)
    assert mod.main(argv) == 0
    [sections] = built
    with duckdb.connect(str(db), read_only=True) as con:
        for rsid, name, rows in sections:
            run = 1 if rsid == "rs1" else 2
            # The email writes the day as YYYY-MM-DD, which is how the JSON gives it.
            old = [
                (*r[:-1], r[-1].isoformat())
                for r in con.execute(OLD_QUERY, {"run": run, **options}).fetchall()
            ]
            assert name == f"Suite {rsid}"
            assert len(rows) == len(old) > 0
            # 0.1.12 added since when each alert held and whether it is new, and lists the new
            # alerts first. The other fields are the same as those of the old query.
            same = [(*r[:6], r[-1]) for r in rows]
            # Rows with the same alert and change may come in either order in the old query.
            assert sorted(same, key=repr) == sorted(old, key=repr)
            new = {r[2]: r[7] for r in rows}
            old = [r for r in old if new[r[2]]] + [r for r in old if not new[r[2]]]
            assert [(r[0], r[5]) for r in rows] == [(r[0], r[5]) for r in old]
    if options["method"] == "adobe":
        # With a local method, most large drops and spikes are unusual values instead.
        kinds = {r[0] for _, _, rows in sections for r in rows}
        assert kinds == {"unusual", "large spike", "large drop", "dropped to zero"}


def test_send_alerts_without_ahd_says_how_to_get_it(tmp_path, monkeypatch, capsys):
    mod = _load("send_alerts")
    monkeypatch.setattr(mod, "_ahd_command", lambda: None)
    assert mod.main([str(_db(tmp_path, 5_000_000.0)), "rs1"]) == 1
    assert capsys.readouterr().err == (
        "This script needs ahd 0.1.11 or newer, from the package aa-health-duckdb. Install it, "
        "e.g., with uv tool install aa-health-duckdb, or run the script with it added, e.g., "
        "uv run --with aa-health-duckdb python send_alerts.py.\n"
    )


def test_the_script_finds_ahd_in_its_python_first_then_on_the_path(monkeypatch):
    mod = _load("send_alerts")
    assert mod._ahd_command() == [sys.executable, "-m", "ahd.cli"]

    def missing(name):
        raise mod.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(mod.metadata, "version", missing)
    monkeypatch.setattr(mod.shutil, "which", lambda name: "/opt/bin/ahd" if name == "ahd" else None)
    assert mod._ahd_command() == ["/opt/bin/ahd"]
    monkeypatch.setattr(mod.shutil, "which", lambda name: None)
    assert mod._ahd_command() is None


OLD_AHD = (
    "import sys; sys.stderr.write('usage: ahd [-h]\\nahd: error: argument command: invalid "
    "choice: \\'alerts\\' (choose from check, run)\\n'); sys.exit(2)"
)


def test_an_ahd_without_the_alerts_command_is_named(tmp_path, monkeypatch, capsys):
    mod = _load("send_alerts")
    monkeypatch.setattr(mod, "_ahd_command", lambda: [sys.executable, "-c", OLD_AHD])
    assert mod.main([str(_db(tmp_path, 5_000_000.0)), "rs1"]) == 1
    assert capsys.readouterr().err == (
        "The ahd that this script found has no alerts command, so it is older than 0.1.11. "
        "Update it, e.g., with uv tool upgrade aa-health-duckdb.\n"
    )


@pytest.mark.parametrize("code", [0, 1, 4])
def test_an_answer_that_is_not_json_is_an_error(tmp_path, monkeypatch, capsys, code):
    mod = _load("send_alerts")
    odd = [sys.executable, "-c", f"import sys; print('not json'); sys.exit({code})"]
    monkeypatch.setattr(mod, "_ahd_command", lambda: odd)
    assert mod.main([str(_db(tmp_path, 5_000_000.0)), "rs1"]) == 1
    assert "ahd alerts did not give its alerts as JSON" in capsys.readouterr().err


def test_another_exit_code_of_ahd_is_an_error(tmp_path, monkeypatch, capsys):
    mod = _load("send_alerts")
    stopped = [sys.executable, "-c", "import sys; sys.stderr.write('Stopped.\\n'); sys.exit(130)"]
    monkeypatch.setattr(mod, "_ahd_command", lambda: stopped)
    assert mod.main([str(_db(tmp_path, 5_000_000.0)), "rs1"]) == 1
    assert capsys.readouterr().err == "Stopped.\n"


def test_the_script_reads_the_json_of_ahd_alerts(tmp_path, capsys):
    """The fields that the script reads are in the JSON that ahd alerts writes."""
    from ahd.cli import main as ahd

    db = _suites_db(tmp_path, {"rs1": 5_000_000.0})
    assert ahd(["alerts", "--db", db, "--rsid", "rs1", "--format", "json"]) == 0
    [run] = json.loads(capsys.readouterr().out)["runs"]
    for key in ("rsid", "rsid_name", "day", "method", "method_name", "sensitivity", "alerts"):
        assert key in run
    for key in ("alert", "name", "component_id", "usual", "value", "pct_vs_usual"):
        assert key in run["alerts"][0]


def test_the_script_writes_nothing_into_the_folder_it_is_not_run_in(tmp_path, monkeypatch):
    """alerts.eml goes into the current folder only."""
    monkeypatch.delenv("SMTP_HOST", raising=False)
    here = tmp_path / "here"
    here.mkdir()
    monkeypatch.chdir(here)
    mod = _load("send_alerts")
    assert mod.main([str(_db(tmp_path, 5_000_000.0)), "rs1"]) == 0
    assert [p.name for p in here.iterdir()] == ["alerts.eml"]
    assert not (EXAMPLES / "alerts.eml").exists()


def _since_db(tmp_path):
    """One run with a metric that dropped to 0 yesterday, one that has been 0 for 9 days, and one
    more than 30 percent below its weekday average on each of the last 8 days."""
    db = tmp_path / "ahd.duckdb"
    s = Store.open(db)
    rid = s.create_run("rs1", "Suite", None, TODAY, "UTC", "0.1")
    tails = {"metrics/event1": [0.0], "metrics/event2": [0.0] * 9, "metrics/event3": [50.0] * 8}
    comps, values = [], []
    for n, (cid, tail) in enumerate(tails.items(), start=1):
        comps.append(
            Component(cid, f"Event {n}", "metric", "custom_events", n, Column(cid, cid), None)
        )
        for d in range(91):
            day = TODAY - timedelta(days=91 - d)
            back = (TODAY - day).days
            v = tail[-back] if back <= len(tail) else 100.0
            values.append(DailyValue(cid, day, v, None, None, None, None))
    s.add_components(rid, comps)
    s.add_daily_values(rid, values)
    s.finish_run(rid, "ok")
    s.con.close()
    return str(db)


def test_the_email_lists_new_alerts_first_and_says_since_when(tmp_path, monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.chdir(tmp_path)
    mod = _load("send_alerts")
    assert mod.main([_since_db(tmp_path), "rs1"]) == 0
    text, page = _mail(tmp_path)
    lines = [line for line in text.splitlines() if line.startswith("- ")]
    assert lines == [
        (
            "- dropped to zero: Event 1 (metrics/event1), weekday average 100, actual 0 -100.0%, "
            "new on 2026-11-06"
        ),
        (
            "- large drop: Event 3 (metrics/event3), weekday average 96, actual 50 -47.8%, "
            "since 2026-10-31 or earlier, at least 7 days"
        ),
        (
            "- dropped to zero: Event 2 (metrics/event2), weekday average 92, actual 0 -100.0%, "
            "since 2026-10-29, 9 days"
        ),
    ]
    assert "Suite (rs1), 2026-11-06: 3 alerts, 1 new" in text
    assert "<th style='text-align:left;padding:4px 8px'>Since</th>" in page
    assert "<td style='padding:4px 8px'>since 2026-10-29, 9 days</td>" in page


def test_the_email_reads_the_json_of_ahd_0_1_11(tmp_path, monkeypatch):
    """ahd 0.1.11 gives no since fields. The email then leaves the words out."""
    mod = _load("send_alerts")
    run = {
        "day": "2026-11-06",
        "alerts": [
            {
                "alert": "large drop",
                "name": "E",
                "component_id": "metrics/event1",
                "usual": 100.0,
                "value": 50.0,
                "pct_vs_usual": -0.5,
            },
        ],
    }
    [row] = mod._rows(run)
    lines, table = mod._table([row])
    assert lines == ["- large drop: E (metrics/event1), weekday average 100, actual 50 -50.0%"]
    assert "<td style='padding:4px 8px'></td>" in table


def _event_db(tmp_path, zeros=12):
    """One run with zeros metrics at 0 yesterday, one large drop and 7 metrics at their weekday
    average. With 12 zeros, they are a suite-wide event: 12 of the 20 that collected data."""
    db = tmp_path / "ahd.duckdb"
    s = Store.open(db)
    rid = s.create_run("rs1", "Suite", None, TODAY, "UTC", "0.1")
    kinds = ["zero"] * zeros + ["drop"] + ["quiet"] * 7
    comps, values = [], []
    for n, kind in enumerate(kinds, start=1):
        cid = f"metrics/event{n:02d}"
        comps.append(
            Component(cid, f"Event {n}", "metric", "custom_events", n, Column(cid, cid), None)
        )
        for d in range(91):
            day = TODAY - timedelta(days=91 - d)
            v = 100.0
            if d == 90:
                v = {"zero": 0.0, "drop": 50.0, "quiet": 100.0}[kind]
            values.append(DailyValue(cid, day, v, None, None, None, None))
    s.add_components(rid, comps)
    s.add_daily_values(rid, values)
    s.finish_run(rid, "ok")
    s.con.close()
    return str(db)


EVENT_TEXT = (
    "Suite-wide event: 12 of the 20 components that collected data had a value of 0 on "
    "2026-11-06. For all of them, this started on 2026-11-06, so it is new."
)


def test_the_email_starts_with_the_event_and_shortens_it(tmp_path, monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.chdir(tmp_path)
    mod = _load("send_alerts")
    assert mod.main([_event_db(tmp_path), "rs1"]) == 0
    text, page = _mail(tmp_path)
    lines = text.splitlines()
    start = lines.index("Suite (rs1), 2026-11-06: 13 alerts, 13 new")
    zero = (
        "- dropped to zero: Event {n} (metrics/event{n:02d}), weekday average 100, actual 0 "
        "-100.0%, new on 2026-11-06"
    )
    assert lines[start + 1 :] == [
        EVENT_TEXT,
        *[zero.format(n=n) for n in range(1, 6)],
        "7 more alerts of this event are not listed. ahd alerts --every-alert lists them all.",
        "Other alerts:",
        (
            "- large drop: Event 13 (metrics/event13), weekday average 100, actual 50 -50.0%, "
            "new on 2026-11-06"
        ),
    ]
    # The subject still counts every alert.
    assert lines[0] == "13 Analytics Health alerts for rs1 on 2026-11-06"
    assert f"<p>{EVENT_TEXT}</p>" in page
    assert page.count("<tr>") == 1 + 5 + 1 + 1
    assert "<p>7 more alerts of this event are not listed." in page
    assert "<p>Other alerts</p>" in page


def test_the_email_without_an_event_lists_every_alert(tmp_path, monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.chdir(tmp_path)
    mod = _load("send_alerts")
    assert mod.main([_event_db(tmp_path, zeros=9), "rs1"]) == 0
    text, page = _mail(tmp_path)
    assert "Suite-wide event" not in text and "Other alerts" not in text
    assert len([line for line in text.splitlines() if line.startswith("- ")]) == 10
    assert "Suite-wide event" not in page


def test_the_email_reads_the_json_of_ahd_0_1_12(tmp_path, monkeypatch):
    """ahd 0.1.12 gives no events and no event field. The email then lists every alert."""
    mod = _load("send_alerts")
    run = {
        "day": "2026-11-06",
        "alerts": [
            {
                "alert": "large drop",
                "name": "E",
                "component_id": "metrics/event1",
                "usual": 100.0,
                "value": 50.0,
                "pct_vs_usual": -0.5,
                "since": "2026-11-06",
                "days": 1,
                "at_least": False,
                "is_new": True,
            },
        ],
    }
    rows = mod._rows(run)
    msg = mod.build([("rs1", "Suite", rows)], 0.3, 0.5, "Adobe.", events={"rs1": []})
    body = msg.get_body(("plain",)).get_content()
    assert "- large drop: E (metrics/event1)" in body and "Suite-wide event" not in body
