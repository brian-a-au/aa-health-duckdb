"""The example scripts in docs/examples run against a real ahd database."""

import email
import email.policy
import importlib.util
from datetime import date, timedelta
from pathlib import Path

import pytest

from ahd.api.reports import Column, DailyValue
from ahd.catalog import Component
from ahd.store import Store
from ahd.summary import ANOMALY_METHODS, SENSITIVITIES

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
    with pytest.raises(SystemExit) as e:
        mod.main([db, "rs1", "--anomaly-sensitivity", "high"])
    assert e.value.code == 1
    err = capsys.readouterr().err
    assert "usage:" in err
    assert "--anomaly-sensitivity works only with a local anomaly method" in err
    assert not (tmp_path / "alerts.eml").exists()
    # normal is the default, so it is accepted with adobe, as on the command line.
    assert mod.main([db, "rs1", "--anomaly-sensitivity", "normal"]) == 0
    assert (tmp_path / "alerts.eml").is_file()


def test_send_alerts_knows_the_methods_and_sensitivities_of_ahd():
    # The script runs without ahd installed, so it keeps its own lists.
    mod = _load("send_alerts")
    assert tuple(mod.METHODS) == ANOMALY_METHODS
    assert mod.SENSITIVITIES == SENSITIVITIES


def test_send_alerts_with_the_defaults_reads_a_file_with_older_macros(
    tmp_path, monkeypatch, capsys
):
    """Until ahd writes to a file again, the file keeps the alerts() macro of the version that
    last wrote to it. The defaults must still work with a macro without method or sensitivity.
    Another method or sensitivity needs the new macro, and the message says how to get it."""
    import duckdb

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
    assert mod.main([str(db), "rs1"]) == 0
    text, _page = _mail(tmp_path)
    assert "- large drop: Old macro" in text
    capsys.readouterr()
    for options in (
        ["--anomaly-method", "weekday-iqr"],
        ["--anomaly-method", "weekday-trend", "--anomaly-sensitivity", "low"],
    ):
        assert mod.main([str(db), "rs1", *options]) == 1
        err = capsys.readouterr().err
        assert f"The alerts() macro in {db} is from an older version of ahd" in err
        assert "Binder" not in err


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
    import duckdb

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
