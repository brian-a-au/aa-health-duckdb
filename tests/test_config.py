import json

import pytest

from ahd.config import ConfigError, Credentials, load_credentials

KEYS = {"org_id": "ORG@AdobeOrg", "client_id": "abcd1234efgh", "secret": "s3cr3t", "scopes": "openid"}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ("AHD_ORG_ID", "AHD_CLIENT_ID", "AHD_SECRET", "AHD_SCOPES"):
        monkeypatch.delenv(k, raising=False)


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
