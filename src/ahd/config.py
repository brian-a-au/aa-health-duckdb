"""Credential loading. Values are never logged, shown in errors, or included in repr."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

_KEYS = ("org_id", "client_id", "secret", "scopes")


class ConfigError(Exception):
    """Raised when configuration is missing or unreadable."""


@dataclass(frozen=True)
class Credentials:
    org_id: str
    client_id: str
    secret: str
    scopes: str

    def __repr__(self) -> str:
        return f"Credentials(org_id={self.org_id!r}, client_id={self.client_id[:4]!r}...)"


def load_credentials(path: Path | None) -> Credentials:
    data: dict = {}
    if path is not None:
        try:
            data = json.loads(Path(path).read_text())
        except FileNotFoundError:
            raise ConfigError(f"Config file not found: {path}") from None
        except (OSError, ValueError):
            raise ConfigError(f"Config file could not be read as JSON: {path}") from None
        if not isinstance(data, dict):
            raise ConfigError(f"Config file must contain a JSON object: {path}")
    values = {}
    for key in _KEYS:
        value = os.environ.get(f"AHD_{key.upper()}") or data.get(key)
        if isinstance(value, str) and value:
            values[key] = value
    missing = [k for k in _KEYS if k not in values]
    if missing:
        raise ConfigError("Missing credential keys: " + ", ".join(missing))
    return Credentials(**values)
