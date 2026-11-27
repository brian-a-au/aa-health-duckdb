"""The shared cases of the Adobe credentials standard, version 1, run against ahd.

tests/standard_cases holds a copy of the cases, made with sync_cases.py of the standard. This
module is the adapter: it builds each case in a temporary home and working folder, runs the
lookup or the command, and checks what the case expects. known-differences.json at the root of
the repository lists the cases that ahd does not meet yet. Each of them must fail here, and
every other case must pass, so the list can only get shorter.

How the words of a case map to ahd:

- The product is aa, and the profile variable is AA_PROFILE.
- config_check is ahd config --format json, and live_test is ahd check --format json.
- environment means the variables ORG_ID, CLIENT_ID, SECRET and SCOPES, the names of the
  standard. A case that says form AHD uses the AHD_ names.
- A case with no command runs find(), the lookup that every command uses.
- A case that makes a profile, or that starts a child process, is skipped. ahd only reads
  profiles and starts no child process.
"""

import json
import os
import re
from pathlib import Path
from typing import ClassVar

import pytest
import requests

from ahd.cli import main
from ahd.config import ConfigError, find, mask_client_id, profile_dir

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "tests" / "standard_cases"
TOOL = "aa-health-duckdb"
PRODUCT = "aa"
FIELDS = ("org_id", "client_id", "secret", "scopes")
FIXTURES = json.loads((CASES / "fixtures.json").read_text(encoding="utf-8"))["credentials"]
KNOWN = json.loads((ROOT / "known-differences.json").read_text(encoding="utf-8"))
EXPECTED_TO_FAIL = {case for d in KNOWN["differences"] for case in d.get("cases", [])}
# A secret that shares no 4 characters in a row with the secrets of the cases.
OTHER_SECRET = "QQQQQQQQQQQQQQQQ"


def _all_cases() -> list[dict]:
    cases = []
    for path in sorted(CASES.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "cases" in data and "area" in data:
            cases += data["cases"]
    return cases


ALL = _all_cases()


def _applies(case: dict) -> bool:
    given = case["given"]
    if PRODUCT not in case.get("products", [PRODUCT]) or TOOL not in case.get("tools", [TOOL]):
        return False
    # ahd makes no profile and starts no child process.
    return given.get("action") != "create_profile" and given.get("command") != "run_child"


def valid(value, schema: dict, where: str = "$") -> None:
    """A small check of a value against the parts of JSON Schema that the three shapes use."""
    kinds = {
        "object": dict,
        "array": list,
        "string": str,
        "boolean": bool,
        "null": type(None),
    }
    if "const" in schema:
        assert value == schema["const"], f"{where} is not {schema['const']!r}"
    if "enum" in schema:
        assert value in schema["enum"], f"{where} is {value!r}, not one of {schema['enum']}"
    if "type" in schema:
        names = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        assert any(type(value) is kinds[n] for n in names), f"{where} is not a {names}"
    if "pattern" in schema:
        assert re.search(schema["pattern"], value), f"{where} does not match the pattern"
    if "not" in schema and isinstance(value, dict):
        assert not all(k in value for k in schema["not"]["required"]), f"{where} holds a secret"
    if isinstance(value, dict):
        for key in schema.get("required", []):
            assert key in value, f"{where} lacks {key}"
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            assert set(value) <= set(properties), f"{where} holds unknown keys"
        for key, part in properties.items():
            if key in value:
                valid(value[key], part, f"{where}.{key}")
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            valid(item, schema["items"], f"{where}[{i}]")


def _schema(name: str) -> dict:
    return json.loads((CASES / f"{name}.schema.json").read_text(encoding="utf-8"))


def _values(source: dict) -> dict:
    """The values of a source of a case: a made-up credential with its changes."""
    values = dict(FIXTURES[source["credential"]]) if "credential" in source else {}
    for key in source.get("omit", []):
        del values[key]
    values.update(source.get("override", {}))
    return values


def _write_json(path: Path, source: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({**_values(source), **source.get("extra_keys", {})}), encoding="utf-8"
    )
    path.chmod(int(source.get("file_mode", "0600"), 8))


class Built:
    """A case built on disk and in the environment: the options for the command."""

    def __init__(self) -> None:
        self.options: list[str] = []
        self.profile: str | None = None
        self.config: Path | None = None


def build(case: dict, tmp_path: Path, monkeypatch, secret: str | None = None) -> Built:
    """Make the sources of the case. secret replaces the secret of every source."""
    work = tmp_path / "parent" / "work"
    work.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(work)
    built = Built()
    sources = dict(case["given"].get("sources", {}))
    if case["given"].get("command") == "live_test" and not sources:
        sources = {"environment": {"credential": "A"}}
    if "secret" in case["given"]:
        sources = {"environment": {"credential": "A", "override": case["given"]}}
    for kind, source in sources.items():
        source = dict(source)
        if secret is not None and "credential" in source and "secret" in _values(source):
            source["override"] = {**source.get("override", {}), "secret": secret}
        if kind in ("flag_profile", "profile_variable", "profile_variable_target"):
            if "credential" in source:
                _write_json(profile_dir(source["name"]) / "config.json", source)
            if kind == "flag_profile":
                built.profile = source["name"]
                built.options += ["--profile", source["name"]]
            elif kind == "profile_variable":
                monkeypatch.setenv("AA_PROFILE", source["name"])
        elif kind == "flag_config":
            built.config = work / "given.json"
            _write_json(built.config, source)
            built.options += ["--config", str(built.config)]
        elif kind in ("environment", "environment_plain"):
            prefix = "AHD_" if source.get("form") == "AHD" else ""
            for key, value in _values(source).items():
                monkeypatch.setenv(f"{prefix}{key.upper()}", value)
            if source.get("other_settings"):
                monkeypatch.setenv("AHD_ANOMALY_METHOD", "weekday-median")
        elif kind == "dotenv":
            lines = [f"{key.upper()}={value}" for key, value in _values(source).items()]
            for name, value in source.get("extra_keys", {}).items():
                lines.append(f"{'AA_PROFILE' if name == 'PROFILE_VARIABLE' else name}={value}")
            folder = work.parent if source.get("location") == "parent_folder" else work
            (folder / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")
        elif kind == "default_config":
            path = work / "config.json"
            if source.get("through_symlink"):
                real = tmp_path / "linked" / "real.json"
                _write_json(real, source)
                path.symlink_to(real)
            else:
                _write_json(path, source)
        else:
            raise AssertionError(f"The adapter does not know the source {kind}")
    return built


def _named(message: str, fields: list[str]) -> bool:
    """True when the text names each field, as a key or as a variable."""
    return all(f in message or f.upper() in message for f in fields)


def check_lookup(case: dict, built: Built) -> None:
    """A case with no command: the lookup alone."""
    expect = case["expect"]
    look = find(built.config, built.profile)
    codes = [n.code for n in look.notices]
    if "error" in expect:
        assert look.error is not None, "the lookup gave a credential"
        assert look.error.code == expect["error"]
        if "missing" in expect:
            assert list(look.error.missing) == expect["missing"]
        return
    assert look.error is None, f"the lookup stopped: {look.error}"
    creds = look.credentials
    assert creds.kind == expect["source"]
    assert {k: getattr(creds, k) for k in FIELDS} == FIXTURES[expect["credential"]]
    if "notice" in expect:
        assert codes == ([] if expect["notice"] is None else [expect["notice"]])


def _run(capsys, argv: list[str]) -> tuple[int, str, str]:
    capsys.readouterr()
    code = main(argv)
    out, err = capsys.readouterr()
    return code, out, err


def _leaks(secret: str, shown: str, baseline: str) -> list[str]:
    """The runs of 4 characters of the secret that the output shows.

    The secrets of the cases hold words such as "secret", which the output also holds for
    another reason, e.g., in the key secret_set. So a run of 4 characters counts only when
    the same command with another secret does not show it.
    """
    runs = {secret[i : i + 4] for i in range(len(secret) - 3)}
    return sorted(r for r in runs if r in shown and r not in baseline)


def check_config(case: dict, built: Built, capsys) -> str:
    """A case for the config check. Gives everything that the command printed."""
    expect = case["expect"]
    code, out, err = _run(capsys, ["--verbose", "config", "--format", "json", *built.options])
    report = json.loads(out)
    valid(report, _schema("config-check-v1"))
    assert code == (0 if report["ok"] else 1)
    used = [s for s in report["sources"] if s["used"]]
    credential = report["credential"]
    notices = {n["code"]: n["message"] for n in report["notices"]}
    problems = {p["code"]: p for p in report["problems"]}
    if "ok" in expect:
        assert report["ok"] is expect["ok"]
    if "source" in expect:
        assert expect["source"] in [s["kind"] for s in used]
    if "profile" in expect:
        assert report["profile"] == expect["profile"]
    if "profile_folder" in expect:
        folder = Path(os.environ["HOME"]) / expect["profile_folder"].removeprefix("~/")
        assert [Path(s["path"]).parent for s in used] == [folder]
    for key in ("client_id", "secret_set", "scopes"):
        if key in expect:
            assert credential[key] == expect[key]
    if expect.get("has_no_secret_field"):
        assert "secret" not in credential and "secret" not in report
    if "skipped_source" in expect:
        [skipped] = [s for s in report["sources"] if s["kind"] == expect["skipped_source"]]
        assert skipped["skipped"] == "partial" and not skipped["used"]
        assert skipped["missing"] == expect["missing"]
    if "problem" in expect:
        assert expect["problem"] in problems
    if "error" in expect:
        assert expect["error"] in problems and not report["ok"]
    if "notice" in expect:
        assert expect["notice"] in notices
        assert _named(notices[expect["notice"]], expect.get("missing", []))
    if "notice_one_of" in expect:
        assert set(expect["notice_one_of"]) & set(notices)
    if "debug" in expect:
        assert expect["debug"] in err
    warnings = [w["code"] for w in report["warnings"]]
    if "warning" in expect:
        assert expect["warning"] in warnings
    if "warning_count" in expect:
        assert len(warnings) == expect["warning_count"]
    _code, text, text_err = _run(capsys, ["config", *built.options])
    if "warning_count" in expect:
        assert text.count("Warning (") == expect["warning_count"]
    return out + err + text + text_err


class Answer:
    def __init__(self, status: int, body) -> None:
        self.status_code = status
        self.headers: dict = {}
        self._body = body

    def json(self):
        return self._body


class StubSession:
    """Adobe's sign-in service and Analytics, as the case describes their answers."""

    case: ClassVar[dict] = {}

    def mount(self, *args, **kwargs) -> None:
        pass

    def post(self, url, data=None, timeout=None):
        answer = self.case["given"]["sign_in_answer"]
        if answer == "connection_error":
            raise requests.ConnectionError("no network")
        return Answer(answer["status"], answer["body"])

    def get(self, url, headers=None, timeout=None):
        answer = self.case["given"].get("product_answer")
        if answer == "connection_error":
            raise requests.ConnectionError("no network")
        orgs = []
        if answer == "has_access":
            company = {"globalCompanyId": "testco", "companyName": "Test"}
            orgs = [{"imsOrgId": FIXTURES["A"]["org_id"], "companies": [company]}]
        return Answer(200, {"imsOrgs": orgs})

    def request(self, method, url, **kwargs):
        return Answer(200, {"content": [{"rsid": "r", "name": "R"}], "lastPage": True})


def check_live(case: dict, capsys, monkeypatch) -> str:
    """A case for the live test. Gives everything that the command printed."""
    expect = case["expect"]
    StubSession.case = case
    monkeypatch.setattr("ahd.api.client.requests.Session", StubSession)
    monkeypatch.setattr("ahd.api.auth._sleep", lambda seconds: None)
    code, out, err = _run(capsys, ["check", "--format", "json"])
    report = json.loads(out)
    valid(report, _schema("live-test-v1"))
    assert report["product"] == PRODUCT and report["source"]["kind"] == "environment"
    assert report["ok"] is bool(report["sign_in"]["ok"] and report["product_access"]["ok"])
    assert (code == 0) is report["ok"]
    for step in ("sign_in", "product_access"):
        want = expect.get(step, {})
        got = report[step]
        message = got["message"] or ""
        if "message_contains" in want:
            assert want["message_contains"] in message
        if "message_excludes" in want:
            assert want["message_excludes"] not in message
        if "message_contains_by_product" in want:
            assert want["message_contains_by_product"][PRODUCT] in message
        plain = {k: v for k, v in want.items() if not k.startswith("message_")}
        assert {k: got[k] for k in plain} == plain
        if got["ok"] is False:
            assert got["code"] and got["message"]
    _code, text, text_err = _run(capsys, ["check"])
    return out + err + text + text_err


def check(case: dict, tmp_path: Path, monkeypatch, capsys) -> None:
    """Run one case. An AssertionError means that ahd does not meet it."""
    given, expect = case["given"], case["expect"]
    if "profile_name" in given and "action" not in given:
        try:
            profile_dir(given["profile_name"])
            found = True
        except ConfigError:
            found = False
        assert found is expect["valid"]
        return
    if "client_id" in given:
        assert mask_client_id(given["client_id"]) == expect["masked"]
        return
    command = given.get("command")
    built = build(case, tmp_path / "case", monkeypatch)
    if "secret" in given:
        _code, out, _err = _run(capsys, ["config"])
        assert f"Secret: {expect['masked']}\n" in out and given["secret"] not in out
        return
    if command is None:
        check_lookup(case, built)
        return
    if command == "config_check":
        shown = check_config(case, built, capsys)
    else:
        shown = check_live(case, capsys, monkeypatch)
    if expect.get("output_excludes_secret"):
        other = build(case, tmp_path / "other", monkeypatch, OTHER_SECRET)
        if command == "config_check":
            baseline = check_config({**case, "expect": {}}, other, capsys)
        else:
            baseline = check_live({**case, "expect": {}}, capsys, monkeypatch)
        assert _leaks(FIXTURES["A"]["secret"], shown, baseline) == []


@pytest.mark.parametrize("case", [c for c in ALL if _applies(c)], ids=lambda c: c["id"])
def test_shared_case(case, tmp_path, monkeypatch, capsys):
    if case["rule"] == "L1.3" and os.name == "nt":
        pytest.skip("The mode of a file does not say who can read it on Windows.")
    if case["id"] not in EXPECTED_TO_FAIL:
        check(case, tmp_path, monkeypatch, capsys)
        return
    try:
        check(case, tmp_path, monkeypatch, capsys)
    except AssertionError:
        return
    pytest.fail(f"{case['id']} now passes. Remove it from known-differences.json.", pytrace=False)


def test_known_differences_has_the_shared_shape_and_names_real_cases():
    valid(KNOWN, _schema("known-differences-v1"))
    assert KNOWN["tool"] == TOOL
    by_id = {c["id"]: c for c in ALL}
    for difference in KNOWN["differences"]:
        for case in difference.get("cases", []):
            assert case in by_id, f"known-differences.json names {case}, which is no case"
            assert by_id[case]["rule"] == difference["rule"]
            assert _applies(by_id[case]), f"{case} does not apply to ahd"


def test_every_case_that_is_skipped_has_a_rule_that_is_listed_as_not_applicable():
    listed = {entry["rule"] for entry in KNOWN["not_applicable"]}
    skipped = [
        c
        for c in ALL
        if not _applies(c)
        and PRODUCT in c.get("products", [PRODUCT])
        and TOOL in c.get("tools", [TOOL])
    ]
    assert skipped and {c["rule"] for c in skipped} <= listed


def test_the_copy_of_the_cases_is_whole():
    """Each file has the checksum that sync_cases.py wrote, so no case was edited here."""
    import hashlib

    source = json.loads((CASES / "SOURCE.json").read_text(encoding="utf-8"))
    for name, digest in source["files"].items():
        assert hashlib.sha256((CASES / name).read_bytes()).hexdigest() == digest, name
