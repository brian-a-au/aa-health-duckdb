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
    return [{"id": f"metrics/event{i}", "name": f"Event {i}"} for i in range(1, n + 1)]


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
    assert code == 1 and err.count("\n") == 1 and "newer" in err and "Traceback" not in err


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
