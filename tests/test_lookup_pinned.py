"""The credential that each combination of sources gives, as of release 0.1.22.

Each test runs a whole command and reads the credential that reaches the sign-in. The plain
variables ORG_ID, CLIENT_ID, SECRET and SCOPES and a .env file in the working folder are added
to every combination that already gave a result in 0.1.22. They must not change it.
"""

import json
import os
from pathlib import Path

import pytest
from fakes import FakeClient

from ahd.cli import main
from ahd.config import profile_dir

A = {
    "org_id": "A1B2C3D4E5F6A7B8C9D0E1F2@AdobeOrg",
    "client_id": "aaaa1111bbbb2222cccc3333dddd4444",
    "secret": "fake-secret-A-not-a-credential-0001",
    "scopes": "openid,AdobeID,additional_info.projectedProductContext",
}
B = {
    "org_id": "B9B8B7B6B5B4B3B2B1B0BABB@AdobeOrg",
    "client_id": "bbbb5555cccc6666dddd7777eeee8888",
    "secret": "fake-secret-B-not-a-credential-0002",
    "scopes": "openid,AdobeID,read_organizations",
}
# A third credential for the plain variables and the .env file, so that a result that came
# from one of them shows.
C = {
    "org_id": "C0C0C0C0C0C0C0C0C0C0C0C0@AdobeOrg",
    "client_id": "cccc9999dddd0000eeee1111ffff2222",
    "secret": "fake-secret-C-not-a-credential-0003",
    "scopes": "openid,AdobeID",
}
FIELDS = ("org_id", "client_id", "secret", "scopes")
EXTRAS = ("nothing", "plain variables", ".env", "plain variables and .env")


@pytest.fixture(params=EXTRAS)
def folder(request, tmp_path, monkeypatch):
    """An empty working folder, with the plain variables or a .env file or both."""
    monkeypatch.chdir(tmp_path)
    for name in ("ORG_ID", "CLIENT_ID", "SECRET", "SCOPES"):
        monkeypatch.delenv(name, raising=False)
    if "plain" in request.param:
        for key, value in C.items():
            monkeypatch.setenv(key.upper(), value)
    if ".env" in request.param:
        lines = [f"{key.upper()}={value}" for key, value in C.items()]
        # A .env file must not choose the profile or the anomaly method either.
        lines += ["AA_PROFILE=fromdotenv", "AHD_ANOMALY_METHOD=no-such-method"]
        (tmp_path / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return tmp_path


def _file(path: Path, values: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values), encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o600)
    return path


def _profile(name: str, values: dict) -> None:
    _file(profile_dir(name) / "config.json", values)


def _variables(monkeypatch, values: dict, *leave_out: str) -> None:
    for key, value in values.items():
        if key not in leave_out:
            monkeypatch.setenv(f"AHD_{key.upper()}", value)


def _run(capsys, *options: str):
    """Run ahd suites. Gives the exit code, the credential that reached the sign-in as
    (values, source), or None when the command stopped before it, and the error text."""
    seen = []

    def factory(creds, company_id=None):
        seen.append(creds)
        return FakeClient(
            gets={"/reportsuites/collections/suites": {"content": [{"rsid": "r", "name": "R"}]}}
        )

    try:
        code = main(["suites", *options], client_factory=factory)
    except SystemExit as stop:
        code = stop.code
    out, err = capsys.readouterr()
    if not seen:
        return code, None, err
    [creds] = seen
    assert f"Signed in using {creds.source}\n" in out
    return code, ({k: getattr(creds, k) for k in FIELDS}, creds.source), err


def test_the_profile_option_wins_over_everything(folder, monkeypatch, capsys):
    _profile("prod", A)
    _profile("other", B)
    monkeypatch.setenv("AA_PROFILE", "other")
    _variables(monkeypatch, B)
    _file(folder / "config.json", B)
    assert _run(capsys, "--profile", "prod")[:2] == (0, (A, "profile prod"))


def test_the_profile_variable_wins_over_the_variables_and_the_file(folder, monkeypatch, capsys):
    _profile("prod", A)
    monkeypatch.setenv("AA_PROFILE", "prod")
    _variables(monkeypatch, B)
    _file(folder / "config.json", B)
    assert _run(capsys)[:2] == (0, (A, "profile prod"))


def test_a_profile_alone_is_used(folder, monkeypatch, capsys):
    _profile("prod", A)
    assert _run(capsys, "--profile", "prod")[:2] == (0, (A, "profile prod"))
    monkeypatch.setenv("AA_PROFILE", "prod")
    assert _run(capsys)[:2] == (0, (A, "profile prod"))


def test_a_profile_that_does_not_exist_stops_the_command(folder, monkeypatch, capsys):
    _variables(monkeypatch, B)
    _file(folder / "config.json", B)
    code, found, err = _run(capsys, "--profile", "missing")
    assert (code, found) == (1, None) and "Profile 'missing' was not found" in err


def test_a_profile_without_a_key_stops_the_command(folder, monkeypatch, capsys):
    _profile("prod", {k: v for k, v in A.items() if k != "scopes"})
    _variables(monkeypatch, B)
    code, found, err = _run(capsys, "--profile", "prod")
    assert (code, found) == (1, None)
    assert err == "Error: Missing credential keys in profile prod: scopes\n"


def test_a_profile_option_and_a_config_option_stop_the_command(folder, capsys):
    _profile("prod", A)
    path = _file(folder / "given.json", B)
    code, found, err = _run(capsys, "--profile", "prod", "--config", str(path))
    assert (code, found) == (2, None) and "Use a profile or --config, not both" in err


def test_the_profile_variable_and_a_config_option_stop_the_command(folder, monkeypatch, capsys):
    _profile("prod", A)
    monkeypatch.setenv("AA_PROFILE", "prod")
    path = _file(folder / "given.json", B)
    code, found, err = _run(capsys, "--config", str(path))
    assert (code, found) == (1, None)
    assert "Use a profile or --config, not both" in err and "AA_PROFILE variable" in err


def test_all_four_variables_win_over_the_file_in_the_folder(folder, monkeypatch, capsys):
    _variables(monkeypatch, A)
    _file(folder / "config.json", B)
    assert _run(capsys)[:2] == (0, (A, "environment variables"))


def test_all_four_variables_alone_are_used(folder, monkeypatch, capsys):
    _variables(monkeypatch, A)
    assert _run(capsys)[:2] == (0, (A, "environment variables"))


def test_all_four_variables_and_a_config_option_stop_the_command(folder, monkeypatch, capsys):
    _variables(monkeypatch, A)
    path = _file(folder / "given.json", B)
    code, found, err = _run(capsys, "--config", str(path))
    assert (code, found) == (1, None) and "Use --config or the four variables" in err


@pytest.mark.parametrize("option", [False, True], ids=["file in the folder", "config option"])
def test_some_variables_win_over_the_same_keys_of_the_file(folder, monkeypatch, capsys, option):
    _variables(monkeypatch, A, "secret", "scopes")
    path = _file(folder / ("given.json" if option else "config.json"), B)
    mixed = {**B, "org_id": A["org_id"], "client_id": A["client_id"]}
    options = ("--config", str(path)) if option else ()
    assert _run(capsys, *options)[:2] == (0, (mixed, f"{path.name} and environment variables"))


@pytest.mark.parametrize("option", [False, True], ids=["file in the folder", "config option"])
def test_a_file_alone_is_used(folder, capsys, option):
    path = _file(folder / ("given.json" if option else "config.json"), A)
    options = ("--config", str(path)) if option else ()
    assert _run(capsys, *options)[:2] == (0, (A, path.name))


def test_a_config_option_wins_over_the_file_in_the_folder(folder, capsys):
    _file(folder / "config.json", B)
    path = _file(folder / "given.json", A)
    assert _run(capsys, "--config", str(path))[:2] == (0, (A, "given.json"))


def test_a_file_without_a_key_stops_the_command(folder, capsys):
    _file(folder / "config.json", {k: v for k, v in A.items() if k != "secret"})
    code, found, err = _run(capsys)
    assert (code, found) == (1, None)
    assert err == "Error: Missing credential keys in config.json: secret\n"


def test_a_file_that_is_not_json_stops_the_command(folder, capsys):
    (folder / "config.json").write_text("{", encoding="utf-8")
    code, found, err = _run(capsys)
    assert (code, found) == (1, None) and "is not valid UTF-8 JSON" in err


def test_some_variables_without_a_file_stop_the_command(folder, monkeypatch, capsys):
    _variables(monkeypatch, A, "secret", "scopes")
    code, found, err = _run(capsys)
    assert (code, found) == (1, None)
    assert err == (
        "Error: Missing credential keys in environment variables: AHD_SECRET, AHD_SCOPES\n"
    )


def test_no_source_at_all_stops_the_command(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    for name in ("ORG_ID", "CLIENT_ID", "SECRET", "SCOPES"):
        monkeypatch.delenv(name, raising=False)
    code, found, err = _run(capsys)
    assert (code, found) == (1, None) and err.startswith("Error: No credential was found.")
