"""scripts/live_check.py, with the fake client. These tests never use the network."""

import importlib.util
import json
import re
from pathlib import Path

import duckdb
import pytest
from fakes import adobe_dimensions, fake_for, is_trend

from ahd.cli import main

ROOT = Path(__file__).resolve().parents[1]
SECRET = "sec-67890"
CONFIG = {"org_id": "o@AdobeOrg", "client_id": "cid-12345", "secret": SECRET, "scopes": "openid"}
METRICS = [
    {"id": "metrics/event1", "name": "Event 1", "allowedForReporting": True},
    {
        "id": "metrics/occurrences",
        "name": "Occurrences",
        "type": "int",
        "allowedForReporting": True,
    },
]
DIMENSIONS = [
    {"id": "variables/prop1", "name": "Prop 1", "allowedForReporting": True},
    {"id": "variables/page", "name": "Page", "allowedForReporting": True},
]


@pytest.fixture
def live_check():
    spec = importlib.util.spec_from_file_location("live_check", ROOT / "scripts/live_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def cfg(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(CONFIG), encoding="utf-8")
    return p


def fake():
    client = fake_for("rs1", METRICS, [])
    client.gets["/dimensions"] = adobe_dimensions(DIMENSIONS)
    return client


def test_the_script_does_not_run_in_ci(live_check, monkeypatch, capsys):
    monkeypatch.setenv("CI", "true")
    monkeypatch.setattr(live_check, "run", lambda argv: pytest.fail("the script ran in CI"))
    assert live_check.cli(["--rsid", "rs1"]) == 2
    assert "does not run in CI" in capsys.readouterr().err


def test_the_script_passes_its_options_to_ahd_run(live_check, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    passed = []
    monkeypatch.setattr(live_check, "run", lambda argv: passed.append(argv) or 0)
    assert live_check.cli(["--rsid", "rs1", "--profile", "prod", "--date", "2026-11-24"]) == 0
    assert passed == [["--rsid", "rs1", "--profile", "prod", "--date", "2026-11-24"]]


def test_the_date_help_says_that_the_date_is_today(live_check, monkeypatch, capsys):
    # ahd run --date treats the date as today and checks the days before it.
    monkeypatch.delenv("CI", raising=False)
    with pytest.raises(SystemExit):
        live_check.cli(["--help"])
    words = " ".join(capsys.readouterr().out.split())
    assert " ".join(live_check.DATE_HELP.split()) in words
    assert "Use this date as today, as ahd run --date does" in live_check.DATE_HELP
    assert "The day to check" not in words


def test_a_run_prints_its_requests_in_order_and_finds_no_problem(live_check, cfg, capsys):
    client = fake()
    argv = ["--rsid", "rs1", "--config", str(cfg), "--date", "2026-11-07"]
    assert live_check.run(argv, connect=lambda creds, company_id=None: client) == 0
    out = capsys.readouterr().out
    # The lookup of Page asks without an expansion before the catalog asks for the field.
    plain = out.index("/dimensions  expansion=(none)")
    catalog = out.index("/dimensions  expansion=allowedForReporting,attributionModel")
    assert plain < catalog
    assert "allowedForReporting in the answer: False" in out[plain:catalog]
    assert "allowedForReporting in the answer: True" in out[catalog:]
    assert out.rstrip().endswith(
        "Checked 4 components in 4 groups.\n"
        "The script has deleted its temporary folder, with the database and the report.\n"
        "No problem found."
    )
    assert SECRET not in out


def test_the_counts_are_singular_for_one(live_check, tmp_path, cfg):
    db = tmp_path / "t.duckdb"
    argv = ["run", "--config", str(cfg), "--db", str(db), "--rsid", "rs1", "--date", "2026-11-07"]
    main([*argv, "--out", str(tmp_path / "out")], client_factory=lambda *a, **k: fake())
    with duckdb.connect(str(db)) as con:
        con.execute("DELETE FROM components WHERE component_id <> 'variables/prop1'")
    assert live_check.checked(db) == "Checked 1 component in 1 group."


def test_the_request_of_0_1_19_is_found(live_check, cfg, capsys):
    # 0.1.19 asked /dimensions for allowedForReporting alone. After the plain lookup of Page,
    # Adobe then left the field out, and props and Page stored NULL.
    client = fake()
    answer = client.gets["/dimensions"]

    def old_request(params):
        params = dict(params or {})
        if params.get("expansion"):
            params["expansion"] = "allowedForReporting"
        return answer(params)

    client.gets["/dimensions"] = old_request
    argv = ["--rsid", "rs1", "--config", str(cfg), "--date", "2026-11-07"]
    assert live_check.run(argv, connect=lambda creds, company_id=None: client) == 1
    out = capsys.readouterr().out
    assert out.rstrip().endswith(
        "allowed_for_reporting is NULL for 1 of 1 in Reserved Props.\n"
        "allowed_for_reporting is NULL for 1 of 1 in custom_props.\n"
        "2 problems found."
    )


def test_one_problem_is_counted_as_one(live_check, cfg, capsys, monkeypatch):
    monkeypatch.setattr(live_check, "check_db", lambda path: ["The run is failed."])
    client = fake()
    argv = ["--rsid", "rs1", "--config", str(cfg), "--date", "2026-11-07"]
    assert live_check.run(argv, connect=lambda creds, company_id=None: client) == 1
    assert capsys.readouterr().out.rstrip().endswith("The run is failed.\n1 problem found.")


def test_a_null_column_is_named_with_its_group(live_check, tmp_path, cfg):
    db = tmp_path / "t.duckdb"
    argv = ["run", "--config", str(cfg), "--db", str(db), "--rsid", "rs1", "--date", "2026-11-07"]
    assert main([*argv, "--out", str(tmp_path / "out")], client_factory=lambda *a, **k: fake()) == 0
    assert live_check.check_db(db) == []
    with duckdb.connect(str(db)) as con:
        con.execute(
            "UPDATE components SET allowed_for_reporting = NULL"
            " WHERE component_id = 'variables/prop1'"
        )
    assert live_check.check_db(db) == ["allowed_for_reporting is NULL for 1 of 1 in custom_props."]


def test_a_failed_run_and_an_empty_file_are_problems(live_check, tmp_path, cfg):
    db = tmp_path / "t.duckdb"
    argv = ["run", "--config", str(cfg), "--db", str(db), "--rsid", "rs1", "--date", "2026-11-07"]
    main([*argv, "--out", str(tmp_path / "out")], client_factory=lambda *a, **k: fake())
    with duckdb.connect(str(db)) as con:
        con.execute("UPDATE runs SET status = 'failed'")
        con.execute("DELETE FROM daily_values")
    assert live_check.check_db(db) == ["The run is failed.", "The run stored no daily values."]
    with duckdb.connect(str(db)) as con:
        con.execute("DELETE FROM runs")
    assert live_check.check_db(db) == ["The file holds no run."]


def test_the_issues_of_a_partial_run_are_counted_and_are_no_problem(live_check, cfg, capsys):
    # Every report but the trends comes back empty, so the pair stores no_rows issues.
    client = fake()
    client.posts.insert(0, (lambda body: not is_trend(body), {"rows": [], "totalElements": 0}))
    argv = ["--rsid", "rs1", "--config", str(cfg), "--date", "2026-11-07"]
    assert live_check.run(argv, connect=lambda creds, company_id=None: client) == 0
    out = capsys.readouterr().out
    lines = out[out.index("Checked 4 components in 4 groups.\n") :].splitlines()
    issues = [line for line in lines if line.startswith("Issues: ")]
    # The issue lines come right after the counts.
    assert issues and lines[1 : 1 + len(issues)] == issues
    assert all(re.fullmatch(r"Issues: \d+ \w+ \w+\.", line) for line in issues)
    assert any(line.endswith(" individual no_rows.") for line in issues)
    assert out.rstrip().endswith("No problem found.")


def test_the_issues_are_counted_by_scope_and_code_in_order(live_check, tmp_path, cfg):
    db = tmp_path / "t.duckdb"
    argv = ["run", "--config", str(cfg), "--db", str(db), "--rsid", "rs1", "--date", "2026-11-07"]
    assert main([*argv, "--out", str(tmp_path / "out")], client_factory=lambda *a, **k: fake()) == 0
    assert live_check.issue_lines(db) == []
    rows = [
        ("individual", "no_rows"),
        ("column", "http_500"),
        ("individual", "no_rows"),
        ("column", "http_500"),
        ("individual", "no_rows"),
        ("column", "bad\x1b[31mcode"),
    ]
    with duckdb.connect(str(db)) as con:
        rid = con.execute("SELECT max(run_id) FROM runs").fetchone()[0]
        for scope, code in rows:
            con.execute(
                "INSERT INTO issues VALUES (?, ?, 'metrics/event1', ?, 'x')", [rid, scope, code]
            )
        # An issue of an earlier run is not counted.
        con.execute("INSERT INTO issues VALUES (?, 'column', 'x', 'old', 'x')", [rid - 1])
    assert live_check.issue_lines(db) == [
        "Issues: 1 column bad?[31mcode.",
        "Issues: 2 column http_500.",
        "Issues: 3 individual no_rows.",
    ]
