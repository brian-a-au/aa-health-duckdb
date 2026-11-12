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
from ahd.store import Store

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
    p.write_text(json.dumps(CONFIG), encoding="utf-8")
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


def test_a_report_that_cannot_be_written_is_a_plain_error(tmp_path, cfg, capsys):
    # --out names a file, so the folder for the report cannot be made.
    taken = tmp_path / "taken"
    taken.write_text("not a folder", encoding="utf-8")
    fake = fake_for("rs1", events(3), custom_dims())
    code = main(
        args(tmp_path, cfg, "run", "--rsid", "rs1", "--date", "2026-11-07", "--out", str(taken)),
        client_factory=lambda creds, company_id=None: fake,
    )
    assert code == 1
    err = capsys.readouterr().err
    assert f"\nError: The report could not be written to {taken} (" in err
    assert "Run 1 is stored. Then run ahd render --run 1 to write the report." in err
    assert "Traceback" not in err
    store = Store.open(tmp_path / "t.duckdb", read_only=True)
    assert [r["status"] for r in store.list_runs()] == ["ok"]
    store.con.close()
    code = main(["render", "--run", "1", "--db", str(tmp_path / "t.duckdb"), "--out", str(taken)])
    assert code == 1
    assert "Error: The report could not be written to" in capsys.readouterr().err


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


def test_check_counts_the_suites_of_every_page(cfg, capsys):
    """A company with more suites than one page holds is counted in full."""
    pages = [[{"rsid": "a"}, {"rsid": "b"}], [{"rsid": "c"}]]
    fake = fake_for("rs1", [], [])
    fake.gets["/reportsuites/collections/suites"] = lambda params: {
        "content": pages[params["page"]],
        "totalPages": len(pages),
        "lastPage": params["page"] == len(pages) - 1,
    }
    code = main(["check", "--config", str(cfg)], client_factory=lambda c, company_id=None: fake)
    assert code == 0 and "Report suites visible: 3" in capsys.readouterr().out


def test_run_names_a_database_folder_that_cannot_be_made(tmp_path, cfg, capsys):
    """A folder path that runs through a file is a plain error, not an error trace."""
    (tmp_path / "file").write_text("x")
    code = main(
        [
            "run",
            "--config",
            str(cfg),
            "--rsid",
            "rs1",
            "--db",
            str(tmp_path / "file" / "sub" / "a.duckdb"),
        ],
        client_factory=lambda c, company_id=None: pytest.fail("must not sign in"),
    )
    err = capsys.readouterr().err
    assert code == 1 and "could not be made" in err and "--db" in err and "Traceback" not in err


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
    assert err.startswith("Stopped by Ctrl-C. A run that ahd run had started is marked failed.")


@pytest.mark.parametrize("company", [None, "co2"])
def test_says_which_login_company_it_used(tmp_path, cfg, capsys, company):
    fake = fake_for("rs1", events(1), custom_dims(), company_id=company or "co1")
    fake.companies, fake.company_name = 2, "Name co1"
    argv = ["suites", "--config", str(cfg)] + (["--company", company] if company else [])
    assert main(argv, client_factory=lambda c, company_id=None: fake) == 0
    out = capsys.readouterr().out
    note = "This credential sees 2 login companies. Using Name co1 (co1). Choose another"
    assert (note in out) == (company is None)


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--batch-size", "0"),
        ("--batch-size", "-5"),
        ("--batch-size", "101"),
        ("--batch-size", "ten"),
        ("--workers", "0"),
        ("--workers", "17"),
    ],
)
def test_batch_size_and_workers_must_be_in_range(tmp_path, cfg, capsys, option, value):
    with pytest.raises(SystemExit) as e:
        main(args(tmp_path, cfg, "run", "--rsid", "rs1", option, value))
    assert e.value.code == 2
    assert "is not a whole number from 1 to" in capsys.readouterr().err


@pytest.mark.parametrize(
    "value",
    ["0001-01-01", "0001-03-31", "9999-12-31", "20261107", "2026-W45-1", "2026-11-7", "2026-02-30"],
)
def test_date_must_be_a_plain_date_that_has_its_trend_days(tmp_path, cfg, capsys, value):
    with pytest.raises(SystemExit) as e:
        main(args(tmp_path, cfg, "run", "--rsid", "rs1", "--date", value))
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert (
        f"{value!r} is not a date in the form YYYY-MM-DD" in err or "too early or too late" in err
    )
    assert "Traceback" not in err


def test_run_defaults_to_8_workers_and_batches_of_20():
    from ahd.cli import _parser
    from ahd.fetch import RunOptions

    parsed = _parser().parse_args(["run", "--rsid", "rs1"])
    assert (parsed.workers, parsed.batch_size) == (8, 20)
    defaults = RunOptions(rsid="rs1", segment_id=None, today=None)
    assert (defaults.workers, defaults.batch_size) == (8, 20)


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
    db.write_text("not a database", encoding="utf-8")
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


def test_prune_and_individual_do_not_create_a_database(tmp_path, cfg, capsys):
    db = tmp_path / "new" / "t.duckdb"
    assert main(["prune", "--run", "1", "--yes", "--db", str(db)]) == 1
    argv = args(tmp_path, cfg, "individual", "--run", "1", "--dimension", "page", "--metric", "x")
    argv[argv.index("--db") + 1] = str(db)
    assert main(argv, client_factory=lambda c, company_id=None: pytest.fail("signed in")) == 1
    assert "There is no database at" in capsys.readouterr().err
    assert not db.parent.exists()


@pytest.mark.parametrize(
    "message",
    [
        'Could not set lock on file "t.duckdb": Conflicting lock is held in python (PID 7)',
        "File is already open in \nC:\\Python\\python.exe (PID 7)",
        (
            'Cannot open file "t.duckdb": The process cannot access the file because it is being '
            "used by another process."
        ),
    ],
)
def test_each_systems_lock_message_is_called_locked(tmp_path, monkeypatch, message):
    import duckdb

    from ahd.store import Store, StoreError

    def locked(path, read_only=False):
        raise duckdb.IOException(message)

    monkeypatch.setattr(duckdb, "connect", locked)
    with pytest.raises(StoreError, match="is in use by another program"):
        Store.open(tmp_path / "t.duckdb")


def test_a_damaged_file_is_not_called_locked(tmp_path, monkeypatch):
    import duckdb

    from ahd.store import Store, StoreError

    def broken(path, read_only=False):
        raise duckdb.IOException("Corrupt database file: block 7 is invalid")

    monkeypatch.setattr(duckdb, "connect", broken)
    with pytest.raises(StoreError, match="Could not open the database"):
        Store.open(tmp_path / "t.duckdb")


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
    (folder / "config.json").write_text(json.dumps(CONFIG), encoding="utf-8")
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


@pytest.mark.parametrize("how", ["flag", "variable"])
def test_profile_and_config_together_is_an_error(tmp_path, cfg, capsys, monkeypatch, how):
    _profile("prod")
    argv = ["suites", "--config", str(cfg)]
    if how == "flag":
        argv += ["--profile", "prod"]
    else:
        monkeypatch.setenv("AA_PROFILE", "prod")
    code = main(argv, client_factory=lambda c, company_id=None: pytest.fail("signed in"))
    assert code == 1
    assert "Use a profile or --config, not both" in capsys.readouterr().err


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


@pytest.mark.parametrize("value", ["tomorrow", "20261001", "2026-W40-4", "2026-02-30"])
def test_prune_before_takes_only_a_date_as_yyyy_mm_dd(three_runs, capsys, value):
    with pytest.raises(SystemExit):
        main(["prune", "--db", str(three_runs), "--before", value, "--yes"])
    assert f"{value!r} is not a date in the form YYYY-MM-DD." in capsys.readouterr().err
    runs = Store.open(three_runs, read_only=True).list_runs()
    assert len(runs) == 3


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
    assert 'value="-45"' in next(out.glob("*.html")).read_text(encoding="utf-8")
    out2 = tmp_path / "o2"
    main(["render", "--run", "1", "--db", str(tmp_path / "t.duckdb"), "--out", str(out2)])
    assert 'value="-30"' in next(out2.glob("*.html")).read_text(encoding="utf-8")
    # The spike slider starts at +50%, or where --spike-alert says.
    assert 'id="spike-alert" min="10" max="200" step="10" value="50"' in (
        next(out2.glob("*.html")).read_text(encoding="utf-8")
    )
    out3 = tmp_path / "o3"
    main(
        ["render", "--run", "1", "--db", str(tmp_path / "t.duckdb"), "--out", str(out3)]
        + ["--spike-alert", "120"]
    )
    page = next(out3.glob("*.html")).read_text(encoding="utf-8")
    assert 'step="10" value="120"' in page and ">+120%</output>" in page


@pytest.mark.parametrize("bad", ["5", "33", "105"])
def test_change_alert_must_be_a_slider_step(tmp_path, bad):
    with pytest.raises(SystemExit):
        main(["render", "--run", "1", "--db", str(tmp_path / "t.duckdb"), "--change-alert", bad])


@pytest.mark.parametrize("bad", ["0", "55", "210"])
def test_spike_alert_must_be_a_slider_step(tmp_path, bad):
    with pytest.raises(SystemExit):
        main(["render", "--run", "1", "--db", str(tmp_path / "t.duckdb"), "--spike-alert", bad])


def test_anomaly_method_option_reaches_the_report_and_the_alert_line(
    tmp_path, cfg, capsys, monkeypatch
):
    from ahd.cli import _alert_line as real_line
    from ahd.cli import build_view as real_view

    seen = []

    def view(*a, **k):
        seen.append(("view", k.get("method", "adobe")))
        return real_view(*a, **k)

    def line(*a, **k):
        seen.append(("line", k.get("method", "adobe")))
        return real_line(*a, **k)

    monkeypatch.setattr("ahd.cli.build_view", view)
    monkeypatch.setattr("ahd.cli._alert_line", line)
    fake = fake_for("rs1", events(2), custom_dims())
    fake.gets["/dimensions"] = custom_dims() + [{"id": "variables/evar17", "name": "Term"}]
    out = tmp_path / "o"
    run = ["run", "--rsid", "rs1", "--date", "2026-11-07", "--out", str(out)]
    assert main(args(tmp_path, cfg, *run), client_factory=lambda c, company_id=None: fake) == 0
    assert seen == [("view", "adobe"), ("line", "adobe")]
    page = next(out.glob("*.html")).read_text(encoding="utf-8")
    assert "<dt>Anomaly detection</dt><dd>Adobe</dd>" in page

    seen.clear()
    main(
        args(tmp_path, cfg, *run, "--anomaly-method", "weekday-median"),
        client_factory=lambda c, company_id=None: fake,
    )
    assert seen == [("view", "weekday-median"), ("line", "weekday-median")]

    seen.clear()
    individual = ["individual", "--run", "1", "--dimension", "evar17", "--metric", "event1"]
    main(
        args(tmp_path, cfg, *individual, "--out", str(out), "--anomaly-method", "weekday-iqr"),
        client_factory=lambda c, company_id=None: fake,
    )
    assert seen == [("view", "weekday-iqr")]

    seen.clear()
    out2 = tmp_path / "o2"
    db = ["--db", str(tmp_path / "t.duckdb"), "--out", str(out2)]
    assert main(["render", "--run", "1", *db, "--anomaly-method", "weekday-median"]) == 0
    assert seen == [("view", "weekday-median")]
    page = next(out2.glob("*.html")).read_text(encoding="utf-8")
    assert "<dt>Anomaly detection</dt><dd>Weekday median <span" in page


def test_anomaly_sensitivity_option_reaches_the_report_and_the_alert_line(
    tmp_path, cfg, capsys, monkeypatch
):
    from ahd.cli import _alert_line as real_line
    from ahd.cli import build_view as real_view

    seen = []

    def view(*a, **k):
        seen.append(("view", k.get("method", "adobe"), k.get("sensitivity", "normal")))
        return real_view(*a, **k)

    def line(*a, **k):
        seen.append(("line", k.get("method", "adobe"), k.get("sensitivity", "normal")))
        return real_line(*a, **k)

    monkeypatch.setattr("ahd.cli.build_view", view)
    monkeypatch.setattr("ahd.cli._alert_line", line)
    fake = fake_for("rs1", events(2), custom_dims())
    fake.gets["/dimensions"] = custom_dims() + [{"id": "variables/evar17", "name": "Term"}]
    out = tmp_path / "o"
    run = ["run", "--rsid", "rs1", "--date", "2026-11-07", "--out", str(out)]
    local = ["--anomaly-method", "weekday-median"]
    assert (
        main(args(tmp_path, cfg, *run, *local), client_factory=lambda c, company_id=None: fake) == 0
    )
    assert seen == [("view", "weekday-median", "normal"), ("line", "weekday-median", "normal")]

    seen.clear()
    main(
        args(tmp_path, cfg, *run, *local, "--anomaly-sensitivity", "high"),
        client_factory=lambda c, company_id=None: fake,
    )
    assert seen == [("view", "weekday-median", "high"), ("line", "weekday-median", "high")]

    seen.clear()
    individual = ["individual", "--run", "1", "--dimension", "evar17", "--metric", "event1"]
    main(
        args(
            tmp_path,
            cfg,
            *individual,
            "--out",
            str(out),
            "--anomaly-method",
            "weekday-iqr",
            "--anomaly-sensitivity",
            "low",
        ),
        client_factory=lambda c, company_id=None: fake,
    )
    assert seen == [("view", "weekday-iqr", "low")]

    seen.clear()
    out2 = tmp_path / "o2"
    db = ["--db", str(tmp_path / "t.duckdb"), "--out", str(out2)]
    code = main(["render", "--run", "1", *db, *local, "--anomaly-sensitivity", "high"])
    assert code == 0
    assert seen == [("view", "weekday-median", "high")]
    page = next(out2.glob("*.html")).read_text(encoding="utf-8")
    assert "<dt>Anomaly detection</dt><dd>Weekday median, high sensitivity <span" in page


SENSITIVITY_COMMANDS = [
    ["run", "--rsid", "rs1"],
    ["individual", "--run", "1", "--dimension", "page", "--metric", "event1"],
    ["render", "--run", "1"],
]


@pytest.mark.parametrize("command", SENSITIVITY_COMMANDS, ids=lambda c: c[0])
@pytest.mark.parametrize("sensitivity", ["low", "high"])
@pytest.mark.parametrize("method", [[], ["--anomaly-method", "adobe"]], ids=["default", "adobe"])
def test_anomaly_sensitivity_needs_a_local_method(
    tmp_path, cfg, capsys, command, sensitivity, method
):
    def no_sign_in(*a, **k):
        raise AssertionError("The command should stop before it signs in.")

    db = tmp_path / "t.duckdb"
    rest = [*method, "--anomaly-sensitivity", sensitivity]
    code = main(
        [command[0], "--config", str(cfg), "--db", str(db), *command[1:], *rest]
        if command[0] != "render"
        else [*command, "--db", str(db), *rest],
        client_factory=no_sign_in,
    )
    assert code == 1
    assert capsys.readouterr().err == (
        "Error: --anomaly-sensitivity works only with a local anomaly method, e.g., "
        "--anomaly-method weekday-median. The adobe method uses the expected range that Adobe "
        "returned, which ahd does not change.\n"
    )
    # It stops before it opens or makes the database.
    assert not db.exists()


@pytest.mark.parametrize("command", SENSITIVITY_COMMANDS, ids=lambda c: c[0])
def test_anomaly_sensitivity_normal_works_with_adobe(tmp_path, capsys, command):
    # normal is the default, so it may be given with adobe. The command then goes on, and here
    # stops later because there is no stored run.
    from ahd.cli import _parser

    assert _parser().parse_args(command).anomaly_sensitivity == "normal"
    db = ["--db", str(tmp_path / "t.duckdb")]
    rest = [*db, "--anomaly-method", "adobe", "--anomaly-sensitivity", "normal"]
    argv = [command[0], *rest, *command[1:]]
    assert main(argv, client_factory=lambda *a, **k: None) == 1
    err = capsys.readouterr().err
    assert "--anomaly-sensitivity" not in err and err.startswith("Error: ")


@pytest.mark.parametrize("command", SENSITIVITY_COMMANDS, ids=lambda c: c[0])
@pytest.mark.parametrize("bad", ["nope", "Normal", "medium"])
def test_anomaly_sensitivity_must_be_a_known_level(tmp_path, capsys, command, bad):
    with pytest.raises(SystemExit) as stop:
        main([*command, "--db", str(tmp_path / "t.duckdb"), "--anomaly-sensitivity", bad])
    assert stop.value.code == 2
    assert "--anomaly-sensitivity: invalid choice" in capsys.readouterr().err


@pytest.mark.parametrize(
    "command",
    [
        ["run", "--rsid", "rs1"],
        ["individual", "--run", "1", "--dimension", "page", "--metric", "event1"],
        ["render", "--run", "1"],
    ],
    ids=lambda c: c[0],
)
@pytest.mark.parametrize("bad", ["nope", "Adobe", "weekday_median"])
def test_anomaly_method_must_be_a_known_method(tmp_path, capsys, command, bad):
    with pytest.raises(SystemExit) as stop:
        main([*command, "--db", str(tmp_path / "t.duckdb"), "--anomaly-method", bad])
    assert stop.value.code == 2
    assert "--anomaly-method: invalid choice" in capsys.readouterr().err


def test_the_alert_line_counts_by_the_chosen_method(tmp_path):
    from datetime import date, timedelta

    from ahd.api.reports import Column, DailyValue
    from ahd.catalog import Component
    from ahd.cli import _alert_line

    store = Store.open(tmp_path / "a.duckdb")
    rid = store.create_run("rs1", "S", None, date(2026, 11, 7), "UTC", "0")
    cid = "metrics/event1"
    store.add_components(
        rid, [Component(cid, "E1", "metric", "custom_events", 1, Column(cid, cid), True)]
    )
    values, day = [], date(2026, 8, 8)
    while day <= date(2026, 11, 7):
        # 100 on every Friday, and 50 on the last one: a large drop, and below a local range.
        v = 50.0 if day == date(2026, 11, 6) else 100.0 if day.weekday() == 4 else 0.0
        values.append(DailyValue(cid, day, v, None, None, None, None))
        day += timedelta(days=1)
    store.add_daily_values(rid, values)
    store.finish_run(rid, "ok")
    assert _alert_line(store, rid, 0.3, 0.5) == (
        "Alerts for 2026-11-06: 0 unusual values, 1 large drop, 0 large spikes, 0 drops to zero"
    )
    assert _alert_line(store, rid, 0.3, 0.5, method="weekday-median") == (
        "Alerts for 2026-11-06: 1 unusual value, 0 large drops, 0 large spikes, 0 drops to zero"
    )
    # A second run: the Fridays vary around 100, and the last one is 125. That is inside the
    # range of normal, 66.6 to 133.4, and above the range of high, 77.8 to 122.2.
    rid = store.create_run("rs1", "S", None, date(2026, 11, 7), "UTC", "0")
    store.add_components(
        rid, [Component(cid, "E1", "metric", "custom_events", 1, Column(cid, cid), True)]
    )
    fridays = iter([100.0, 110.0, 90.0, 100.0, 120.0, 80.0, 100.0, 105.0, 95.0, 100.0, 130.0, 70.0])
    values, day = [], date(2026, 8, 8)
    while day <= date(2026, 11, 7):
        v = 125.0 if day == date(2026, 11, 6) else next(fridays) if day.weekday() == 4 else 0.0
        values.append(DailyValue(cid, day, v, None, None, None, None))
        day += timedelta(days=1)
    store.add_daily_values(rid, values)
    store.finish_run(rid, "ok")
    counts = {
        s: _alert_line(store, rid, 0.3, 0.5, method="weekday-median", sensitivity=s)
        for s in ("low", "normal", "high")
    }
    assert counts == {
        "low": "Alerts for 2026-11-06: 0 unusual values, 0 large drops, 0 large spikes, 0 drops to zero",
        "normal": (
            "Alerts for 2026-11-06: 0 unusual values, 0 large drops, 0 large spikes, 0 drops to zero"
        ),
        "high": "Alerts for 2026-11-06: 1 unusual value, 0 large drops, 0 large spikes, 0 drops to zero",
    }
    store.con.close()


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


def test_names_that_the_output_encoding_cannot_show_do_not_crash(cfg, monkeypatch):
    # On Windows, output sent to a file or a pipe uses an older code page, e.g., cp1252.
    import io

    from fakes import FakeClient

    fake = FakeClient(
        gets={
            "/reportsuites/collections/suites": {
                "content": [{"rsid": "jp1", "name": "日本サイト"}],
                "lastPage": True,
            }
        }
    )
    out = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    err = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr("sys.stdout", out)
    monkeypatch.setattr("sys.stderr", err)
    assert main(["suites", "--config", str(cfg)], client_factory=lambda c: fake) == 0
    out.flush()
    assert "jp1  ?????" in out.buffer.getvalue().decode("cp1252")


def test_output_without_reconfigure_is_left_as_it_is(monkeypatch):
    import io

    from ahd.cli import _replace_unknown_characters

    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    _replace_unknown_characters()
    print("日本サイト")
    assert out.getvalue() == "日本サイト\n"


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
    html = next(out.glob("*.html")).read_text(encoding="utf-8")
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
    from ahd.store import Store

    Store.open(tmp_path / "t.duckdb").con.close()

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


def test_an_answer_in_another_form_is_one_line_for_each_command(tmp_path, cfg, capsys):
    """No command ends with an error trace when Adobe sends a list where an object belongs."""
    for command, path, answer in (
        (["suites"], "/reportsuites/collections/suites", [{"rsid": "rs1"}]),
        (["segments", "--rsid", "rs1"], "/segments", [{"id": "s1"}]),
        (["check"], "/reportsuites/collections/suites", {"content": 5}),
        (["check", "--rsid", "rs1"], "/metrics", {"content": []}),
        (["check", "--rsid", "rs1"], "/reportsuites/collections/suites/rs1", [1]),
    ):
        fake = fake_for("rs1", events(1), custom_dims())
        fake.gets[path] = answer
        code = main(
            [command[0], "--config", str(cfg), *command[1:]],
            client_factory=lambda creds, company_id=None, fake=fake: fake,
        )
        err = capsys.readouterr().err
        assert code == 1, command
        assert err.startswith("Error: ") and len(err.splitlines()) == 1, command
        assert "invalid_response" in err and "could not read" in err


def test_ids_and_names_of_another_type_do_not_stop_the_lists(cfg, capsys):
    fake = fake_for("rs1", events(1), custom_dims())
    fake.gets["/reportsuites/collections/suites"] = {
        "content": [{"rsid": 5, "name": "a"}, {"rsid": "rs1", "name": 7}]
    }
    fake.gets["/segments"] = {"content": [{"id": 5, "name": "a"}, {"id": "s1", "name": "Seg"}]}
    factory = lambda creds, company_id=None: fake
    assert main(["suites", "--config", str(cfg)], client_factory=factory) == 0
    assert capsys.readouterr().out.splitlines()[-1].split() == ["rs1"]
    assert main(["segments", "--config", str(cfg), "--rsid", "rs1"], client_factory=factory) == 0
    assert capsys.readouterr().out.splitlines()[-1].split() == ["s1", "Seg"]
