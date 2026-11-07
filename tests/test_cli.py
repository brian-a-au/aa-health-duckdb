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

CONFIG = {"org_id": "o@AdobeOrg", "client_id": "cid-12345", "secret": "sec-67890",
          "scopes": "openid"}


def events(n):
    return [{"id": f"metrics/event{i}", "name": f"Event {i}"} for i in range(1, n + 1)]


def custom_dims():
    return [{"id": "variables/prop1", "name": "Prop 1", "allowedForReporting": True},
            {"id": "variables/page", "name": "Page", "allowedForReporting": True}]


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
    fake = fake_for("rs1", events(3), custom_dims(),
                    posts=[(is_trend, ApiError(403, "no", "invalid_access"))])
    code = main(
        args(tmp_path, cfg, "run", "--rsid", "rs1", "--date", "2026-11-07",
             "--out", str(tmp_path / "o")),
        client_factory=lambda creds, company_id=None: fake,
    )
    assert code == 1  # every request failed: no values


def test_run_partial_exit_code(tmp_path, cfg):
    fake = fake_for("rs1", events(3), custom_dims(),
                    posts=[(is_anomaly_trend, ApiError(403, "no", "invalid_access"))])
    out = tmp_path / "o"
    code = main(
        args(tmp_path, cfg, "run", "--rsid", "rs1", "--date", "2026-11-07", "--out", str(out)),
        client_factory=lambda creds, company_id=None: fake,
    )
    assert code == 3
    assert len(list(out.glob("*.html"))) == 1


def test_render_needs_no_client(tmp_path, cfg, capsys):
    fake = fake_for("rs1", events(3), custom_dims())
    main(args(tmp_path, cfg, "run", "--rsid", "rs1", "--date", "2026-11-07",
              "--out", str(tmp_path / "o1")),
         client_factory=lambda creds, company_id=None: fake)
    capsys.readouterr()

    def boom(*a, **k):
        raise AssertionError("client must not be created")

    out = tmp_path / "o2"
    code = main(["render", "--run", "1", "--db", str(tmp_path / "t.duckdb"), "--out", str(out)],
                client_factory=boom)
    assert code == 0
    assert len(list(out.glob("*.html"))) == 1


def test_runs_lists_runs(tmp_path, cfg, capsys):
    fake = fake_for("rs1", events(3), custom_dims())
    main(args(tmp_path, cfg, "run", "--rsid", "rs1", "--date", "2026-11-07",
              "--out", str(tmp_path / "o")),
         client_factory=lambda creds, company_id=None: fake)
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
    code = main(["check", "--config", str(cfg), "--rsid", "rs1"],
                client_factory=lambda c, company_id=None: fake)
    assert code == 2
    assert "product profile" in capsys.readouterr().out


def test_check_ok(tmp_path, cfg, capsys):
    fake = fake_for("rs1", events(3), custom_dims())
    code = main(["check", "--config", str(cfg), "--rsid", "rs1"],
                client_factory=lambda c, company_id=None: fake)
    out = capsys.readouterr().out
    assert code == 0 and "3" in out


def test_check_anomaly_denied(tmp_path, cfg, capsys):
    fake = fake_for("rs1", events(3), custom_dims(),
                    posts=[(is_trend, ApiError(403, "no", "invalid_access"))])
    code = main(["check", "--config", str(cfg), "--rsid", "rs1"],
                client_factory=lambda c, company_id=None: fake)
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
