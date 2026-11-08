"""Credential loading. Values are never logged, shown in errors, or included in repr.

Credentials come from the first of these that is set:

1. a profile (--profile or AA_PROFILE): ~/.aa/orgs/<name>/config.json, the file aa_auto_sdr uses
2. the AHD_* environment variables, when all four are set
3. a config file (--config, else ./config.json); an AHD_* variable wins over the same key
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

_KEYS = ("org_id", "client_id", "secret", "scopes")
PROFILE_ENV = "AA_PROFILE"
_PROFILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ConfigError(Exception):
    """Raised when configuration is missing or unreadable."""


@dataclass(frozen=True)
class Credentials:
    org_id: str
    client_id: str
    secret: str
    scopes: str
    # A plain name for where the values came from, e.g. "profile prod". Never a value.
    source: str = field(default="config.json", compare=False)

    def __repr__(self) -> str:
        return f"Credentials(org_id={self.org_id!r}, client_id={self.client_id[:4]!r}...)"


def profiles_base() -> Path:
    """The folder that holds the profiles, ~/.aa/orgs, as in aa_auto_sdr."""
    return Path(os.environ.get("HOME", "~")).expanduser() / ".aa" / "orgs"


def profile_dir(name: str) -> Path:
    """The folder of one profile. The name may not leave the profiles folder."""
    if not _PROFILE_NAME.match(name):
        raise ConfigError(
            f"Profile name {name!r} is not valid. Use letters, digits, dots, dashes and "
            "underscores, starting with a letter or digit."
        )
    return profiles_base() / name


def chosen_profile(flag: str | None) -> str | None:
    """The profile from the flag, else from AA_PROFILE, else None."""
    return flag or os.environ.get(PROFILE_ENV) or None


def read_config_object(path: Path, label: str) -> dict:
    """Read a JSON file that must hold one object. Accepts a UTF-8 byte-order mark."""
    try:
        # utf-8-sig: Windows PowerShell 5 writes UTF-8 with a byte-order mark.
        with Path(path).open(encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        raise ConfigError(f"{label}: the file {path} was not found.") from None
    except OSError as e:
        raise ConfigError(
            f"{label}: could not read {path} ({e.strerror or type(e).__name__})."
        ) from None
    except ValueError:  # JSONDecodeError and UnicodeDecodeError
        raise ConfigError(f"{label}: {path} is not valid UTF-8 JSON.") from None
    if not isinstance(data, dict):
        raise ConfigError(
            f"{label}: {path} must hold one JSON object, not a {type(data).__name__}."
        )
    return data


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _build(values: dict[str, str | None], source: str) -> Credentials:
    missing = [k for k in _KEYS if not values.get(k)]
    if missing:
        raise ConfigError(f"Missing credential keys in {source}: " + ", ".join(missing))
    return Credentials(**{k: values[k] for k in _KEYS}, source=source)


def load_profile(name: str) -> Credentials:
    path = profile_dir(name) / "config.json"
    if not path.exists():
        raise ConfigError(
            f"Profile {name!r} was not found. Expected the file {path}. "
            "Create it with aa_auto_sdr --profile-add, or check the name."
        )
    data = read_config_object(path, f"Profile {name!r}")
    return _build({k: _text(data.get(k)) for k in _KEYS}, f"profile {name}")


def load_credentials(path: Path | None, profile: str | None = None) -> Credentials:
    if profile:
        return load_profile(profile)
    env = {k: _text(os.environ.get(f"AHD_{k.upper()}")) for k in _KEYS}
    if all(env.values()):
        return _build(env, "environment variables")
    if path is None:
        return _build(env, "environment variables")
    data = read_config_object(Path(path), "Config file")
    values = {k: env[k] or _text(data.get(k)) for k in _KEYS}
    name = Path(path).name
    source = f"{name} and environment variables" if any(env.values()) else name
    return _build(values, source)
