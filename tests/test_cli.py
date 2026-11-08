import json

import pytest
from fakes import (
    ApiError,
    fake_for,
    is_anomaly_trend,
    is_trend,
    restricted_dimensions,
    restricted_metrics,
)

from ahd.cli import main

CONFIG = {
    "org_id": "o@AdobeOrg",
    "client_id": "cid-12345",
    "secret": "sec-67890",
    "scopes": "openid",
}


def events(n):
    # Occurrences is the default metric for the Individual Component Summary.
    return [{"id": f"metrics/event{i}", "name": f"Event {i}"} for i in range(1, n + 1)] + [
        {"id": "metrics/occurrences", "name": "Occurrences", "type": "int"}
    ]


def custom_dims():
    return [
        {"id": "variables/prop1", "name": "Prop 1", "allowedForReporting": True},
        {"id": "variables/page", "name": "Page", "allowedForReporting": True},
    ]


@pytest.fixture
def cfg(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(CONFIG))
    return p


def args(tmp_path, cfg, *rest):
    return [rest[0], "--config", str(cfg), "--db", str(tmp_path / "t.duckdb"), *rest[1:]]


def test_run_writes_html_and_exit_code(tmp_path, cfg, capsys):
    fake = fake_for("rs1", events(3), custom_dims())
    out = tmp_path / "out"
    code = main(
        args(tmp_path, cfg, "run", "--rsid", "rs1", "--date", "2026-11-07", "--out", str(out)),
        client_factory=lambda creds, company_id=None: fake,
    )
    assert code == 0
    files = list(out.glob("*.html"))
    assert len(files) == 1
    assert str(files[0]) in capsys.readouterr().out


def test_run_failed_exit_code(tmp_path, cfg):
    fake = fake_for(
        "rs1", events(3), custom_dims(), posts=[(is_trend, ApiError(403, "no", "invalid_access"))]
    )
    code = main(
        args(
            tmp_path,
            cfg,
            "run",
            "--rsid",
            "rs1",
            "--date",
            "2026-11-07",
            "--out",
            str(tmp_path / "o"),
        ),
        client_factory=lambda creds, company_id=None: fake,
    )
    assert code == 1  # every request failed: no values


def test_run_partial_exit_code(tmp_path, cfg):
    fake = fake_for(
        "rs1",
        events(3),
        custom_dims(),
        posts=[(is_anomaly_trend, ApiError(403, "no", "invalid_access"))],
    )
    out = tmp_path / "o"
    code = main(
        args(tmp_path, cfg, "run", "--rsid", "rs1", "--date", "2026-11-07", "--out", str(out)),
        client_factory=lambda creds, company_id=None: fake,
    )
    assert code == 3
    assert len(list(out.glob("*.html"))) == 1


def test_render_needs_no_client(tmp_path, cfg, capsys):
    fake = fake_for("rs1", events(3), custom_dims())
    main(
        args(
            tmp_path,
            cfg,
            "run",
            "--rsid",
            "rs1",
            "--date",
            "2026-11-07",
            "--out",
            str(tmp_path / "o1"),
        ),
        client_factory=lambda creds, company_id=None: fake,
    )
    capsys.readouterr()

    def boom(*a, **k):
        raise AssertionError("client must not be created")

    out = tmp_path / "o2"
    code = main(
        ["render", "--run", "1", "--db", str(tmp_path / "t.duckdb"), "--out", str(out)],
        client_factory=boom,
    )
    assert code == 0
    assert len(list(out.glob("*.html"))) == 1


def test_runs_lists_runs(tmp_path, cfg, capsys):
    fake = fake_for("rs1", events(3), custom_dims())
    main(
        args(
            tmp_path,
            cfg,
            "run",
            "--rsid",
            "rs1",
            "--date",
            "2026-11-07",
            "--out",
            str(tmp_path / "o"),
        ),
        client_factory=lambda creds, company_id=None: fake,
    )
    capsys.readouterr()
    assert main(["runs", "--db", str(tmp_path / "t.duckdb")]) == 0
    out = capsys.readouterr().out
    assert "rs1" in out and "ok" in out


def test_check_without_rsid(tmp_path, cfg, capsys):
    fake = fake_for("rs1", [], [])
    fake.gets["/reportsuites/collections/suites"] = {"content": [{"rsid": "a"}, {"rsid": "b"}]}
    code = main(["check", "--config", str(cfg)], client_factory=lambda c, company_id=None: fake)
    out = capsys.readouterr().out
    assert code == 0 and "testco" in out and "2" in out


def test_check_reports_restricted_profile(tmp_path, cfg, capsys):
    fake = fake_for("rs1", restricted_metrics(), restricted_dimensions())
    code = main(
        ["check", "--config", str(cfg), "--rsid", "rs1"],
        client_factory=lambda c, company_id=None: fake,
    )
    assert code == 2
    assert "product profile" in capsys.readouterr().out


def test_check_ok(tmp_path, cfg, capsys):
    fake = fake_for("rs1", events(3), custom_dims())
    code = main(
        ["check", "--config", str(cfg), "--rsid", "rs1"],
        client_factory=lambda c, company_id=None: fake,
    )
    out = capsys.readouterr().out
    assert code == 0 and "3" in out


def test_check_anomaly_denied(tmp_path, cfg, capsys):
    fake = fake_for(
        "rs1", events(3), custom_dims(), posts=[(is_trend, ApiError(403, "no", "invalid_access"))]
    )
    code = main(
        ["check", "--config", str(cfg), "--rsid", "rs1"],
        client_factory=lambda c, company_id=None: fake,
    )
    assert code == 2
    assert "Anomaly Detection" in capsys.readouterr().out


def test_missing_config_is_one_line(tmp_path, capsys, monkeypatch):
    for k in ("ORG_ID", "CLIENT_ID", "SECRET", "SCOPES"):
        monkeypatch.delenv(f"AHD_{k}", raising=False)
    monkeypatch.chdir(tmp_path)
    code = main(["check", "--config", str(tmp_path / "nope.json")])
    err = capsys.readouterr().err
    assert code == 1
    assert err.count("\n") == 1 and "Traceback" not in err


def test_ctrl_c_is_one_line(tmp_path, cfg, capsys):
    def stop(creds, company_id=None):
        raise KeyboardInterrupt

    code = main(args(tmp_path, cfg, "run", "--rsid", "rs1"), client_factory=stop)
    err = capsys.readouterr().err
    assert code == 130
    assert err == "Stopped by Ctrl-C. A run that had started is marked failed.\n"


def test_api_error_is_one_line(tmp_path, cfg, capsys):
    def factory(creds, company_id=None):
        raise ApiError(401, "bad sign in", "unauthorized")

    code = main(["check", "--config", str(cfg)], client_factory=factory)
    err = capsys.readouterr().err
    assert code == 1 and err.count("\n") == 1 and "sec-67890" not in err


@pytest.mark.parametrize("failure", ["api500", "timeout"])
def test_check_probe_failure_does_not_crash(tmp_path, cfg, capsys, failure):
    import requests

    def raise_timeout(body):
        raise requests.Timeout("slow")

    responder = ApiError(500, "down", None) if failure == "api500" else raise_timeout
    fake = fake_for("rs1", events(3), custom_dims(), posts=[(is_anomaly_trend, responder)])
    code = main(
        ["check", "--config", str(cfg), "--rsid", "rs1"],
        client_factory=lambda c, company_id=None: fake,
    )
    out = capsys.readouterr().out
    name = "ApiError" if failure == "api500" else "Timeout"
    assert f"Anomaly detection: could not be tested ({name})" in out
    assert "All checks passed." not in out
    assert code == 2


def test_newer_database_is_one_line_error(tmp_path, cfg, capsys):
    import duckdb

    from ahd.store import Store

    db = tmp_path / "t.duckdb"
    Store.open(db).con.close()
    con = duckdb.connect(str(db))
    con.execute("update schema_version set version = 99")
    con.close()
    code = main(["runs", "--db", str(db)])
    err = capsys.readouterr().err
    assert (
        code == 1 and err.count("\n") == 1 and "different version" in err and "Traceback" not in err
    )


def test_locked_database_is_one_line_error(tmp_path, cfg, capsys):
    import subprocess
    import sys

    from ahd.store import Store

    db = tmp_path / "t.duckdb"
    Store.open(db).con.close()
    # Another process holds the file open for writing, as ahd run does.
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
        called = []

        def sign_in(creds, company_id=None):
            called.append(1)

        for argv in (["runs", "--db", str(db)], args(tmp_path, cfg, "run", "--rsid", "rs1")):
            code = main(argv, client_factory=sign_in)
            err = capsys.readouterr().err
            assert code == 1 and err.count("\n") == 1 and "Traceback" not in err
            assert "is in use by another program" in err
            assert not called  # ahd run stops before it signs in
    finally:
        holder.kill()
        holder.wait()


def test_file_that_is_not_a_database_is_one_line_error(tmp_path, capsys):
    db = tmp_path / "notes.duckdb"
    db.write_text("not a database")
    assert main(["runs", "--db", str(db)]) == 1
    assert "is not a DuckDB database" in capsys.readouterr().err


def test_runs_and_render_do_not_create_a_database(tmp_path, capsys):
    db = tmp_path / "new" / "t.duckdb"
    assert main(["runs", "--db", str(db)]) == 1
    assert main(["render", "--run", "1", "--db", str(db)]) == 1
    assert "There is no database at" in capsys.readouterr().err
    assert not db.parent.exists()


def test_runs_reads_a_database_that_a_reader_has_open(tmp_path, cfg, capsys):
    import duckdb

    from ahd.store import Store

    db = tmp_path / "t.duckdb"
    Store.open(db).con.close()
    reader = duckdb.connect(str(db), read_only=True)
    try:
        assert main(["runs", "--db", str(db)]) == 0
    finally:
        reader.close()


def test_empty_duckdb_file_is_not_an_ahd_database(tmp_path, capsys):
    import duckdb

    db = tmp_path / "other.duckdb"
    duckdb.connect(str(db)).close()
    assert main(["runs", "--db", str(db)]) == 1
    assert "holds no ahd runs" in capsys.readouterr().err


# ---- profiles ----------------------------------------------------------------


def _profile(name="prod"):
    from ahd.config import profile_dir

    folder = profile_dir(name)
    folder.mkdir(parents=True)
    (folder / "config.json").write_text(json.dumps(CONFIG))
    return folder


def _run_args(*rest):
    return ["run", "--rsid", "rs1", "--date", "2026-11-07", *rest]


def test_run_with_profile_uses_profile_db_and_says_so(tmp_path, capsys):
    folder = _profile()
    seen = []

    def factory(creds, company_id=None):
        seen.append(creds)
        return fake_for("rs1", events(3), custom_dims())

    code = main(
        _run_args("--profile", "prod", "--out", str(tmp_path / "o")), client_factory=factory
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "Signed in using profile prod" in out
    assert "sec-67890" not in out and "cid-12345" not in out
    assert seen[0].client_id == "cid-12345"
    assert (folder / "ahd.duckdb").exists()
    code = main(["--profile", "prod", "runs"])
    assert code == 0 and "rs1" in capsys.readouterr().out


def test_aa_profile_variable_picks_profile(tmp_path, capsys, monkeypatch):
    folder = _profile("stage")
    monkeypatch.setenv("AA_PROFILE", "stage")
    fake = fake_for("rs1", events(3), custom_dims())
    code = main(
        _run_args("--out", str(tmp_path / "o")), client_factory=lambda c, company_id=None: fake
    )
    assert code == 0 and "profile stage" in capsys.readouterr().out
    assert (folder / "ahd.duckdb").exists()


def test_db_flag_wins_over_profile(tmp_path, capsys):
    folder = _profile()
    fake = fake_for("rs1", events(3), custom_dims())
    db = tmp_path / "mine.duckdb"
    code = main(
        _run_args("--profile", "prod", "--db", str(db), "--out", str(tmp_path / "o")),
        client_factory=lambda c, company_id=None: fake,
    )
    assert code == 0 and db.exists() and not (folder / "ahd.duckdb").exists()


def test_without_profile_default_db_is_data_folder(tmp_path, cfg, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fake = fake_for("rs1", events(3), custom_dims())
    code = main(
        _run_args("--config", str(cfg), "--out", str(tmp_path / "o")),
        client_factory=lambda c, company_id=None: fake,
    )
    assert code == 0 and (tmp_path / "data" / "ahd.duckdb").exists()


def test_missing_profile_folder_is_not_created(tmp_path, capsys):
    from ahd.config import profiles_base

    code = main(["runs", "--profile", "typo"])
    err = capsys.readouterr().err
    assert code == 1 and "Profile 'typo' was not found" in err
    assert not (profiles_base() / "typo").exists()


def test_check_prints_source_and_explains_no_suites(tmp_path, cfg, capsys):
    from fakes import FakeClient

    fake = FakeClient(gets={"/reportsuites/collections/suites": {"content": []}})
    code = main(["check", "--config", str(cfg)], client_factory=lambda c, company_id=None: fake)
    out = capsys.readouterr().out
    assert code == 2
    assert "Signed in using config.json" in out
    assert "sees no report suites" in out and "Admin Console" in out


def test_sign_in_error_is_one_line(tmp_path, cfg, capsys):
    from ahd.api.auth import WRONG_SECRET, SignInError

    def factory(creds, company_id=None):
        raise SignInError(WRONG_SECRET)

    code = main(["check", "--config", str(cfg)], client_factory=factory)
    err = capsys.readouterr().err
    assert code == 1 and err == f"Error: {WRONG_SECRET}\n" and "sec-67890" not in err


def test_run_warns_when_components_drop(tmp_path, cfg, capsys):
    db_args = ["--config", str(cfg), "--db", str(tmp_path / "t.duckdb")]
    for n in (10, 5):
        fake = fake_for("rs1", events(n), custom_dims())
        main(
            _run_args(*db_args, "--out", str(tmp_path / "o")),
            client_factory=lambda c, company_id=None, fake=fake: fake,
        )
    out = capsys.readouterr().out
    assert out.count("Warning: this run sees fewer components") == 1
    assert "This run sees 5 custom events. Run 1 for the same report suite saw 10." in out
    assert "product profile" in out


# ---- prune -------------------------------------------------------------------


@pytest.fixture
def three_runs(tmp_path, cfg):
    from ahd.store import Store

    db = tmp_path / "t.duckdb"
    for _ in range(3):
        fake = fake_for("rs1", events(2), custom_dims())
        main(
            _run_args("--config", str(cfg), "--db", str(db), "--out", str(tmp_path / "o")),
            client_factory=lambda c, company_id=None, fake=fake: fake,
        )
    s = Store.open(db)
    s.con.execute("UPDATE runs SET today = DATE '2026-09-30' WHERE run_id = 1")
    s.con.execute("UPDATE runs SET status = 'running' WHERE run_id = 2")
    s.con.close()
    return db


def _stored(db):
    from ahd.store import Store

    s = Store.open(db)
    try:
        return [r["run_id"] for r in s.list_runs()]
    finally:
        s.con.close()


def _answer(monkeypatch, reply):
    def fake_input(question):
        if isinstance(reply, BaseException):
            raise reply
        return reply

    monkeypatch.setattr("builtins.input", fake_input)


def test_prune_needs_a_selection(three_runs, capsys):
    assert main(["prune", "--db", str(three_runs)]) == 1
    assert "--run N or --before" in capsys.readouterr().err


def test_prune_asks_then_deletes(three_runs, capsys, monkeypatch):
    _answer(monkeypatch, "y")
    code = main(["prune", "--db", str(three_runs), "--run", "3", "--before", "2026-10-01"])
    out = capsys.readouterr().out
    assert code == 0
    assert "These 2 runs will be deleted:" in out and "Deleted 2 runs." in out
    assert _stored(three_runs) == [2]


@pytest.mark.parametrize("reply", ["n", "", EOFError()])
def test_prune_keeps_runs_unless_confirmed(three_runs, capsys, monkeypatch, reply):
    _answer(monkeypatch, reply)
    assert main(["prune", "--db", str(three_runs), "--run", "1"]) == 1
    out = capsys.readouterr().out
    assert "This run will be deleted:" in out and "No runs were deleted." in out
    assert _stored(three_runs) == [3, 2, 1]


def test_prune_yes_skips_question(three_runs, capsys, monkeypatch):
    _answer(monkeypatch, AssertionError("must not ask"))
    assert main(["prune", "--db", str(three_runs), "--run", "1", "--yes"]) == 0
    assert "Deleted 1 run." in capsys.readouterr().out
    assert _stored(three_runs) == [3, 2]


def test_prune_keeps_running_runs_without_force(three_runs, capsys):
    code = main(["prune", "--db", str(three_runs), "--run", "2", "--run", "7", "--yes"])
    out = capsys.readouterr().out
    assert code == 0
    assert "There is no stored run 7." in out
    assert 'Run 2 is still marked "running", so it is kept. Add --force' in out
    assert "No runs were deleted." in out
    assert _stored(three_runs) == [3, 2, 1]
    assert main(["prune", "--db", str(three_runs), "--run", "2", "--yes", "--force"]) == 0
    assert _stored(three_runs) == [3, 1]


def test_change_alert_option(tmp_path, cfg):
    fake = fake_for("rs1", events(3), custom_dims())
    out = tmp_path / "o"
    code = main(
        args(
            tmp_path,
            cfg,
            "run",
            "--rsid",
            "rs1",
            "--date",
            "2026-11-07",
            "--out",
            str(out),
            "--change-alert",
            "45",
        ),
        client_factory=lambda creds, company_id=None: fake,
    )
    assert code == 0
    assert 'value="45"' in next(out.glob("*.html")).read_text()
    out2 = tmp_path / "o2"
    main(["render", "--run", "1", "--db", str(tmp_path / "t.duckdb"), "--out", str(out2)])
    assert 'value="30"' in next(out2.glob("*.html")).read_text()


@pytest.mark.parametrize("bad", ["5", "33", "105"])
def test_change_alert_must_be_a_slider_step(tmp_path, bad):
    with pytest.raises(SystemExit):
        main(["render", "--run", "1", "--db", str(tmp_path / "t.duckdb"), "--change-alert", bad])


# ---- suites, segments, names, company, progress --------------------------------


def test_suites_and_segments_commands(cfg, capsys):
    fake = fake_for("rs1", events(1), custom_dims())
    assert main(["suites", "--config", str(cfg)], client_factory=lambda c: fake) == 0
    out = capsys.readouterr().out
    assert "rs1  Test Suite" in out
    code = main(
        ["segments", "--config", str(cfg), "--rsid", "test suite"], client_factory=lambda c: fake
    )
    out = capsys.readouterr().out
    assert code == 0 and "s300_abc  Mobile visits" in out


def test_suites_none_visible(cfg, capsys):
    from fakes import FakeClient

    fake = FakeClient(gets={"/reportsuites/collections/suites": {"content": []}})
    assert main(["suites", "--config", str(cfg)], client_factory=lambda c: fake) == 2
    assert "sees no report suites" in capsys.readouterr().out


def test_segments_none(cfg, capsys):
    fake = fake_for("rs1", events(1), custom_dims())
    fake.gets["/segments"] = {"content": []}
    assert (
        main(["segments", "--config", str(cfg), "--rsid", "rs1"], client_factory=lambda c: fake)
        == 0
    )
    assert "No segments are available" in capsys.readouterr().out


def test_run_by_names_and_company(tmp_path, cfg, capsys):
    from ahd.store import Store

    fake = fake_for("rs1", events(3), custom_dims())
    seen = {}

    def factory(creds, company_id=None):
        seen["company"] = company_id
        return fake

    code = main(
        args(
            tmp_path,
            cfg,
            "run",
            "--rsid",
            "Test Suite",
            "--segment",
            "mobile visits",
            "--date",
            "2026-11-07",
            "--out",
            str(tmp_path / "o"),
            "--company",
            "Luma",
        ),
        client_factory=factory,
    )
    assert code == 0 and seen["company"] == "Luma"
    run = Store.open(tmp_path / "t.duckdb").get_run(1)
    assert run["rsid"] == "rs1" and run["segment_id"] == "s300_abc"
    for body in fake.posted():
        assert {"type": "segment", "segmentId": "s300_abc"} in body["globalFilters"]
    err = capsys.readouterr().err
    assert "Found " in err and "Daily trends: 1 of 1 requests done." in err
    assert "Individual Component Summary: 5 of 5 requests done." in err


def test_check_by_suite_name(cfg, capsys):
    fake = fake_for("rs1", events(3), custom_dims())
    code = main(
        ["check", "--config", str(cfg), "--rsid", "TEST SUITE"],
        client_factory=lambda c, company_id=None: fake,
    )
    assert code == 0 and "Report suite: Test Suite" in capsys.readouterr().out


def test_runs_shows_duration(tmp_path, cfg, capsys):
    fake = fake_for("rs1", events(3), custom_dims())
    main(
        args(
            tmp_path,
            cfg,
            "run",
            "--rsid",
            "rs1",
            "--date",
            "2026-11-07",
            "--out",
            str(tmp_path / "o"),
        ),
        client_factory=lambda c, company_id=None: fake,
    )
    capsys.readouterr()
    main(["runs", "--db", str(tmp_path / "t.duckdb")])
    out = capsys.readouterr().out
    assert "took" in out.splitlines()[0] and out.splitlines()[1].rstrip().endswith(" s")


def test_progress_on_a_terminal_rewrites_one_line():
    import io

    from ahd.cli import _Progress

    class Tty(io.StringIO):
        def isatty(self):
            return True

    t = Tty()
    p = _Progress(t)
    p("Long first message")
    p("Short")
    p.done()
    assert t.getvalue() == (
        "\rLong first message"
        + "\rShort".ljust(len("Long first message") + 1)
        + "\r"
        + " " * 5
        + "\r"
    )
    plain = io.StringIO()
    q = _Progress(plain)
    q("one")
    q.done()
    assert plain.getvalue() == "one\n"


# ---- individual pairs ---------------------------------------------------------


def test_individual_adds_a_pair_and_rewrites_the_report(tmp_path, cfg, capsys):
    from ahd.store import Store

    fake = fake_for("rs1", events(2), custom_dims())
    fake.gets["/dimensions"] = custom_dims() + [
        {"id": "variables/evar17", "name": "Internal Search Term"}
    ]
    fake.gets["/metrics"] = events(2) + [
        {"id": "metrics/cartadditions", "name": "Cart Additions", "type": "int"}
    ]
    out = tmp_path / "o"
    main(
        args(tmp_path, cfg, "run", "--rsid", "rs1", "--date", "2026-11-07", "--out", str(out)),
        client_factory=lambda c, company_id=None: fake,
    )
    capsys.readouterr()
    code = main(
        [
            "individual",
            "--config",
            str(cfg),
            "--db",
            str(tmp_path / "t.duckdb"),
            "--run",
            "1",
            "--dimension",
            "internal search term",
            "--metric",
            "cartadditions",
            "--out",
            str(out),
        ],
        client_factory=lambda c, company_id=None: fake,
    )
    stdout = capsys.readouterr().out
    assert code == 0
    assert "Added Internal Search Term by Cart Additions to run 1." in stdout
    html = next(out.glob("*.html")).read_text()
    assert "Page by Occurrences, and 1 more pair" in html
    pairs = Store.open(tmp_path / "t.duckdb").list_pairs(1)
    assert [(p["dimension"], p["metric"]) for p in pairs] == [
        ("variables/page", "metrics/occurrences"),
        ("variables/evar17", "metrics/cartadditions"),
    ]


def test_individual_with_problems_exits_3(tmp_path, cfg, capsys):
    fake = fake_for("rs1", events(2), custom_dims())
    main(
        args(
            tmp_path,
            cfg,
            "run",
            "--rsid",
            "rs1",
            "--date",
            "2026-11-07",
            "--out",
            str(tmp_path / "o"),
        ),
        client_factory=lambda c, company_id=None: fake,
    )
    fake.posts = [
        (
            lambda b: "variables/prop1" in str(b) and "metrics/event1" in str(b),
            ApiError(400, "bad", "invalid"),
        )
    ]
    code = main(
        [
            "individual",
            "--config",
            str(cfg),
            "--db",
            str(tmp_path / "t.duckdb"),
            "--run",
            "1",
            "--dimension",
            "prop1",
            "--metric",
            "event1",
            "--out",
            str(tmp_path / "o"),
        ],
        client_factory=lambda c, company_id=None: fake,
    )
    assert code == 3 and "5 of its requests had problems" in capsys.readouterr().out


def test_individual_unknown_run(tmp_path, cfg, capsys):
    def boom(*a, **k):
        raise AssertionError("must not sign in")

    code = main(
        [
            "individual",
            "--config",
            str(cfg),
            "--db",
            str(tmp_path / "t.duckdb"),
            "--run",
            "9",
            "--dimension",
            "page",
            "--metric",
            "occurrences",
        ],
        client_factory=boom,
    )
    assert code == 1 and "There is no stored run 9." in capsys.readouterr().err
