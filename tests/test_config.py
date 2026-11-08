import json

import pytest

from ahd.config import (
    ConfigError,
    Credentials,
    chosen_profile,
    load_credentials,
    profile_dir,
    read_config_object,
)

KEYS = {
    "org_id": "ORG@AdobeOrg",
    "client_id": "abcd1234efgh",
    "secret": "s3cr3t",
    "scopes": "openid",
}


def _write(tmp_path, data):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data))
    return p


def test_loads_file(tmp_path):
    c = load_credentials(_write(tmp_path, KEYS))
    assert (c.org_id, c.client_id, c.secret, c.scopes) == tuple(KEYS.values())


def test_env_overrides_file(tmp_path, monkeypatch):
    monkeypatch.setenv("AHD_SECRET", "from-env")
    assert load_credentials(_write(tmp_path, KEYS)).secret == "from-env"


def test_env_only_without_file(monkeypatch):
    for k, v in KEYS.items():
        monkeypatch.setenv(f"AHD_{k.upper()}", v)
    assert load_credentials(None).client_id == "abcd1234efgh"


def test_missing_keys_named(tmp_path):
    data = {k: v for k, v in KEYS.items() if k != "secret"}
    with pytest.raises(ConfigError, match="secret") as e:
        load_credentials(_write(tmp_path, data))
    assert "abcd1234efgh" not in str(e.value)


def test_repr_hides_secret():
    c = Credentials("ORG@AdobeOrg", "abcd1234efgh", "s3cr3t-value", "openid")
    assert "s3cr3t-value" not in repr(c) and "1234efgh" not in repr(c)
    assert "ORG@AdobeOrg" in repr(c) and "abcd" in repr(c)


def _profile(name, data, raw=None):
    folder = profile_dir(name)
    folder.mkdir(parents=True)
    path = folder / "config.json"
    if raw is not None:
        path.write_bytes(raw)
    else:
        path.write_text(json.dumps(data))
    return path


def test_profile_is_read_from_the_aa_folder(tmp_path):
    path = _profile("prod", KEYS)
    assert path.parent.parent.parent.name == ".aa"
    c = load_credentials(None, "prod")
    assert c.client_id == "abcd1234efgh" and c.source == "profile prod"


def test_profile_wins_over_env_and_file(tmp_path, monkeypatch):
    _profile("prod", KEYS)
    for k in KEYS:
        monkeypatch.setenv(f"AHD_{k.upper()}", "from-env")
    other = _write(tmp_path, {k: "from-file" for k in KEYS})
    assert load_credentials(other, "prod").secret == "s3cr3t"


def test_profile_accepts_byte_order_mark():
    _profile("bom", None, raw=b"\xef\xbb\xbf" + json.dumps(KEYS).encode())
    assert load_credentials(None, "bom").org_id == "ORG@AdobeOrg"


def test_profile_missing_names_the_path():
    with pytest.raises(ConfigError, match="Profile 'nope' was not found") as e:
        load_credentials(None, "nope")
    assert ".aa/orgs/nope/config.json" in str(e.value)


@pytest.mark.parametrize(
    ("raw", "match"),
    [(b"{not json", "not valid UTF-8 JSON"), (b"[1, 2]", "one JSON object, not a list")],
)
def test_profile_bad_file(raw, match):
    _profile("bad", None, raw=raw)
    with pytest.raises(ConfigError, match=match):
        load_credentials(None, "bad")


def test_profile_not_utf8():
    _profile("latin", None, raw=b'{"org_id": "\xff"}')
    with pytest.raises(ConfigError, match="not valid UTF-8 JSON"):
        load_credentials(None, "latin")


def test_profile_missing_key_names_source():
    _profile("half", {k: v for k, v in KEYS.items() if k != "scopes"})
    with pytest.raises(ConfigError, match="profile half: scopes"):
        load_credentials(None, "half")


def test_profile_null_value_is_missing():
    _profile("nulls", {**KEYS, "secret": None})
    with pytest.raises(ConfigError, match="secret"):
        load_credentials(None, "nulls")


@pytest.mark.parametrize("name", ["../x", "a/b", ".hidden", ""])
def test_profile_name_cannot_leave_the_folder(name):
    with pytest.raises(ConfigError, match="not valid"):
        profile_dir(name)


def test_chosen_profile_flag_then_env(monkeypatch):
    assert chosen_profile(None) is None
    monkeypatch.setenv("AA_PROFILE", "fromenv")
    assert chosen_profile(None) == "fromenv"
    assert chosen_profile("flag") == "flag"


def test_sources(tmp_path, monkeypatch):
    path = _write(tmp_path, KEYS)
    assert load_credentials(path).source == "config.json"
    monkeypatch.setenv("AHD_SECRET", "x")
    assert load_credentials(path).source == "config.json and environment variables"
    for k, v in KEYS.items():
        monkeypatch.setenv(f"AHD_{k.upper()}", v)
    assert load_credentials(path).source == "environment variables"


def test_no_file_and_no_env():
    with pytest.raises(ConfigError, match="environment variables: org_id"):
        load_credentials(None)


def test_file_not_found(tmp_path):
    with pytest.raises(ConfigError, match="was not found"):
        load_credentials(tmp_path / "nope.json")


def test_unreadable_file(tmp_path):
    with pytest.raises(ConfigError, match="could not read"):
        read_config_object(tmp_path, "Config file")  # a folder, not a file
