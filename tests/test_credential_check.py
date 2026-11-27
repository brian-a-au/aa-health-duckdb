"""ahd config, ahd check --format json, the plain variables, the .env file, the notices for
0.2.0 and the warning for a credential file that others can read."""

import json
import logging
import os
from pathlib import Path

import pytest
import requests
from fakes import ApiError, FakeClient, fake_for

from ahd import config
from ahd.api.auth import AFTER_TOKEN, WRONG_SECRET, SignInError, failed_step
from ahd.cli import main
from ahd.config import (
    NO_CREDENTIAL,
    Credentials,
    find,
    load_credentials,
    mask_client_id,
    others_can_read,
    profile_dir,
    read_dotenv,
)

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
FIELDS = ("org_id", "client_id", "secret", "scopes")
not_windows = pytest.mark.skipif(os.name == "nt", reason="Windows has no file modes")


def _file(name: str, values: dict, mode: int = 0o600) -> Path:
    path = Path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values), encoding="utf-8")
    path.chmod(mode)
    return path


def _dotenv(values: dict, mode: int = 0o600) -> None:
    Path(".env").write_text(
        "".join(f"{k.upper()}={v}\n" for k, v in values.items()), encoding="utf-8"
    )
    Path(".env").chmod(mode)


def _set(monkeypatch, values: dict, prefix: str = "", leave_out: tuple = ()) -> None:
    for key, value in values.items():
        if key not in leave_out:
            monkeypatch.setenv(f"{prefix}{key.upper()}", value)


def _values(creds: Credentials) -> dict:
    return {k: getattr(creds, k) for k in FIELDS}


def _codes(look) -> list[str]:
    return [n.code for n in look.notices]


def _suites(*rows):
    content = [{"rsid": r, "name": r.upper()} for r in rows]
    return FakeClient(gets={"/reportsuites/collections/suites": {"content": content}})


# ---- the masks ---------------------------------------------------------------


def test_the_repr_of_a_credential_masks_the_client_id_and_leaves_out_the_secret():
    text = repr(Credentials(**A))
    assert "aaaa****4444" in text and A["client_id"] not in text and A["secret"] not in text
    assert mask_client_id("12345678") == "****" and mask_client_id("") == "****"


# ---- the plain variables and the .env file -----------------------------------


def test_the_plain_variables_are_used_when_nothing_else_gives_a_credential(monkeypatch):
    _set(monkeypatch, A)
    _dotenv(B)
    look = find(None)
    assert _values(look.credentials) == A and look.credentials.kind == "environment"
    assert look.credentials.source == "environment variables ORG_ID, CLIENT_ID, SECRET and SCOPES"
    assert look.notices == [] and [s.kind for s in look.sources] == ["environment"]


def test_the_dotenv_file_is_used_after_the_plain_variables(monkeypatch):
    _dotenv(A)
    look = find(None)
    assert _values(look.credentials) == A
    assert (look.credentials.source, look.credentials.kind) == (".env", "dotenv")
    assert look.notices == []
    assert load_credentials(None).source == ".env"


def test_a_partial_set_of_plain_variables_is_not_mixed_with_the_dotenv_file(monkeypatch):
    _set(monkeypatch, B, leave_out=("secret",))
    _dotenv(A)
    look = find(None)
    assert _values(look.credentials) == A
    assert _codes(look) == ["partial_source_skipped"] and "SECRET is not" in look.notices[0].text
    [plain, dotenv] = look.sources
    assert (plain.kind, plain.skipped, plain.missing) == ("environment", "partial", ("secret",))
    assert (dotenv.kind, dotenv.used) == ("dotenv", True)


def test_partial_plain_variables_and_a_partial_dotenv_file_give_no_credential(monkeypatch):
    _set(monkeypatch, A, leave_out=("secret", "scopes"))
    _dotenv({"org_id": A["org_id"]})
    look = find(None)
    assert look.credentials is None and look.error.code == "no_credential"
    assert str(look.error) == (
        f"{NO_CREDENTIAL} Of the variables ORG_ID, CLIENT_ID, SECRET and SCOPES, these are not "
        "set: SECRET, SCOPES. The .env file in this folder lacks CLIENT_ID, SECRET, SCOPES."
    )
    assert [(s.kind, s.skipped) for s in look.sources] == [
        ("environment", "partial"),
        ("dotenv", "partial"),
    ]
    assert A["org_id"] not in str(look.error)


def test_a_dotenv_file_is_read_line_by_line_for_the_four_names_only(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "# a comment\n"
        "export ORG_ID='o@AdobeOrg'\n"
        'CLIENT_ID = "the client"\n'
        "SECRET=s3 # the secret\n"
        "SCOPES=\n"
        "AHD_SECRET=other\n"
        "not a line\n",
        encoding="utf-8",
    )
    assert read_dotenv(path) == {
        "org_id": "o@AdobeOrg",
        "client_id": "the client",
        "secret": "s3",
        "scopes": None,
    }


def test_a_dotenv_file_that_cannot_be_read_gives_nothing(tmp_path):
    path = tmp_path / ".env"
    path.write_bytes(b"SECRET=\xff\xfe\n")
    assert not any(read_dotenv(path).values())
    assert not any(read_dotenv(tmp_path / "missing").values())


def test_a_dotenv_file_does_not_reach_the_environment(monkeypatch):
    _dotenv(A)
    Path(".env").write_text(
        Path(".env").read_text(encoding="utf-8") + "AA_PROFILE=other\n", encoding="utf-8"
    )
    before = dict(os.environ)
    assert find(None).credentials.kind == "dotenv"
    assert dict(os.environ) == before


# ---- the notices for 0.2.0 ---------------------------------------------------


def test_plain_variables_beside_a_config_file_give_a_notice(monkeypatch):
    _file("config.json", A)
    _set(monkeypatch, B)
    look = find(None)
    assert _values(look.credentials) == A and _codes(look) == ["plain_variables_not_used"]
    assert "uses config.json" in look.notices[0].text and "0.2.0" in look.notices[0].text
    assert B["secret"] not in look.notices[0].text


def test_some_plain_variables_beside_a_config_file_give_a_notice(monkeypatch):
    _file("config.json", A)
    _set(monkeypatch, B, leave_out=("secret", "scopes"))
    look = find(None)
    assert _values(look.credentials) == A and _codes(look) == ["partial_source_skipped"]
    assert "SECRET, SCOPES are not" in look.notices[0].text


def test_some_ahd_variables_over_a_config_file_give_a_notice(monkeypatch):
    path = _file("given.json", B)
    _set(monkeypatch, A, "AHD_", leave_out=("secret", "scopes"))
    for look in (find(path), find(None)):
        if look.error is None:
            assert _codes(look) == ["mixed_sources"]
            assert "AHD_ORG_ID, AHD_CLIENT_ID from the variables" in look.notices[0].text
            assert [(s.kind, s.used) for s in look.sources] == [
                ("environment", True),
                ("flag_config", True),
            ]
    assert find(None).error.code == "partial_source"


def test_the_config_option_gets_no_notice_for_the_plain_variables_or_dotenv(monkeypatch):
    path = _file("given.json", A)
    _set(monkeypatch, B)
    _dotenv(B)
    look = find(path)
    assert _values(look.credentials) == A and look.notices == []
    assert [s.kind for s in look.sources] == ["flag_config", "environment"]


def test_a_profile_gets_no_notice(monkeypatch):
    _file(str(profile_dir("prod") / "config.json"), A)
    _set(monkeypatch, B)
    _set(monkeypatch, B, "AHD_", leave_out=("secret",))
    _dotenv(B)
    look = find(None, "prod")
    assert _values(look.credentials) == A and look.notices == [] and look.profile == "prod"
    assert [(s.kind, s.skipped) for s in look.sources] == [
        ("flag_profile", None),
        ("environment", "partial"),
        ("environment", "lower_priority"),
    ]


def test_the_two_forms_of_the_variables_with_different_values_give_a_notice(monkeypatch):
    _set(monkeypatch, A, "AHD_")
    _set(monkeypatch, {**A, "secret": B["secret"]})
    look = find(None)
    assert _values(look.credentials) == A and _codes(look) == ["variable_forms_differ"]
    assert "AHD_SECRET and SECRET are both set" in look.notices[0].text
    assert A["secret"] not in look.notices[0].text and B["secret"] not in look.notices[0].text


def test_the_two_forms_beside_a_config_file_give_one_notice_for_the_forms(monkeypatch):
    _file("config.json", A)
    monkeypatch.setenv("AHD_SCOPES", "openid")
    _set(monkeypatch, B)
    look = find(None)
    assert look.credentials.scopes == "openid"
    assert _codes(look) == ["mixed_sources", "variable_forms_differ"]


def test_a_dotenv_file_beside_a_config_file_gives_a_notice_when_they_differ():
    _file("config.json", A)
    _dotenv(A)
    assert find(None).notices == []
    _dotenv(B)
    look = find(None)
    assert _values(look.credentials) == A and _codes(look) == ["dotenv_and_config_differ"]
    assert [(s.kind, s.skipped) for s in look.sources] == [
        ("default_config", None),
        ("dotenv", "lower_priority"),
    ]


def test_a_partial_dotenv_file_beside_a_config_file_gives_a_notice():
    _file("config.json", A)
    _dotenv({"org_id": B["org_id"], "client_id": B["client_id"], "scopes": B["scopes"]})
    look = find(None)
    assert _values(look.credentials) == A and _codes(look) == ["partial_source_skipped"]
    assert "SECRET is not in it" in look.notices[0].text


def test_an_error_has_no_notice(monkeypatch):
    _file("config.json", {k: v for k, v in A.items() if k != "secret"})
    _set(monkeypatch, B)
    look = find(None)
    assert look.error.code == "partial_source" and look.error.missing == ("secret",)
    assert look.notices == [] and look.credentials is None


def test_a_file_without_any_key_is_no_source(monkeypatch):
    _file("config.json", {}, 0o644)
    _file(str(profile_dir("prod") / "config.json"), {}, 0o644)
    for look in (find(None), find(None, "prod")):
        assert look.error.code == "partial_source" and look.error.missing == FIELDS
        assert look.sources == [] and look.warnings == []


# ---- names, keys and file modes ----------------------------------------------


def test_a_profile_name_of_more_than_64_characters_is_not_valid():
    assert profile_dir("a" * 64).name == "a" * 64
    for name in ("a" * 65, "prod\n"):
        look = find(None, name)
        assert look.error.code == "invalid_profile_name"
        assert "64 characters or fewer" in str(look.error)


def test_an_unknown_key_of_a_file_is_named_in_the_debug_log(caplog):
    _file("config.json", {**A, "note": "private text", "sandbox": "dev"})
    _file(str(profile_dir("prod") / "config.json"), {**A, "extra": 1})
    with caplog.at_level(logging.DEBUG, logger="ahd.config"):
        assert find(None).error is None and find(None, "prod").error is None
    assert caplog.messages == [
        "Config file: the key 'note' is not known and was ignored (unknown_key).",
        "Profile 'prod': the key 'extra' is not known and was ignored (unknown_key).",
    ]
    assert "sandbox" in find(None).sources[0].fields


@not_windows
def test_one_warning_names_every_credential_file_that_others_can_read():
    _file("config.json", A, 0o644)
    _dotenv(B, 0o640)
    look = find(None)
    [warning] = look.warnings
    assert warning.code == "file_readable_by_others"
    assert "config.json and .env" in warning.text and "chmod 600 config.json" in warning.text
    Path("config.json").chmod(0o600)
    Path(".env").chmod(0o600)
    assert find(None).warnings == []


@not_windows
def test_a_file_that_others_can_read_is_named_also_when_it_lacks_a_key():
    _file("config.json", {"org_id": A["org_id"]}, 0o604)
    look = find(None)
    assert look.error.code == "partial_source" and len(look.warnings) == 1


def test_the_file_mode_is_not_checked_on_windows_or_for_a_missing_file(tmp_path, monkeypatch):
    path = _file(str(tmp_path / "open.json"), A, 0o644)
    assert not others_can_read(tmp_path / "missing.json")
    monkeypatch.setattr(config.os, "name", "nt")
    assert not others_can_read(path)


# ---- ahd config --------------------------------------------------------------


def test_config_prints_the_credential_without_the_secret(capsys):
    _file(str(profile_dir("prod") / "config.json"), A)
    code = main(["config", "--profile", "prod"])
    out, err = capsys.readouterr()
    assert code == 0 and err == ""
    assert out.splitlines() == [
        "Credential: found",
        "Source: profile prod",
        "Profile: prod",
        f"File: {profile_dir('prod') / 'config.json'}",
        f"Organization ID: {A['org_id']}",
        "Client ID: aaaa****4444",
        "Secret: ****",
        f"Scopes: {A['scopes']}",
    ]


def test_config_lists_the_sources_that_were_not_used_and_the_notices(monkeypatch, capsys):
    _file("config.json", A)
    _set(monkeypatch, B, leave_out=("secret",))
    _dotenv(B)
    code = main(["config"])
    out, err = capsys.readouterr()
    assert code == 0 and err == ""
    assert "Source: config.json\n" in out and "Sources that were not used:\n" in out
    assert (
        "  environment variables ORG_ID, CLIENT_ID, SECRET and SCOPES: it lacks secret. "
        "It holds org_id, client_id, scopes.\n"
    ) in out
    assert f"  .env ({Path('.env').absolute()}): another source is used first." in out
    assert "Notice (partial_source_skipped): " in out
    assert "Notice (dotenv_and_config_differ): " in out
    assert B["secret"] not in out and A["secret"] not in out


def test_config_reports_no_credential_as_a_problem(capsys):
    code = main(["config"])
    out, err = capsys.readouterr()
    assert code == 1 and err == ""
    assert out.splitlines() == [
        "Credential: not found",
        "Organization ID: not set",
        "Client ID: not set",
        "Secret: not set",
        "Scopes: not set",
        f"Problem (no_credential): {NO_CREDENTIAL}",
    ]


def test_config_shows_what_a_partial_file_holds(capsys):
    _file("config.json", {k: v for k, v in A.items() if k != "secret"})
    code = main(["config", "--format", "json"])
    report = json.loads(capsys.readouterr().out)
    assert code == 1 and report["ok"] is False
    assert report["credential"] == {
        "org_id": A["org_id"],
        "client_id": "aaaa****4444",
        "secret_set": False,
        "scopes": A["scopes"],
    }
    [problem] = report["problems"]
    assert (problem["code"], problem["missing"]) == ("partial_source", ["secret"])
    [source] = report["sources"]
    assert (source["used"], source["skipped"], source["missing"]) == (False, "partial", ["secret"])


@pytest.mark.parametrize("variable", [False, True], ids=["option", "AA_PROFILE"])
def test_config_reports_a_profile_with_a_config_option_as_a_problem(monkeypatch, capsys, variable):
    path = _file("given.json", A)
    options = ["--config", str(path)]
    if variable:
        monkeypatch.setenv("AA_PROFILE", "prod")
    else:
        options += ["--profile", "prod"]
    code = main(["config", "--format", "json", *options])
    report = json.loads(capsys.readouterr().out)
    assert code == 1 and report["profile"] == "prod"
    [problem] = report["problems"]
    assert problem["code"] == "profile_and_config_both_given"
    assert ("AA_PROFILE variable" if variable else "--profile") in problem["message"]


def test_config_reports_a_config_option_with_all_four_variables_as_a_problem(monkeypatch, capsys):
    path = _file("given.json", A)
    _set(monkeypatch, B, "AHD_")
    code = main(["config", "--config", str(path)])
    out = capsys.readouterr().out
    assert code == 1 and "Problem (no_credential): Use --config or the four variables" in out
    assert "AHD_ environment variables: another source is used first" in out


@not_windows
def test_config_prints_the_warning_for_a_file_that_others_can_read(capsys):
    _file("config.json", A, 0o644)
    code = main(["config"])
    out, err = capsys.readouterr()
    assert code == 0 and err == ""
    assert out.count("Warning (file_readable_by_others): Other users of this computer") == 1


# ---- the notices and the warning of the other commands -----------------------


@not_windows
def test_a_command_prints_the_warning_and_the_notices_once_on_stderr(monkeypatch, capsys):
    _file("config.json", A, 0o644)
    _set(monkeypatch, B)
    code = main(["suites"], client_factory=lambda creds: _suites("one", "two"))
    out, err = capsys.readouterr()
    assert code == 0
    assert out.splitlines()[0] == "Signed in using config.json"
    assert "Warning" not in out and "Notice" not in out
    warning, notice = err.splitlines()
    assert warning.startswith("Warning (file_readable_by_others): Other users of this computer")
    assert notice.startswith("Notice (plain_variables_not_used): The variables ORG_ID")
    assert A["secret"] not in err and B["secret"] not in err


def test_a_command_uses_the_plain_variables_when_nothing_else_is_set(monkeypatch, capsys):
    _set(monkeypatch, A)
    seen = []
    code = main(["suites"], client_factory=lambda creds: seen.append(creds) or _suites("one"))
    out, err = capsys.readouterr()
    assert code == 0 and err == "" and _values(seen[0]) == A
    assert "Signed in using environment variables ORG_ID, CLIENT_ID, SECRET and SCOPES\n" in out


# ---- ahd check --format json -------------------------------------------------


def _check(capsys, factory, *options):
    code = main(["check", "--format", "json", *options], client_factory=factory)
    out, err = capsys.readouterr()
    return code, json.loads(out), err


def test_check_as_json_holds_the_two_results_and_the_lines_of_the_text(monkeypatch, capsys):
    _set(monkeypatch, A, "AHD_")
    code, report, err = _check(capsys, lambda creds: _suites("one", "two"))
    assert code == 0 and err == ""
    assert report == {
        "schema": "adobe-credentials/v1/live-test",
        "tool": "aa-health-duckdb",
        "tool_version": report["tool_version"],
        "product": "aa",
        "ok": True,
        "source": {"kind": "environment", "name": "environment variables"},
        "sign_in": {"ok": True, "code": None, "message": None},
        "product_access": {"ok": True, "code": None, "message": None},
        "details": [
            "Signed in using environment variables",
            "Company ID: testco",
            "Report suites visible: 2",
        ],
    }


def test_check_as_json_keeps_the_exit_code_of_the_text_format(monkeypatch, capsys):
    _file("config.json", A)
    code, report, _err = _check(capsys, lambda creds: _suites())
    assert code == 2 and report["ok"] is True
    assert report["source"] == {"kind": "default_config", "name": "config.json"}
    assert report["details"][-1].startswith("This credential signs in but sees no report suites")


def test_check_as_json_checks_a_suite(monkeypatch, capsys):
    _file("config.json", A)
    metrics = [{"id": "metrics/event1", "name": "Event 1"}]
    fake = fake_for("rs1", metrics, [{"id": "variables/prop1", "name": "Prop 1"}])
    fake.companies, fake.company_name = 2, "First"
    code, report, _err = _check(capsys, lambda creds: fake, "--rsid", "rs1")
    assert code == 0 and report["ok"] is True
    assert report["details"][1].startswith("This credential sees 2 login companies. Using First")
    assert report["details"][-1] == "All checks passed."


def test_check_as_json_reports_an_error_after_the_sign_in_in_the_details(monkeypatch, capsys):
    _file("config.json", A)
    fake = FakeClient(gets={"/reportsuites/collections/suites": ApiError(500, "down", None)})
    code, report, err = _check(capsys, lambda creds: fake)
    assert code == 1 and err == "" and report["ok"] is True
    assert report["details"] == [
        "Signed in using config.json",
        "Error: Adobe API error 500: down",
    ]


def test_check_as_json_names_a_wrong_secret_without_any_part_of_it(monkeypatch, capsys):
    _file("config.json", A)

    def factory(creds):
        raise SignInError(WRONG_SECRET, "bad_secret")

    code, report, err = _check(capsys, factory)
    assert code == 1 and err == "" and report["ok"] is False
    assert report["sign_in"] == {"ok": False, "code": "bad_secret", "message": WRONG_SECRET}
    assert report["product_access"] == {"ok": None, "code": None, "message": None}
    assert report["details"] == []


def test_check_as_json_tells_a_network_error_of_the_second_step(monkeypatch, capsys):
    _file("config.json", A)

    def factory(creds):
        error = requests.ConnectionError("https://example.invalid/secret-in-a-url")
        error.add_note(AFTER_TOKEN)
        raise error

    code, report, _err = _check(capsys, factory)
    assert code == 1 and report["sign_in"]["ok"] is True
    assert report["product_access"] == {
        "ok": False,
        "code": "unreachable",
        "message": "The request failed with a network error (ConnectionError).",
    }
    assert failed_step(requests.Timeout()) == "sign_in"


def test_check_as_json_stops_without_json_when_there_is_no_credential(capsys):
    code = main(["check", "--format", "json"])
    out, err = capsys.readouterr()
    assert code == 1 and out == "" and err == f"Error: {NO_CREDENTIAL}\n"


@not_windows
def test_check_as_json_keeps_the_notices_out_of_the_json(monkeypatch, capsys):
    _file("config.json", A, 0o644)
    _set(monkeypatch, B)
    code, report, err = _check(capsys, lambda creds: _suites("one"))
    assert code == 0 and report["ok"] is True
    assert [line.split(":")[0] for line in err.splitlines()] == [
        "Warning (file_readable_by_others)",
        "Notice (plain_variables_not_used)",
    ]
