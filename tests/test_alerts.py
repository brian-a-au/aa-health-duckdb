"""ahd alerts: the alerts of stored runs as text, JSON or CSV, by the rules of the alerts() macro."""

import csv
import io
import json
import math
import shutil
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from ahd.alerts import FORMATS, csv_value, run_age
from ahd.api.reports import Column, DailyValue
from ahd.catalog import Component
from ahd.cli import main
from ahd.store import Store
from ahd.summary import ANOMALY_METHODS, SENSITIVITIES

TODAY = date(2026, 11, 7)
YESTERDAY = TODAY - timedelta(days=1)
FIXTURES = Path(__file__).resolve().parent / "fixtures"

# One component for each kind of alert, one without an alert and one that never collected data.
# Each has the value 100 on every day but yesterday, so its weekday average is 100.
KINDS = {
    "metrics/event1": ("Flagged", 150.0),
    "metrics/event2": ("Zero", 0.0),
    "metrics/event3": ("Drop", 50.0),
    "metrics/event4": ("Spike", 200.0),
    "metrics/event5": ("Quiet", 100.0),
    "metrics/event6": ("Never", 0.0),
}


def _add_run(
    store, rsid="rs1", name="Suite One", segment=None, today=TODAY, quiet=False, names=None
):
    """A finished run. quiet gives every component its usual value yesterday, so no alerts."""
    names = names or {}
    rid = store.create_run(rsid, name, segment, today, "UTC", "0.1")
    comps = [
        Component(cid, names.get(cid, label), "metric", "custom_events", n, Column(cid, cid), True)
        for n, (cid, (label, _)) in enumerate(KINDS.items(), start=1)
    ]
    store.add_components(rid, comps)
    values = []
    for cid, (_label, last) in KINDS.items():
        usual = 0.0 if cid == "metrics/event6" else 100.0
        for n in range(91):
            day = today - timedelta(days=91 - n)
            if day == today - timedelta(days=1):
                value = usual if quiet else last
                if cid == "metrics/event1" and not quiet:
                    # Adobe flagged it: above the upper end of its range.
                    values.append(DailyValue(cid, day, value, 100.0, 120.0, 80.0, True))
                    continue
            else:
                value = usual
            values.append(DailyValue(cid, day, value, None, None, None, None))
    store.add_daily_values(rid, values)
    store.finish_run(rid, "ok")
    return rid


def _db(tmp_path, **kw):
    db = tmp_path / "ahd.duckdb"
    store = Store.open(db)
    _add_run(store, **kw)
    store.con.close()
    return db


def _alerts(db, *options, client_factory=None):
    """The exit code and the output of ahd alerts on this database. Without --date, today is
    TODAY, so the runs of TODAY are not old."""
    kw = {} if client_factory is None else {"client_factory": client_factory}
    if not any(str(o).startswith("--date") for o in options):
        options = (*options, "--date", TODAY.isoformat())
    return main(["alerts", "--db", str(db), *options], **kw)


def _macro(db, run, **options):
    """The rows of the alerts() macro of the current views, as dicts."""
    store = Store.open(db)  # a write open puts in the current views
    try:
        names = "".join(f", {k} := ${k}" for k in options)
        cur = store.con.execute(f"FROM alerts($run{names})", {"run": run, **options})
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        store.con.close()


RULES = (
    "A large drop is more than 30% below the weekday average, and a large spike is more than "
    "50% above it. The weekday average is the average of the same weekday in the 12 weeks "
    "before. Adobe's anomaly detection flags the unusual values."
)


# ---- text -------------------------------------------------------------------------------


def test_text_lists_every_kind_of_alert(tmp_path, capsys):
    db = _db(tmp_path)
    assert _alerts(db, "--run", "1") == 0
    assert capsys.readouterr().out == (
        f"{RULES}\n"
        "\n"
        "Run 1, Suite One (rs1), status ok\n"
        "Alerts for 2026-11-06: 1 unusual value, 1 large drop, 1 large spike, 1 drop to zero\n"
        "3 of these alerts are new on 2026-11-06.\n"
        "- large spike: Spike (metrics/event4), weekday average 100, actual 200, change +100.0%, "
        "new on 2026-11-06\n"
        "- large drop: Drop (metrics/event3), weekday average 100, actual 50, change -50.0%, "
        "new on 2026-11-06\n"
        "- dropped to zero: Zero (metrics/event2), weekday average 100, actual 0, "
        "change -100.0%, new on 2026-11-06\n"
        # Adobe gave no range for the days before, so the flag may have started earlier.
        "- unusual: Flagged (metrics/event1), weekday average 100, actual 150, expected 100, "
        "change +50.0%, since 2026-11-06 or earlier, at least 1 day\n"
    )


def test_text_without_alerts_says_so_and_exits_with_4(tmp_path, capsys):
    db = _db(tmp_path, quiet=True)
    assert _alerts(db, "--run", "1", "--format", "text") == 4
    assert capsys.readouterr().out == (
        f"{RULES}\n"
        "\n"
        "Run 1, Suite One (rs1), status ok\n"
        "Alerts for 2026-11-06: 0 unusual values, 0 large drops, 0 large spikes, 0 drops to zero\n"
    )


def test_text_names_the_segment_the_method_and_the_thresholds(tmp_path, capsys):
    db = _db(tmp_path, segment="s300_mobile", name=None)
    options = ["--change-alert", "27.5", "--spike-alert", "120"]
    local = ["--anomaly-method", "weekday-iqr", "--anomaly-sensitivity", "high"]
    assert _alerts(db, "--run", "1", *options, *local) == 0
    out = capsys.readouterr().out
    assert out.startswith(
        "A large drop is more than 27.5% below the weekday average, and a large spike is more "
        "than 120% above it. The weekday average is the average of the same weekday in the 12 "
        "weeks before. The Weekday quartile range method with high sensitivity flags the "
        "unusual values. ahd computes this method from the daily values of the run.\n\n"
    )
    # A run without a stored suite name shows its ID.
    assert "\nRun 1, rs1, segment s300_mobile, status ok\n" in out


def test_the_text_line_is_the_alert_line_of_ahd_run(tmp_path, capsys):
    from ahd.cli import _alert_line

    db = _db(tmp_path)
    store = Store.open(db)
    lines = {
        m: _alert_line(store, 1, 0.3, 0.5, method=m)
        for m in ("adobe", "weekday-median", "weekday-trend")
    }
    store.con.close()
    for method, line in lines.items():
        _alerts(db, "--run", "1", "--anomaly-method", method)
        assert f"\n{line}\n" in capsys.readouterr().out


def test_ahd_run_and_ahd_alerts_print_the_same_line(tmp_path, capsys):
    """The alert line that ahd run prints after a run, and the one of ahd alerts, agree."""
    from fakes import fake_for

    cfg = tmp_path / "config.json"
    cfg.write_text(
        json.dumps({"org_id": "o@AdobeOrg", "client_id": "c", "secret": "s", "scopes": "x"}),
        encoding="utf-8",
    )
    events = [{"id": f"metrics/event{i}", "name": f"Event {i}"} for i in (1, 2, 3)] + [
        {"id": "metrics/occurrences", "name": "Occurrences", "type": "int"}
    ]
    dims = [{"id": "variables/page", "name": "Page", "allowedForReporting": True}]
    fake = fake_for("rs1", events, dims)
    db = tmp_path / "t.duckdb"
    run = ["run", "--config", str(cfg), "--db", str(db), "--rsid", "rs1", "--date", "2026-11-07"]
    local = ["--anomaly-method", "weekday-median", "--change-alert", "50"]
    code = main([*run, "--out", str(tmp_path / "o"), *local], client_factory=lambda c, **k: fake)
    assert code == 0
    [line] = [x for x in capsys.readouterr().out.splitlines() if x.startswith("Alerts for ")]
    _alerts(db, "--run", "1", *local)
    assert f"\n{line}\n" in capsys.readouterr().out


# ---- JSON -------------------------------------------------------------------------------


def _plain(value):
    """A value of the macro as the JSON output gives it."""
    if isinstance(value, date):
        return value.isoformat()
    return value


def test_json_holds_the_run_and_every_column_of_the_macro(tmp_path, capsys):
    db = _db(tmp_path)
    assert _alerts(db, "--run", "1", "--format", "json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["format_version"] == 1
    [run] = data["runs"]
    alerts = run.pop("alerts")
    # ahd 0.1.13 added the suite-wide events. This run has none.
    events = run.pop("events")
    assert events == []
    assert run == {
        "run_id": 1,
        "rsid": "rs1",
        "rsid_name": "Suite One",
        "segment_id": None,
        "status": "ok",
        "day": "2026-11-06",
        "method": "adobe",
        "method_name": "Adobe",
        "sensitivity": "normal",
        "threshold": 0.3,
        "spike_threshold": 0.5,
        "current_day": "2026-11-07",
        "stale": False,
        "failed_after": 0,
        "warning": None,
    }
    rows = _macro(db, 1)
    # Each alert has every column of the macro. Its run_id and day are those of the run. The
    # new alerts come first, and each part keeps the order of the macro.
    assert [a["alert"] for a in alerts] == [
        "large spike",
        "large drop",
        "dropped to zero",
        "unusual",
    ]
    rows = [r for r in rows if r["is_new"]] + [r for r in rows if not r["is_new"]]
    for alert, row in zip(alerts, rows, strict=True):
        run_id, day = row.pop("run_id"), row.pop("day")
        assert (run_id, day) == (1, YESTERDAY)
        assert alert == {k: _plain(v) for k, v in row.items()}
    assert alerts[3]["anomaly"] == "high" and alerts[3]["expected"] == 100.0
    assert alerts[1]["pct_vs_usual"] == -0.5
    drop = {k: alerts[1][k] for k in ("since", "days", "at_least", "is_new")}
    assert drop == {"since": "2026-11-06", "days": 1, "at_least": False, "is_new": True}
    assert (alerts[3]["days"], alerts[3]["at_least"], alerts[3]["is_new"]) == (1, True, False)


def test_a_large_write_to_a_closed_pipe_on_windows_ends_quietly(tmp_path, monkeypatch, capsys):
    # On Windows, a write to a pipe that the reader closed fails with EINVAL. Python 3.12 and
    # 3.13 then drop the text of a write larger than the buffer, so a second flush works.
    import errno

    from ahd import cli

    class ClosedPipe(io.RawIOBase):
        def __init__(self, fd):
            self.fd = fd
            self.closed_pipe = True

        def writable(self):
            return True

        def write(self, b):
            if self.closed_pipe:
                raise OSError(errno.EINVAL, "Invalid argument")
            return len(b)

        def fileno(self):
            return self.fd

    db = _db(tmp_path, names={cid: "Name " * 1500 for cid in KINDS})
    with (tmp_path / "out.txt").open("w") as f:
        raw = ClosedPipe(f.fileno())
        stdout = io.TextIOWrapper(io.BufferedWriter(raw), encoding="utf-8")
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(sys, "stdout", stdout)
        try:
            assert _alerts(db, "--run", "1", "--format", "json") == cli.EXIT_PIPE_CLOSED
            assert sys.stdout is stdout
        finally:
            raw.closed_pipe = False
            stdout.close()
    # The JSON is one write of more than 30 KB.
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("fmt", FORMATS)
def test_alerts_work_when_the_output_is_closed_at_start(tmp_path, monkeypatch, fmt):
    """e.g. ahd alerts ... >&- in a job that only needs the exit code, or under pythonw."""
    monkeypatch.setattr(sys, "stdout", None)
    assert _alerts(_db(tmp_path), "--run", "1", "--format", fmt) == 0
    assert sys.stdout is None


def test_json_without_alerts_has_the_run_with_an_empty_list(tmp_path, capsys):
    db = _db(tmp_path, quiet=True)
    assert _alerts(db, "--run", "1", "--format", "json") == 4
    [run] = json.loads(capsys.readouterr().out)["runs"]
    assert run["alerts"] == [] and run["run_id"] == 1


# Every pair but adobe with low or high, which the command refuses. test_cli.py checks that.
PAIRS = [(m, s) for m in ANOMALY_METHODS for s in SENSITIVITIES if m != "adobe" or s == "normal"]


@pytest.mark.parametrize(("method", "sensitivity"), PAIRS)
def test_json_agrees_with_the_macro_for_every_method_and_sensitivity(
    tmp_path, capsys, method, sensitivity
):
    db = _db(tmp_path)
    options = ["--anomaly-method", method, "--anomaly-sensitivity", sensitivity]
    code = _alerts(db, "--run", "1", "--format", "json", "--change-alert", "45", *options)
    [run] = json.loads(capsys.readouterr().out)["runs"]
    rows = _macro(db, 1, threshold=0.45, method=method, sensitivity=sensitivity)
    assert code == (0 if rows else 4)
    assert (run["method"], run["sensitivity"], run["threshold"]) == (method, sensitivity, 0.45)
    rows = [r for r in rows if r["is_new"]] + [r for r in rows if not r["is_new"]]
    fields = ("component_id", "alert", "anomaly", "days", "at_least", "is_new")
    assert [tuple(a[k] for k in fields) for a in run["alerts"]] == [
        tuple(r[k] for k in fields) for r in rows
    ]
    assert [a["since"] for a in run["alerts"]] == [r["since"].isoformat() for r in rows]


def test_json_keeps_numbers_that_are_not_finite_out(tmp_path):
    from ahd.alerts import json_value

    assert json_value(math.inf) is None and json_value(-math.inf) is None
    assert json_value(math.nan) is None
    assert json_value(1.5) == 1.5 and json_value(None) is None and json_value("x") == "x"
    assert json_value(YESTERDAY) == "2026-11-06"


def test_json_of_a_value_that_is_not_finite_is_valid_json(tmp_path, capsys):
    db = _db(tmp_path)
    con = duckdb.connect(str(db))
    con.execute(
        "UPDATE daily_values SET value = 'Infinity'::DOUBLE, expected = 'NaN'::DOUBLE "
        "WHERE component_id = 'metrics/event1' AND day = ?",
        [YESTERDAY],
    )
    con.close()
    assert _alerts(db, "--run", "1", "--format", "json") == 0
    out = capsys.readouterr().out
    assert "Infinity" not in out and "NaN" not in out
    alerts = json.loads(out)["runs"][0]["alerts"]
    flagged = next(a for a in alerts if a["component_id"] == "metrics/event1")
    assert flagged["value"] is None and flagged["expected"] is None
    assert flagged["pct_vs_usual"] is None
    # The text says so, and has no change for it.
    assert _alerts(db, "--run", "1") == 0
    line = (
        "- unusual: Flagged (metrics/event1), weekday average 100, actual no data, expected no "
        "data, since 2026-11-06 or earlier, at least 1 day\n"
    )
    assert line in capsys.readouterr().out


# ---- CSV --------------------------------------------------------------------------------

CSV_HEADER = [
    "run_id",
    "rsid",
    "rsid_name",
    "segment_id",
    "status",
    "day",
    "method",
    "method_name",
    "sensitivity",
    "threshold",
    "spike_threshold",
    "current_day",
    "stale",
    "failed_after",
    "alert",
    "anomaly",
    "component_id",
    "name",
    "group",
    "metric_type",
    "value",
    "usual",
    "expected",
    "pct_vs_usual",
    "since",
    "days",
    "at_least",
    "is_new",
    # ahd 0.1.13 added the event of each alert.
    "event",
]


def test_csv_has_one_header_and_one_row_per_alert(tmp_path, capsys):
    db = tmp_path / "ahd.duckdb"
    store = Store.open(db)
    _add_run(store)
    _add_run(store, rsid="rs2", name="Two")
    store.con.close()
    assert _alerts(db, "--rsid", "rs1", "--rsid", "rs2", "--format", "csv") == 0
    rows = list(csv.reader(io.StringIO(capsys.readouterr().out)))
    assert rows[0] == CSV_HEADER
    assert len(rows) == 1 + 4 + 4
    first = dict(zip(CSV_HEADER, rows[1]))
    assert first == {
        "run_id": "1",
        "rsid": "rs1",
        "rsid_name": "Suite One",
        "segment_id": "",
        "status": "ok",
        "day": "2026-11-06",
        "method": "adobe",
        "method_name": "Adobe",
        "sensitivity": "normal",
        "threshold": "0.3",
        "spike_threshold": "0.5",
        "current_day": "2026-11-07",
        "stale": "false",
        "failed_after": "0",
        # New alerts come first.
        "alert": "large spike",
        "anomaly": "",
        "component_id": "metrics/event4",
        "name": "Spike",
        "group": "custom_events",
        "metric_type": "",
        "value": "200.0",
        "usual": "100.0",
        "expected": "",
        "pct_vs_usual": "1.0",
        "since": "2026-11-06",
        "days": "1",
        "at_least": "false",
        "is_new": "true",
        "event": "",
    }
    assert [r[1] for r in rows[1:]] == ["rs1"] * 4 + ["rs2"] * 4
    # The flag comes last: it is not new.
    last = dict(zip(CSV_HEADER, rows[4]))
    assert (last["alert"], last["at_least"], last["is_new"]) == ("unusual", "true", "false")


FORMULAS = {
    "metrics/event1": '=HYPERLINK("https://example.com","Flagged")',
    "metrics/event2": "+1+2",
    "metrics/event3": "-2+3",
    "metrics/event4": "@SUM(A1:A2)",
    "metrics/event6": "\tTabbed",
}


def test_csv_keeps_text_that_looks_like_a_formula_as_text(tmp_path, capsys):
    db = _db(tmp_path, names=FORMULAS, name="=Suite\rOne")
    assert _alerts(db, "--run", "1", "--format", "csv") == 0
    rows = [dict(zip(CSV_HEADER, r)) for r in csv.reader(io.StringIO(capsys.readouterr().out))]
    by_id = {r["component_id"]: r for r in rows[1:]}
    for cid, name in FORMULAS.items():
        if cid in by_id:
            assert by_id[cid]["name"] == "'" + name
    assert set(by_id) == {"metrics/event1", "metrics/event2", "metrics/event3", "metrics/event4"}
    assert all(r["rsid_name"] == "'=Suite\rOne" for r in rows[1:])
    # Numbers stay numbers, also a negative one, and other text stays as it is.
    drop = by_id["metrics/event3"]
    assert drop["pct_vs_usual"] == "-0.5" and drop["value"] == "50.0"
    assert drop["alert"] == "large drop" and drop["rsid"] == "rs1"
    # The JSON keeps every value as it is.
    assert _alerts(db, "--run", "1", "--format", "json") == 0
    [run] = json.loads(capsys.readouterr().out)["runs"]
    assert run["rsid_name"] == "=Suite\rOne"
    assert {a["component_id"]: a["name"] for a in run["alerts"]}["metrics/event2"] == "+1+2"


@pytest.mark.parametrize(
    ("value", "cell"),
    [
        ("=1+1", "'=1+1"),
        ("+1", "'+1"),
        ("-1", "'-1"),
        ("@A1", "'@A1"),
        ("\tx", "'\tx"),
        ("\rx", "'\rx"),
        ("a=1", "a=1"),
        ("", ""),
        (-0.5, -0.5),
        (-3, -3),
        (True, "true"),
        (None, None),
    ],
)
def test_csv_value_quotes_only_text_that_starts_like_a_formula(value, cell):
    assert csv_value(value) == cell


def test_csv_without_alerts_is_only_the_header(tmp_path, capsys):
    db = _db(tmp_path, quiet=True)
    assert _alerts(db, "--run", "1", "--format", "csv") == 4
    assert capsys.readouterr().out == ",".join(CSV_HEADER) + "\n"


# ---- names from Adobe stay inert --------------------------------------------------------

ODD = {
    "metrics/event1": 'Big "quoted", <b>bold</b>',
    "metrics/event3": '=HYPERLINK("x")\nsecond line\r\x1b[31mred\u202e',
    "metrics/event4": "日本サイト\u2028next",
}


def test_odd_names_stay_inert_in_every_format(tmp_path, capsys):
    db = _db(tmp_path, names=ODD, name="Suite\n\x07 One")
    assert _alerts(db, "--run", "1", "--format", "json") == 0
    out = capsys.readouterr().out
    assert out.isascii()  # every other character is escaped
    alerts = {a["component_id"]: a["name"] for a in json.loads(out)["runs"][0]["alerts"]}
    assert all(alerts[k] == v for k, v in ODD.items())

    assert _alerts(db, "--run", "1", "--format", "csv") == 0
    rows = list(csv.reader(io.StringIO(capsys.readouterr().out, newline="")))
    found = {r[CSV_HEADER.index("component_id")]: r[CSV_HEADER.index("name")] for r in rows[1:]}
    # A name that starts with = gets a quote in front, so that a spreadsheet keeps it as text.
    assert all(found[k] == ("'" + v if v.startswith("=") else v) for k, v in ODD.items())
    assert len(rows) == 5

    assert _alerts(db, "--run", "1") == 0
    text = capsys.readouterr().out
    # One line for each alert, and no character that moves the cursor or colors the terminal.
    lines = text.splitlines()
    assert len(lines) == 3 + 1 + 1 + 4
    assert "Run 1, Suite?? One (rs1), status ok" in lines
    assert '- large drop: =HYPERLINK("x")?second line??[31mred? (metrics/event3)' in text
    assert "- large spike: 日本サイト?next (metrics/event4)" in text
    assert not any(c in text for c in "\x1b\x07\r\u202e\u2028")


class _Terminal(io.StringIO):
    """Output that says it is a terminal."""

    def isatty(self):
        return True


def test_csv_on_a_terminal_keeps_names_inert(tmp_path, capsys, monkeypatch):
    db = _db(tmp_path, names=ODD, name="Suite\n\x07 One")
    screen = _Terminal()
    monkeypatch.setattr(sys, "stdout", screen)
    assert _alerts(db, "--run", "1", "--format", "csv") == 0
    out = screen.getvalue()
    assert not any(c in out for c in "\x1b\x07\r\u202e\u2028")
    rows = list(csv.reader(io.StringIO(out, newline="")))
    assert len(rows) == 5  # one header and one row for each alert, with no broken line
    found = {r[CSV_HEADER.index("component_id")]: r[CSV_HEADER.index("name")] for r in rows[1:]}
    assert found["metrics/event3"] == '\'=HYPERLINK("x")?second line??[31mred?'
    assert all(r[CSV_HEADER.index("rsid_name")] == "Suite?? One" for r in rows[1:])


# ---- old runs ---------------------------------------------------------------------------


def _run_dict(store, rid):
    """The row of a run as choose_runs gives it."""
    cur = store.con.execute("FROM runs WHERE run_id = ?", [rid])
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, cur.fetchone()))


def _bare_run(store, today, zone="UTC", status="ok", rsid="rs1", segment=None):
    rid = store.create_run(rsid, "One", segment, today, zone, "0.1")
    if status != "running":
        store.finish_run(rid, status)
    return rid


def test_a_run_is_old_by_the_day_of_its_suite_not_of_the_computer(tmp_path):
    # Auckland is UTC+13 in November. At 12:00 UTC on Nov 6 it is Nov 7 there.
    store = Store.open(tmp_path / "ahd.duckdb")
    rid = _bare_run(store, date(2026, 11, 7), "Pacific/Auckland")
    age = run_age(store, _run_dict(store, rid), datetime(2026, 11, 6, 12, tzinfo=UTC))
    assert age == {
        "current_day": date(2026, 11, 7),
        "stale": False,
        "failed_after": 0,
        "warning": None,
    }


def test_a_run_behind_utc_is_not_old_on_its_own_day(tmp_path):
    # Los Angeles at 05:00 UTC on Nov 7 is still Nov 6.
    store = Store.open(tmp_path / "ahd.duckdb")
    rid = _bare_run(store, date(2026, 11, 6), "America/Los_Angeles")
    age = run_age(store, _run_dict(store, rid), datetime(2026, 11, 7, 5, tzinfo=UTC))
    assert age["current_day"] == date(2026, 11, 6) and age["stale"] is False


def test_an_old_run_says_when_its_alerts_are_from_and_counts_the_failed_runs_after_it(tmp_path):
    store = Store.open(tmp_path / "ahd.duckdb")
    _bare_run(store, date(2026, 11, 4), status="failed")  # before it: not counted
    rid = _bare_run(store, date(2026, 11, 5))
    _bare_run(store, date(2026, 11, 6), status="failed")
    _bare_run(store, date(2026, 11, 7), status="failed")
    _bare_run(store, date(2026, 11, 7), status="running")  # still running: not counted
    _bare_run(store, date(2026, 11, 7), status="failed", segment="s1")  # another segment
    _bare_run(store, date(2026, 11, 7), status="failed", rsid="rs2")  # another suite
    age = run_age(store, _run_dict(store, rid), datetime(2026, 11, 7, 8, tzinfo=UTC))
    assert age["stale"] is True and age["failed_after"] == 2
    assert age["warning"] == (
        "The latest finished run of rs1 has the alerts of 2026-11-04, but yesterday was "
        "2026-11-06 in the time zone of the suite. 2 later runs of it failed."
    )


def test_a_failed_run_of_the_same_day_made_later_counts(tmp_path):
    store = Store.open(tmp_path / "ahd.duckdb")
    rid = _bare_run(store, date(2026, 11, 5))
    _bare_run(store, date(2026, 11, 5), status="failed")
    age = run_age(store, _run_dict(store, rid), datetime(2026, 11, 7, 8, tzinfo=UTC))
    assert age["warning"].endswith(" 1 later run of it failed.")


NOW = datetime(2026, 11, 7, 8, tzinfo=UTC)


def _no_start(store, rid):
    """A run without a start time, as ahd import can give from an older layout."""
    store.con.execute("UPDATE runs SET started_at = NULL WHERE run_id = ?", [rid])


def test_a_failed_run_without_a_start_time_ranks_last_of_its_day(tmp_path):
    # latest_runs sorts a missing start time last, so this failed run is older than the run.
    store = Store.open(tmp_path / "ahd.duckdb")
    rid = _bare_run(store, date(2026, 11, 5))
    failed = _bare_run(store, date(2026, 11, 5), status="failed")
    _no_start(store, failed)
    assert run_age(store, _run_dict(store, rid), NOW)["failed_after"] == 0


def test_a_failed_run_counts_when_the_run_has_no_start_time(tmp_path):
    store = Store.open(tmp_path / "ahd.duckdb")
    rid = _bare_run(store, date(2026, 11, 5))
    _bare_run(store, date(2026, 11, 5), status="failed")
    _no_start(store, rid)
    assert run_age(store, _run_dict(store, rid), NOW)["failed_after"] == 1


def test_the_run_number_decides_when_neither_run_has_a_start_time(tmp_path):
    store = Store.open(tmp_path / "ahd.duckdb")
    before = _bare_run(store, date(2026, 11, 5), status="failed")
    rid = _bare_run(store, date(2026, 11, 5))
    after = _bare_run(store, date(2026, 11, 5), status="failed")
    for r in (before, rid, after):
        _no_start(store, r)
    assert run_age(store, _run_dict(store, rid), NOW)["failed_after"] == 1


def test_an_old_run_without_failed_runs_after_it_says_only_the_days(tmp_path):
    store = Store.open(tmp_path / "ahd.duckdb")
    rid = _bare_run(store, date(2026, 11, 5))
    age = run_age(store, _run_dict(store, rid), datetime(2026, 11, 7, 8, tzinfo=UTC))
    assert age["warning"] == (
        "The latest finished run of rs1 has the alerts of 2026-11-04, but yesterday was "
        "2026-11-06 in the time zone of the suite."
    )


@pytest.mark.parametrize("zone", ["Mars/Base", None])
def test_an_unknown_time_zone_does_not_make_a_run_old(tmp_path, zone):
    store = Store.open(tmp_path / "ahd.duckdb")
    rid = _bare_run(store, date(2026, 11, 1), zone)
    age = run_age(store, _run_dict(store, rid), datetime(2026, 11, 7, 8, tzinfo=UTC))
    assert age == {"current_day": None, "stale": None, "failed_after": 0, "warning": None}


def test_a_given_date_sets_today_for_every_suite(tmp_path, capsys):
    db = _db(tmp_path)
    assert _alerts(db, "--rsid", "rs1", "--format", "json", "--date", "2026-11-09") == 0
    run = json.loads(capsys.readouterr().out)["runs"][0]
    assert run["current_day"] == "2026-11-09" and run["stale"] is True
    assert run["warning"].startswith(
        "The latest finished run of rs1 has the alerts of 2026-11-06, but yesterday was 2026-11-08"
    )


def test_a_fresh_run_has_no_warning(tmp_path, capsys):
    assert _alerts(_db(tmp_path), "--rsid", "rs1", "--format", "json") == 0
    run = json.loads(capsys.readouterr().out)["runs"][0]
    assert run["stale"] is False and run["warning"] is None and run["failed_after"] == 0


def test_without_a_date_today_comes_from_the_clock(tmp_path, capsys, monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 20, 8, tzinfo=UTC)

    monkeypatch.setattr("ahd.cli.datetime", Clock)
    assert main(["alerts", "--db", str(_db(tmp_path)), "--run", "1", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["runs"][0]["current_day"] == "2026-11-20"


def test_the_text_warns_under_the_heading_of_an_old_run(tmp_path, capsys):
    _alerts(_db(tmp_path), "--rsid", "rs1", "--date", "2026-11-09")
    lines = capsys.readouterr().out.splitlines()
    heading = next(i for i, line in enumerate(lines) if line.startswith("Run 1,"))
    assert lines[heading + 1] == (
        "Warning: The latest finished run of rs1 has the alerts of 2026-11-06, but yesterday "
        "was 2026-11-08 in the time zone of the suite."
    )


def test_an_old_run_keeps_the_exit_code_of_its_alerts(tmp_path):
    assert _alerts(_db(tmp_path, quiet=True), "--rsid", "rs1", "--date", "2026-11-09") == 4


def test_the_warning_keeps_an_odd_rsid_inert(tmp_path, capsys):
    _alerts(_db(tmp_path, rsid="rs\u202eevil"), "--rsid", "rs\u202eevil", "--date", "2026-11-09")
    out = capsys.readouterr().out
    assert "Warning: The latest finished run of rs?evil has" in out
    assert "\u202e" not in out


def test_the_date_must_be_a_date(tmp_path):
    with pytest.raises(SystemExit) as stop:
        _alerts(_db(tmp_path), "--rsid", "rs1", "--date", "11/09/2026")
    assert stop.value.code == 2


# ---- which runs -------------------------------------------------------------------------


@pytest.fixture
def many(tmp_path):
    """rs1 without a segment (run 1), and an older run date in run 4. rs2 has no alerts (run 2).
    rs1 with a segment (run 3). rs3 only failed (run 5)."""
    db = tmp_path / "ahd.duckdb"
    store = Store.open(db)
    _add_run(store)
    _add_run(store, rsid="rs2", name="Another", quiet=True)
    _add_run(store, segment="s300_mobile", quiet=True)
    _add_run(store, today=TODAY - timedelta(days=30), quiet=True)
    rid = _add_run(store, rsid="rs3", name="Failed")
    store.finish_run(rid, "failed")
    store.con.close()
    return db


def _runs(capsys):
    return [(r["run_id"], r["rsid"]) for r in json.loads(capsys.readouterr().out)["runs"]]


def test_rsid_takes_the_latest_run_by_run_date(many, capsys):
    # Run 4 has a higher number, but an older run date.
    assert _alerts(many, "--rsid", "rs1", "--format", "json") == 0
    assert _runs(capsys) == [(1, "rs1")]


def test_several_rsids_keep_their_order_and_count_once(many, capsys):
    assert _alerts(many, "--rsid", "rs2", "--rsid", "rs1", "--rsid", "rs2", "--format", "json") == 0
    assert _runs(capsys) == [(2, "rs2"), (1, "rs1")]


def test_segment_picks_the_runs_with_it(many, capsys):
    assert _alerts(many, "--rsid", "rs1", "--segment", "s300_mobile", "--format", "json") == 4
    assert _runs(capsys) == [(3, "rs1")]


def test_all_suites_uses_every_suite_with_a_finished_run_by_name(many, capsys):
    assert _alerts(many, "--all-suites", "--format", "json") == 0
    assert _runs(capsys) == [(2, "rs2"), (1, "rs1")]
    assert _alerts(many, "--all-suites", "--segment", "s300_mobile", "--format", "json") == 4
    assert _runs(capsys) == [(3, "rs1")]


def test_a_suite_without_a_finished_run_is_an_error_but_the_others_are_printed(many, capsys):
    code = _alerts(many, "--rsid", "rs1", "--rsid", "rs3", "--rsid", "typo", "--format", "json")
    assert code == 1
    captured = capsys.readouterr()
    assert [r["rsid"] for r in json.loads(captured.out)["runs"]] == ["rs1"]
    assert captured.err == (
        f"Error: {many} holds no finished run of rs3 without a segment. Check the report suite "
        "ID, or run ahd runs to list the runs.\n"
        f"Error: {many} holds no finished run of typo without a segment. Check the report suite "
        "ID, or run ahd runs to list the runs.\n"
    )
    assert _alerts(many, "--rsid", "rs2", "--segment", "s300_other") == 1
    assert "holds no finished run of rs2 with the segment s300_other." in capsys.readouterr().err


def test_all_suites_without_a_finished_run_is_an_error(many, capsys):
    assert _alerts(many, "--all-suites", "--segment", "s300_none") == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"Error: {many} holds no finished run with the segment s300_none.\n"


@pytest.mark.parametrize(
    ("run", "message"),
    [
        ("9", "There is no stored run 9."),
        (
            "5",
            'Run 5 ended "failed", so it has no alerts. Choose a run that ended "ok" or "partial".',
        ),
    ],
)
def test_a_run_that_has_no_alerts_to_read_is_an_error(many, capsys, run, message):
    assert _alerts(many, "--run", run) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == f"Error: {message}\n"


def test_a_running_run_is_an_error(many, capsys):
    con = duckdb.connect(str(many))
    con.execute("UPDATE runs SET status = 'running' WHERE run_id = 1")
    con.close()
    assert _alerts(many, "--run", "1") == 1
    assert 'Run 1 is still marked "running"' in capsys.readouterr().err


def test_a_partial_run_has_alerts(many, capsys):
    con = duckdb.connect(str(many))
    con.execute("UPDATE runs SET status = 'partial' WHERE run_id = 1")
    con.close()
    assert _alerts(many, "--run", "1", "--format", "json") == 0
    assert json.loads(capsys.readouterr().out)["runs"][0]["status"] == "partial"


def test_segment_does_not_go_with_run(many, capsys):
    # Two options that do not go together are a wrong option, with exit code 2 and the usage.
    with pytest.raises(SystemExit) as stop:
        _alerts(many, "--run", "1", "--segment", "s300_mobile")
    assert stop.value.code == 2
    assert capsys.readouterr().err.endswith(
        "error: --segment works only with --rsid or --all-suites. The run that --run names "
        "already has its segment.\n"
    )


@pytest.mark.parametrize(
    "options",
    [[], ["--run", "1", "--rsid", "rs1"], ["--rsid", "rs1", "--all-suites"], ["--run", "x"]],
)
def test_the_runs_are_chosen_one_way_only(many, capsys, options):
    with pytest.raises(SystemExit) as e:
        _alerts(many, *options)
    assert e.value.code == 2
    assert "usage:" in capsys.readouterr().err


def test_it_makes_no_api_call_and_no_new_file(tmp_path, capsys):
    def no_sign_in(*a, **k):
        raise AssertionError("ahd alerts must not sign in.")

    db = tmp_path / "new" / "ahd.duckdb"
    assert _alerts(db, "--run", "1", client_factory=no_sign_in) == 1
    assert "There is no database at" in capsys.readouterr().err
    assert not db.parent.exists()
    db = _db(tmp_path)
    assert _alerts(db, "--all-suites", client_factory=no_sign_in) == 0


# ---- thresholds -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("option", "percent", "share"),
    [
        ("--change-alert", "27.5", 0.275),
        ("--change-alert", "100", 1.0),
        ("--change-alert", "0.001", 0.00001),
        ("--spike-alert", "200", 2.0),
        ("--spike-alert", "33.3333333333333333333333333333", 0.333333333333333333333333333333),
    ],
)
def test_a_percent_becomes_the_exact_share_of_the_macro(tmp_path, capsys, option, percent, share):
    db = _db(tmp_path)
    _alerts(db, "--run", "1", "--format", "json", option, percent)
    [run] = json.loads(capsys.readouterr().out)["runs"]
    key = "threshold" if option == "--change-alert" else "spike_threshold"
    assert run[key] == share  # the same float as the share written as a decimal


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--change-alert", "0"),
        ("--change-alert", "100.5"),
        ("--change-alert", "-5"),
        ("--change-alert", "1e1"),
        ("--change-alert", "nan"),
        ("--change-alert", "thirty"),
        ("--spike-alert", "0"),
        ("--spike-alert", "201"),
        ("--spike-alert", "inf"),
    ],
)
def test_a_percent_out_of_range_is_a_usage_error(tmp_path, capsys, option, value):
    with pytest.raises(SystemExit) as e:
        _alerts(tmp_path / "t.duckdb", "--run", "1", option, value)
    assert e.value.code == 2
    assert "is not a percent above 0 and up to" in capsys.readouterr().err


def test_the_thresholds_change_the_alerts(tmp_path, capsys):
    db = _db(tmp_path)
    # The drop is 50 percent and the spike 100 percent.
    _alerts(db, "--run", "1", "--format", "json", "--change-alert", "50", "--spike-alert", "100")
    [run] = json.loads(capsys.readouterr().out)["runs"]
    # The drop to zero is new, so it comes first.
    assert [a["alert"] for a in run["alerts"]] == ["dropped to zero", "unusual"]


# ---- the anomaly variables --------------------------------------------------------------


def test_the_anomaly_variables_choose_the_method(tmp_path, capsys, monkeypatch):
    db = _db(tmp_path)
    monkeypatch.setenv("AHD_ANOMALY_METHOD", "weekday-trend")
    monkeypatch.setenv("AHD_ANOMALY_SENSITIVITY", "low")
    _alerts(db, "--run", "1", "--format", "json")
    [run] = json.loads(capsys.readouterr().out)["runs"]
    assert (run["method"], run["method_name"], run["sensitivity"]) == (
        "weekday-trend",
        "Weekday trend",
        "low",
    )
    _alerts(db, "--run", "1", "--format", "json", "--anomaly-sensitivity", "normal")
    assert json.loads(capsys.readouterr().out)["runs"][0]["sensitivity"] == "normal"


# ---- files with older views, and files that others hold open -----------------------------


def _with_old_views(db, sql):
    con = duckdb.connect(str(db))
    con.execute(sql)
    con.close()


def _local(db, capsys):
    options = ["--anomaly-method", "weekday-median", "--anomaly-sensitivity", "high"]
    code = _alerts(db, "--run", "1", "--format", "json", *options)
    return code, json.loads(capsys.readouterr().out)["runs"][0]["alerts"]


def test_a_file_with_the_views_of_0_1_8_gets_the_current_rules(tmp_path, capsys):
    """0.1.8 wrote views version 4. Its macros take no sensitivity, and ahd alerts must not
    write to the file to get the current ones."""
    new = _db(tmp_path / "new")
    old = tmp_path / "old.duckdb"
    shutil.copy(new, old)
    views = (FIXTURES / "views_0_1_8.sql").read_text(encoding="utf-8")
    _with_old_views(old, views + "\nUPDATE views_version SET version = 4;")
    assert _local(old, capsys) == _local(new, capsys)
    assert _local(old, capsys)[0] == 0
    # The file keeps its old views: the command did not write to it.
    con = duckdb.connect(str(old), read_only=True)
    try:
        assert con.execute("FROM views_version").fetchall() == [(4,)]
        with pytest.raises(duckdb.BinderException):
            con.execute("FROM alerts(1, sensitivity := 'high')")
    finally:
        con.close()


def test_a_file_without_any_views_gets_the_current_rules(tmp_path, capsys):
    """ahd 0.1.2 and earlier wrote no views and no views_version table."""
    db = _db(tmp_path)
    expected = _local(db, capsys)
    _with_old_views(
        db,
        "DROP MACRO TABLE alerts; DROP MACRO TABLE anomaly_days; DROP VIEW latest_runs; "
        "DROP VIEW component_days; DROP TABLE views_version;",
    )
    assert _local(db, capsys) == expected
    assert _alerts(db, "--all-suites", "--format", "json") == 0
    assert [r["run_id"] for r in json.loads(capsys.readouterr().out)["runs"]] == [1]


def test_the_current_views_are_made_for_the_session_only(tmp_path):
    from ahd.store import temporary_views

    sql = temporary_views()
    statements = [
        s.strip()
        for s in "\n".join(
            line for line in sql.splitlines() if not line.lstrip().startswith("--")
        ).split(";")
        if s.strip()
    ]
    assert len(statements) == 5
    assert all(s.startswith("CREATE OR REPLACE TEMP ") for s in statements)


def test_a_drop_in_the_views_is_left_out_of_the_session(tmp_path, monkeypatch, capsys):
    from ahd.store import _views

    db = _db(tmp_path)
    monkeypatch.setattr(
        "ahd.store._views", lambda: _views() + "\nDROP MACRO TABLE IF EXISTS gone;\n"
    )
    assert _alerts(db, "--run", "1") == 0


def _hold(db, read_only):
    """Another process that holds the file open until it is killed. It prints a line when the
    file is open, and the caller waits for that line."""
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                f"import duckdb, time; c = duckdb.connect({str(db)!r}, read_only={read_only}); "
                "print('ready', flush=True); time.sleep(60)"
            ),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )


def test_it_reads_a_file_that_another_reader_holds_open(tmp_path, capsys):
    db = _db(tmp_path)
    with _hold(db, read_only=True) as holder:
        try:
            ready = holder.stdout.readline().strip()
            assert ready == "ready"
            assert _alerts(db, "--run", "1") == 0
            assert "Alerts for 2026-11-06" in capsys.readouterr().out
        finally:
            holder.kill()


def test_a_file_that_a_writer_holds_is_a_plain_error(tmp_path, capsys):
    db = _db(tmp_path)
    with _hold(db, read_only=False) as holder:
        try:
            ready = holder.stdout.readline().strip()
            assert ready == "ready"
            assert _alerts(db, "--run", "1") == 1
            captured = capsys.readouterr()
            assert captured.out == ""
            assert "is in use by another program" in captured.err
            assert captured.err.count("\n") == 1
        finally:
            holder.kill()


# ---- since when an alert has held -------------------------------------------------------


def _held(store, days_zero):
    """A run whose event2 has been 0 for days_zero days and whose event3 dropped on each of the
    last 8 days, with the components of KINDS."""
    rid = _add_run(store)
    store.con.execute(
        "UPDATE daily_values SET value = 0 WHERE run_id = ? AND component_id = 'metrics/event2'"
        " AND day >= ?",
        [rid, YESTERDAY - timedelta(days=days_zero - 1)],
    )
    store.con.execute(
        "UPDATE daily_values SET value = 50 WHERE run_id = ? AND component_id = 'metrics/event3'"
        " AND day >= ?",
        [rid, YESTERDAY - timedelta(days=7)],
    )
    return rid


def test_text_says_since_when_each_alert_held(tmp_path, capsys):
    db = tmp_path / "ahd.duckdb"
    store = Store.open(db)
    _held(store, 9)
    store.con.close()
    assert _alerts(db, "--run", "1") == 0
    lines = capsys.readouterr().out.splitlines()
    start = lines.index("1 of these alerts is new on 2026-11-06.")
    assert lines[start + 1 :] == [
        (
            "- large spike: Spike (metrics/event4), weekday average 100, actual 200, change "
            "+100.0%, new on 2026-11-06"
        ),
        # The others keep the order of the macro.
        (
            "- unusual: Flagged (metrics/event1), weekday average 100, actual 150, expected 100, "
            "change +50.0%, since 2026-11-06 or earlier, at least 1 day"
        ),
        # A drop can be judged only on the last 7 days, which have a weekday average.
        (
            "- large drop: Drop (metrics/event3), weekday average 95.83, actual 50, change -47.8%, "
            "since 2026-10-31 or earlier, at least 7 days"
        ),
        # A zero can be judged on every day, so its start is known.
        (
            "- dropped to zero: Zero (metrics/event2), weekday average 91.67, actual 0, change "
            "-100.0%, since 2026-10-29, 9 days"
        ),
    ]


def test_json_and_csv_say_since_when_each_alert_held(tmp_path, capsys):
    db = tmp_path / "ahd.duckdb"
    store = Store.open(db)
    _held(store, 9)
    store.con.close()
    assert _alerts(db, "--run", "1", "--format", "json") == 0
    alerts = json.loads(capsys.readouterr().out)["runs"][0]["alerts"]
    got = {a["component_id"]: (a["since"], a["days"], a["at_least"], a["is_new"]) for a in alerts}
    assert got == {
        "metrics/event4": ("2026-11-06", 1, False, True),
        "metrics/event1": ("2026-11-06", 1, True, False),
        "metrics/event3": ("2026-10-31", 7, True, False),
        "metrics/event2": ("2026-10-29", 9, False, False),
    }
    assert list(got) == ["metrics/event4", "metrics/event1", "metrics/event3", "metrics/event2"]
    assert _alerts(db, "--run", "1", "--format", "csv") == 0
    rows = list(csv.DictReader(io.StringIO(capsys.readouterr().out)))
    assert [
        (r["component_id"], r["since"], r["days"], r["at_least"], r["is_new"]) for r in rows
    ] == [
        (cid, *[str(v).lower() if isinstance(v, bool) else str(v) for v in want])
        for cid, want in got.items()
    ]


@pytest.mark.parametrize(("new", "line"), [(0, "0 of these alerts are new"), (1, "1 of these")])
def test_the_count_of_new_alerts_reads_well(new, line):
    from ahd.alerts import new_line

    assert new_line(date(2026, 11, 6), new).startswith(line)


# ---- suite-wide events ------------------------------------------------------------------


def _event_db(tmp_path, zeros=12, quiet=7, earlier=0, names=None):
    """A run with zeros components at 0 yesterday, earlier more at 0 since 3 days, one large
    drop, one flag and quiet components at their weekday average of 100. With the defaults, the
    zeros are an event: 12 of the 21 components that collected data."""
    names = names or {}
    db = tmp_path / "ahd.duckdb"
    store = Store.open(db)
    rid = store.create_run("rs1", "Suite One", None, TODAY, "UTC", "0.1")
    kinds = (
        [("zero", 1)] * zeros
        + [("zero", 3)] * earlier
        + [("drop", 1), ("flag", 1)]
        + [("quiet", 1)] * quiet
    )
    comps, values = [], []
    for n, (kind, days) in enumerate(kinds, start=1):
        cid = f"metrics/event{n}"
        label = names.get(cid, f"{kind.title()} {n}")
        comps.append(Component(cid, label, "metric", "custom_events", n, Column(cid, cid), True))
        for i in range(91):
            day = TODAY - timedelta(days=91 - i)
            back = (TODAY - day).days
            if kind == "flag" and back == 1:
                values.append(DailyValue(cid, day, 150.0, 100.0, 120.0, 80.0, True))
                continue
            v = 100.0
            if kind == "zero" and back <= days:
                v = 0.0
            elif kind == "drop" and back == 1:
                v = 50.0
            values.append(DailyValue(cid, day, v, None, None, None, None))
    store.add_components(rid, comps)
    store.add_daily_values(rid, values)
    store.finish_run(rid, "ok")
    store.con.close()
    return db


def _by_id(count):
    """The numbers 1 to count in the order of their component IDs, as the macro orders them."""
    return sorted(range(1, count + 1), key=lambda n: f"metrics/event{n}")


def _zero_line(n):
    return (
        f"- dropped to zero: Zero {n} (metrics/event{n}), weekday average 100, actual 0, "
        "change -100.0%, new on 2026-11-06"
    )


OTHER_LINES = [
    (
        "- large drop: Drop 13 (metrics/event13), weekday average 100, actual 50, change "
        "-50.0%, new on 2026-11-06"
    ),
    (
        "- unusual: Flag 14 (metrics/event14), weekday average 100, actual 150, expected 100, "
        "change +50.0%, since 2026-11-06 or earlier, at least 1 day"
    ),
]


def test_text_starts_with_the_event_and_lists_its_first_5_alerts(tmp_path, capsys):
    db = _event_db(tmp_path)
    assert _alerts(db, "--run", "1") == 0
    assert capsys.readouterr().out == "\n".join(
        [
            RULES,
            "",
            "Run 1, Suite One (rs1), status ok",
            "Alerts for 2026-11-06: 1 unusual value, 1 large drop, 0 large spikes, 12 drops to zero",
            "13 of these alerts are new on 2026-11-06.",
            (
                "Suite-wide event: 12 of the 21 components that collected data had a value of 0 "
                "on 2026-11-06. For all of them, this started on 2026-11-06, so it is new."
            ),
            *[_zero_line(n) for n in _by_id(12)[:5]],
            "7 more alerts of this event are not listed. Add --every-alert to list them.",
            "Other alerts:",
            *OTHER_LINES,
            "",
        ]
    )


def test_every_alert_lists_the_whole_event(tmp_path, capsys):
    db = _event_db(tmp_path)
    assert _alerts(db, "--run", "1", "--every-alert") == 0
    lines = capsys.readouterr().out.splitlines()
    start = lines.index("13 of these alerts are new on 2026-11-06.")
    assert lines[start + 2 :] == [
        *[_zero_line(n) for n in _by_id(12)],
        "Other alerts:",
        *OTHER_LINES,
    ]


def test_text_without_an_event_reads_as_before(tmp_path, capsys):
    # 10 zeros of the 21 components that collected data are fewer than half of them.
    db = _event_db(tmp_path, zeros=10, quiet=9)
    assert _alerts(db, "--run", "1", "--every-alert") == 0
    with_option = capsys.readouterr().out
    assert _alerts(db, "--run", "1") == 0
    out = capsys.readouterr().out
    assert out == with_option
    assert "Suite-wide event" not in out and "Other alerts" not in out
    # As in 0.1.12: the new alerts first, each part in the order of the macro.
    drop, flag = (line.replace("13", "11").replace("14", "12") for line in OTHER_LINES)
    assert out.splitlines()[5:] == [drop, *[_zero_line(n) for n in _by_id(10)], flag]


def test_an_event_with_mixed_starts_names_the_most_common_one(tmp_path, capsys):
    db = _event_db(tmp_path, zeros=4, earlier=8, quiet=7)
    assert _alerts(db, "--run", "1") == 0
    out = capsys.readouterr().out
    assert (
        "Suite-wide event: 12 of the 21 components that collected data had a value of 0 on "
        "2026-11-06. For 8 of them, this started on 2026-11-04. For the other 4, it started "
        "on other days.\n"
    ) in out


def test_the_event_sentence_reads_well_for_each_case():
    from ahd.alerts import Rules, event_text

    day = date(2026, 11, 11)
    rules = Rules(0.275, 1.2)

    def text(event, **kw):
        e = {
            "event": event,
            "alerts": 97,
            "components": 99,
            "since": date(2026, 11, 5),
            "at_least": False,
            "since_alerts": 97,
            "new": 0,
            **kw,
        }
        return event_text(e, day, rules)

    head = "97 of the 99 components that collected data had a value"
    assert text("drop", at_least=True) == (
        f"{head} more than 27.5% below the weekday average on 2026-11-11. For all of them, "
        "this started on 2026-11-05 or earlier."
    )
    assert text("spike", since_alerts=96).endswith(
        "more than 120% above the weekday average on 2026-11-11. For 96 of them, this started "
        "on 2026-11-05. For the other one, it started on another day."
    )
    assert text("low").startswith(f"{head} below the expected range on 2026-11-11.")
    assert text("high").startswith(f"{head} above the expected range on 2026-11-11.")
    assert text("zero", since=day, new=97).endswith(
        "For all of them, this started on 2026-11-11, so it is new."
    )
    # A start on the day that may be earlier is not new.
    assert text("zero", since=day, at_least=True).endswith("on 2026-11-11 or earlier.")


def test_json_adds_the_events_and_keeps_every_alert(tmp_path, capsys):
    db = _event_db(tmp_path)
    assert _alerts(db, "--run", "1", "--format", "json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["format_version"] == 1
    [run] = data["runs"]
    assert run["events"] == [
        {
            "event": "zero",
            "alerts": 12,
            "components": 21,
            "since": "2026-11-06",
            "at_least": False,
            "since_alerts": 12,
            "new": 12,
            "text": "12 of the 21 components that collected data had a value of 0 on "
            "2026-11-06. For all of them, this started on 2026-11-06, so it is new.",
        }
    ]
    # Every alert stays in the list, in the order of 0.1.12. Without the new field, each one is
    # what 0.1.12 gave.
    rows = _macro(db, 1)
    rows = [r for r in rows if r["is_new"]] + [r for r in rows if not r["is_new"]]
    assert len(run["alerts"]) == len(rows) == 14
    for alert, row in zip(run["alerts"], rows, strict=True):
        row.pop("run_id"), row.pop("day")
        assert alert == {k: _plain(v) for k, v in row.items()}
    assert [a["event"] for a in run["alerts"]] == [None] + ["zero"] * 12 + [None]
    old = {k for k in run["alerts"][0] if k != "event"}
    assert old == set(ALERT_FIELDS_0_1_12)


ALERT_FIELDS_0_1_12 = (
    "alert",
    "anomaly",
    "component_id",
    "name",
    "group",
    "metric_type",
    "value",
    "usual",
    "expected",
    "pct_vs_usual",
    "since",
    "days",
    "at_least",
    "is_new",
)


def test_json_without_an_event_has_an_empty_list(tmp_path, capsys):
    db = _event_db(tmp_path, zeros=9)
    assert _alerts(db, "--run", "1", "--format", "json") == 0
    [run] = json.loads(capsys.readouterr().out)["runs"]
    assert run["events"] == []
    assert {a["event"] for a in run["alerts"]} == {None}


def test_csv_adds_the_event_of_each_alert_at_the_end(tmp_path, capsys):
    db = _event_db(tmp_path)
    assert _alerts(db, "--run", "1", "--format", "csv") == 0
    rows = list(csv.reader(io.StringIO(capsys.readouterr().out)))
    assert rows[0] == CSV_HEADER and rows[0][-1] == "event"
    assert len(rows) == 1 + 14
    assert [r[-1] for r in rows[1:]] == [""] + ["zero"] * 12 + [""]


@pytest.mark.parametrize(("method", "sensitivity"), PAIRS)
def test_the_events_of_the_json_are_those_of_the_macro(tmp_path, capsys, method, sensitivity):
    db = _event_db(tmp_path)
    options = ["--anomaly-method", method, "--anomaly-sensitivity", sensitivity]
    assert _alerts(db, "--run", "1", "--format", "json", *options) == 0
    [run] = json.loads(capsys.readouterr().out)["runs"]
    store = Store.open(db)
    try:
        cur = store.con.execute(
            "FROM suite_events(1, method := ?, sensitivity := ?)", [method, sensitivity]
        )
        cols = [d[0] for d in cur.description]
        macro = [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        store.con.close()
    assert macro
    got = [{k: v for k, v in e.items() if k != "text"} for e in run["events"]]
    assert got == [
        {k: _plain(v) for k, v in m.items() if k not in ("run_id", "day")} for m in macro
    ]


def test_odd_names_in_an_event_stay_inert(tmp_path, capsys):
    db = _event_db(tmp_path, names={"metrics/event1": "Zero\nfake line\x1b[31m\u202e"})
    assert _alerts(db, "--run", "1") == 0
    out = capsys.readouterr().out
    assert "- dropped to zero: Zero?fake line?[31m? (metrics/event1)" in out
    assert "\x1b" not in out and "\u202e" not in out


# ---- only the new alerts ----------------------------------------------------------------


def test_new_only_keeps_only_the_new_alerts_in_the_json(tmp_path, capsys):
    assert _alerts(_db(tmp_path), "--run", "1", "--format", "json", "--new-only") == 0
    [run] = json.loads(capsys.readouterr().out)["runs"]
    # The flag holds since an earlier day, so only the other three are new.
    assert [a["alert"] for a in run["alerts"]] == ["large spike", "large drop", "dropped to zero"]
    assert all(a["is_new"] for a in run["alerts"])


def test_new_only_keeps_only_the_new_alerts_in_the_csv(tmp_path, capsys):
    assert _alerts(_db(tmp_path), "--run", "1", "--format", "csv", "--new-only") == 0
    rows = list(csv.reader(io.StringIO(capsys.readouterr().out)))
    assert len(rows) == 1 + 3
    assert {r[CSV_HEADER.index("is_new")] for r in rows[1:]} == {"true"}


def test_new_only_says_so_in_place_of_the_new_line(tmp_path, capsys):
    assert _alerts(_db(tmp_path), "--run", "1", "--new-only") == 0
    out = capsys.readouterr().out
    assert (
        "Alerts for 2026-11-06: 0 unusual values, 1 large drop, 1 large spike, 1 drop to zero\n"
        "Only the alerts that are new on 2026-11-06 are listed.\n"
    ) in out
    assert "of these alerts are new on" not in out
    assert "- unusual:" not in out


def test_new_only_keeps_an_event_line_without_new_alerts(tmp_path, capsys):
    # 12 components at 0 for 3 days: an event, and none of its alerts is new.
    db = _event_db(tmp_path, zeros=0, earlier=12)
    assert _alerts(db, "--run", "1", "--new-only") == 0
    lines = capsys.readouterr().out.splitlines()
    at = next(i for i, line in enumerate(lines) if line.startswith("Suite-wide event: "))
    assert lines[at + 1] == "None of its alerts are new on 2026-11-06."
    # The large drop is new and outside the event.
    assert lines[at + 2 :] == ["Other alerts:", OTHER_LINES[0]]


def test_new_only_lists_the_new_alerts_of_an_event(tmp_path, capsys):
    db = _event_db(tmp_path)
    assert _alerts(db, "--run", "1", "--new-only") == 0
    out = capsys.readouterr().out
    assert "Suite-wide event: " in out and "None of its alerts are new" not in out
    assert _zero_line(_by_id(12)[0]) in out


def test_one_more_alert_of_an_event_reads_well(tmp_path, capsys):
    # An event of 12 zeros, 6 of them new. With new_only, 5 are listed and 1 is left.
    db = _event_db(tmp_path, zeros=6, earlier=6)
    assert _alerts(db, "--run", "1", "--new-only") == 0
    lines = capsys.readouterr().out.splitlines()
    assert "1 more alert of this event is not listed. Add --every-alert to list it." in lines
    assert not any("more alerts of this event" in line for line in lines)


def test_new_only_keeps_every_event_in_the_json(tmp_path, capsys):
    db = _event_db(tmp_path, zeros=0, earlier=12)
    assert _alerts(db, "--run", "1", "--format", "json", "--new-only") == 0
    [run] = json.loads(capsys.readouterr().out)["runs"]
    assert [(e["event"], e["alerts"], e["new"]) for e in run["events"]] == [("zero", 12, 0)]
    assert [a["alert"] for a in run["alerts"]] == ["large drop"]


def test_new_only_without_new_alerts_exits_with_4(tmp_path, capsys):
    db = tmp_path / "ahd.duckdb"
    store = Store.open(db)
    rid = store.create_run("rs1", "One", None, TODAY, "UTC", "0.1")
    cid = "metrics/event1"
    store.add_components(
        rid, [Component(cid, "Old", "metric", "custom_events", 1, Column(cid, cid), True)]
    )
    store.add_daily_values(
        rid,
        [
            DailyValue(
                cid,
                TODAY - timedelta(days=91 - n),
                0.0 if n >= 88 else 100.0,
                None,
                None,
                None,
                None,
            )
            for n in range(91)
        ],
    )
    store.finish_run(rid, "ok")
    store.con.close()
    assert _alerts(db, "--run", "1") == 0
    capsys.readouterr()
    assert _alerts(db, "--run", "1", "--new-only") == 4
    assert "Alerts for 2026-11-06: 0 unusual values" in capsys.readouterr().out


@pytest.mark.parametrize("options", [["--run", "1"], ["--rsid", "rs1"], ["--all-suites"]])
def test_new_only_goes_with_every_way_to_choose_runs(tmp_path, capsys, options):
    assert _alerts(_db(tmp_path), *options, "--format", "json", "--new-only") == 0
    [run] = json.loads(capsys.readouterr().out)["runs"]
    assert len(run["alerts"]) == 3


def test_a_run_chosen_by_its_number_is_not_called_the_latest(tmp_path, capsys):
    """--run can name an older run, so its warning names the run, not the latest run."""
    _alerts(_db(tmp_path), "--run", "1", "--date", "2026-11-09")
    assert (
        "Warning: Run 1 of rs1 has the alerts of 2026-11-06, but yesterday was 2026-11-08 in "
        "the time zone of the suite.\n"
    ) in capsys.readouterr().out
