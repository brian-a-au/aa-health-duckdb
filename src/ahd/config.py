"""Credential loading. Values are never logged, shown in errors, or included in repr.

Credentials come from the first of these that is set:

1. a profile (--profile or AA_PROFILE): ~/.aa/orgs/<name>/config.json, the file aa_auto_sdr uses
2. the AHD_* environment variables, when all four are set
3. a config file (--config, else ./config.json); an AHD_* variable wins over the same key
4. the variables ORG_ID, CLIENT_ID, SECRET and SCOPES, when all four are set
5. a .env file in the working folder that holds those four names

--config together with all four AHD_* variables is an error, because one of them would be
ignored. A ./config.json that the tool finds by itself gives way to the variables.

Sources 4 and 5 are read only when sources 1 to 3 give nothing at all, so that a setup that
worked before them gets the same credential. The Adobe credentials standard, version 1, has
another order, which the tool follows from 0.2.0. Until then, find() adds a notice for each
case where that order would give another result.
"""

from __future__ import annotations

import json
import logging
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_KEYS = ("org_id", "client_id", "secret", "scopes")
# Keys of a file that the tool knows. An Analytics tool accepts sandbox and does not use it.
_KNOWN_KEYS = (*_KEYS, "sandbox")
PROFILE_ENV = "AA_PROFILE"
# Letters, digits, dots, dashes and underscores, 64 characters or fewer.
_PROFILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
PLAIN_VARIABLES = "ORG_ID, CLIENT_ID, SECRET and SCOPES"
AHD_VARIABLES = "AHD_ORG_ID, AHD_CLIENT_ID, AHD_SECRET and AHD_SCOPES"
DOTENV = ".env"
# The release that follows the lookup order of the standard.
STRICT_RELEASE = "0.2.0"
MASK = "****"


NO_CREDENTIAL = (
    "No credential was found. Use --profile NAME, set the AHD_ORG_ID, AHD_CLIENT_ID, AHD_SECRET "
    "and AHD_SCOPES variables or the ORG_ID, CLIENT_ID, SECRET and SCOPES variables, or put a "
    "config.json file or a .env file in this folder."
)

CONFIG_AND_VARIABLES = (
    "Use --config or the four variables AHD_ORG_ID, AHD_CLIENT_ID, AHD_SECRET and AHD_SCOPES, "
    "not both. To use the file, unset the variables."
)

PROFILE_AND_CONFIG = "Use a profile or --config, not both. The profile {name!r} comes from {where}."


class ConfigError(Exception):
    """Raised when configuration is missing or unreadable.

    code is the name of the problem in the Adobe credentials standard, e.g., no_credential.
    missing holds the credential keys that a source lacks.
    """

    def __init__(self, message: str, code: str = "no_credential", missing: tuple[str, ...] = ()):
        super().__init__(message)
        self.code = code
        self.missing = missing


def mask_client_id(client_id: str) -> str:
    """The client ID as the tool shows it: its first 4 and last 4 characters around four
    stars, e.g., 1a2b****9y0z. A client ID of 8 characters or fewer shows as four stars only.
    Every place that shows a client ID uses this function."""
    if len(client_id) <= 8:
        return MASK
    return f"{client_id[:4]}{MASK}{client_id[-4:]}"


@dataclass(frozen=True)
class Credentials:
    org_id: str
    client_id: str
    secret: str
    scopes: str
    # A plain name for where the values came from, e.g. "profile prod". Never a value.
    source: str = field(default="config.json", compare=False)
    # The kind of the source in the Adobe credentials standard, e.g., flag_profile.
    kind: str = field(default="default_config", compare=False)

    def __repr__(self) -> str:
        return f"Credentials(org_id={self.org_id!r}, client_id={mask_client_id(self.client_id)!r})"


@dataclass(frozen=True)
class Source:
    """One place the lookup read that held a credential value. It never holds a value."""

    kind: str
    name: str
    fields: tuple[str, ...]
    path: Path | None = None
    used: bool = False
    # Why the source was not used: "partial", "lower_priority" or None.
    skipped: str | None = None
    missing: tuple[str, ...] = ()


@dataclass(frozen=True)
class Message:
    """A notice or a warning: its code in the Adobe credentials standard and one line of text."""

    code: str
    text: str


@dataclass
class Lookup:
    """What find() found: the credential or the error, and how it got there."""

    credentials: Credentials | None = None
    error: ConfigError | None = None
    profile: str | None = None
    sources: list[Source] = field(default_factory=list)
    # A notice says that the tool will act differently from STRICT_RELEASE.
    notices: list[Message] = field(default_factory=list)
    warnings: list[Message] = field(default_factory=list)
    # The values that were found when a key is missing, for the config check. Never printed
    # whole: the secret shows as set or not set, and the client ID is masked.
    # They hold the secret, so they stay out of the text of repr().
    found: dict[str, str | None] = field(default_factory=dict, repr=False)

    def notice(self, code: str, text: str) -> None:
        self.notices.append(Message(code, text))


def profiles_base() -> Path:
    """The folder that holds the profiles, ~/.aa/orgs, as in aa_auto_sdr."""
    return Path(os.environ.get("HOME", "~")).expanduser() / ".aa" / "orgs"


def profile_dir(name: str) -> Path:
    """The folder of one profile. The name may not leave the profiles folder."""
    if not _PROFILE_NAME.fullmatch(name):
        raise ConfigError(
            f"Profile name {name!r} is not valid. Use letters, digits, dots, dashes and "
            "underscores, starting with a letter or digit, and 64 characters or fewer.",
            "invalid_profile_name",
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


def _variable(key: str) -> str:
    """The environment variable of a credential key, e.g., AHD_SECRET for secret."""
    return f"AHD_{key.upper()}"


def _missing(values: dict[str, str | None]) -> tuple[str, ...]:
    return tuple(k for k in _KEYS if not values.get(k))


def _build(
    values: dict[str, str | None],
    source: str,
    *,
    variables: bool = False,
    kind: str = "default_config",
) -> Credentials:
    """The credential of values. variables is true when they all came from the AHD_* variables.
    Then the message for a missing value names the variable, e.g., AHD_SECRET."""
    missing = _missing(values)
    if missing:
        names = [_variable(k) if variables else k for k in missing]
        raise ConfigError(
            f"Missing credential keys in {source}: " + ", ".join(names), "partial_source", missing
        )
    return Credentials(**{k: values[k] or "" for k in _KEYS}, source=source, kind=kind)


# A value in quotes, with or without a comment after the closing quote.
_QUOTED = re.compile(r"""(['"])(.*?)\1\s*(?:#.*)?""")
# A comment in a value without quotes: a "#" at its start or after a space.
_COMMENT = re.compile(r"(?:^|\s)#")
# One line of a .env file: NAME=value, with or without "export" in front.
_DOTENV_LINE = re.compile(r"(?:export\s+)?(\w+)\s*=(.*)")


def _unquoted(value: str) -> str:
    """The value of a .env line without its quotes and without a comment after it."""
    quoted = _QUOTED.fullmatch(value)
    if quoted:
        return quoted.group(2)
    return _COMMENT.split(value, maxsplit=1)[0]


def read_dotenv(path: Path) -> dict[str, str | None]:
    """The four credential values of a .env file. A missing or unreadable file gives none.

    Only the lines for ORG_ID, CLIENT_ID, SECRET and SCOPES are read, as NAME=value, with or
    without quotes and with or without "export" in front. Nothing goes into the environment,
    so the file cannot set any other variable, e.g., AA_PROFILE.
    """
    values: dict[str, str | None] = dict.fromkeys(_KEYS)
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except (OSError, ValueError):
        return values
    names = {k.upper(): k for k in _KEYS}
    for line in text.splitlines():
        match = _DOTENV_LINE.fullmatch(line.strip())
        key = names.get(match.group(1)) if match else None
        if match and key:
            values[key] = _text(_unquoted(match.group(2).strip()))
    return values


def others_can_read(path: Path) -> bool:
    """True when the group or other users can read the file. A link is followed, so the file
    it points to is the one that is checked. Always false on Windows, where the mode of a file
    does not say who can read it."""
    if os.name == "nt":
        return False
    try:
        mode = os.stat(path).st_mode
    except OSError:
        return False
    return bool(stat.S_IMODE(mode) & 0o044)


def _fields(data: dict[str, Any]) -> tuple[str, ...]:
    """The known keys that a file holds a value for."""
    return tuple(k for k in _KNOWN_KEYS if _text(data.get(k)))


def _note_unknown_keys(data: dict[str, Any], label: str) -> None:
    for key in data:
        if key not in _KNOWN_KEYS:
            # Only the name of the key, never its value.
            log.debug("%s: the key %r is not known and was ignored (unknown_key).", label, key)


def _variables_source(
    values: dict[str, str | None], name: str, *, used: bool = False, complete_skip: str | None
) -> list[Source]:
    """The source entry of one set of variables, or nothing when none of them is set. A set
    that is not used and lacks a key is partial. complete_skip says why a full set is not used."""
    fields = tuple(k for k in _KEYS if values[k])
    if not fields:
        return []
    missing = _missing(values)
    skipped = None if used else ("partial" if missing else complete_skip)
    return [
        Source(
            "environment",
            name,
            fields,
            used=used,
            skipped=skipped,
            missing=missing if skipped == "partial" else (),
        )
    ]


def _differ(one: dict[str, str | None], other: dict[str, str | None]) -> list[str]:
    """The keys that both hold with different values."""
    return [k for k in _KEYS if one[k] and other[k] and one[k] != other[k]]


def _names(keys: tuple[str, ...] | list[str], prefix: str = "") -> str:
    """The variables of the keys, e.g., "AHD_SECRET, AHD_SCOPES" with the prefix AHD_."""
    return ", ".join(f"{prefix}{k.upper()}" for k in keys)


class _Finder:
    """One lookup. Each method handles one case and fills self.look."""

    def __init__(self) -> None:
        self.look = Lookup()
        self.ahd = {k: _text(os.environ.get(_variable(k))) for k in _KEYS}
        self.plain = {k: _text(os.environ.get(k.upper())) for k in _KEYS}
        # The files that were read and hold a credential value.
        self.files: list[Path] = []

    def _add(self, sources: list[Source]) -> None:
        self.look.sources.extend(sources)

    def _ahd_source(self, *, used: bool = False) -> list[Source]:
        return _variables_source(
            self.ahd, "AHD_ environment variables", used=used, complete_skip="lower_priority"
        )

    def _plain_source(self, *, used: bool = False) -> list[Source]:
        return _variables_source(
            self.plain,
            f"environment variables {PLAIN_VARIABLES}",
            used=used,
            complete_skip="lower_priority",
        )

    def _forms_differ(self) -> bool:
        """Add the notice for AHD_ and plain variables with different values."""
        keys = _differ(self.ahd, self.plain)
        if keys:
            self.look.notice(
                "variable_forms_differ",
                f"{_names(keys, 'AHD_')} and {_names(keys)} are both set, with different "
                f"values. This release uses the AHD_ names. From {STRICT_RELEASE}, ahd stops "
                "when the two differ.",
            )
        return bool(keys)

    def _plain_not_used(self, used: str) -> None:
        """Add the notice for plain variables that are set while another source is used."""
        if not any(self.plain.values()):
            return
        missing = _missing(self.plain)
        if missing:
            self.look.notice(
                "partial_source_skipped",
                f"Some of the variables {PLAIN_VARIABLES} are set, and {_names(missing)} "
                f"{'is' if len(missing) == 1 else 'are'} not. This release does not read them "
                f"and uses {used}. From {STRICT_RELEASE}, ahd stops when only some are set.",
            )
        else:
            self.look.notice(
                "plain_variables_not_used",
                f"The variables {PLAIN_VARIABLES} are set. This release does not read them "
                f"and uses {used}. From {STRICT_RELEASE}, ahd uses them before a config.json "
                "file or a .env file.",
            )

    def _dotenv(self) -> tuple[dict[str, str | None], Path]:
        path = Path(DOTENV)
        values = read_dotenv(path) if path.is_file() else dict.fromkeys(_KEYS)
        if any(values.values()):
            self.files.append(path)
        return values, path

    def _dotenv_source(
        self, values: dict[str, str | None], path: Path, *, used: bool = False
    ) -> list[Source]:
        fields = tuple(k for k in _KEYS if values[k])
        if not fields:
            return []
        missing = _missing(values)
        skipped = None if used else ("partial" if missing else "lower_priority")
        return [
            Source(
                "dotenv",
                DOTENV,
                fields,
                path.absolute(),
                used,
                skipped,
                missing if skipped == "partial" else (),
            )
        ]

    def profile(self, name: str, kind: str) -> None:
        self.look.profile = name
        path = profile_dir(name) / "config.json"
        if not path.exists():
            raise ConfigError(
                f"Profile {name!r} was not found. Expected the file {path}. "
                "Create it with aa_auto_sdr --profile-add, or check the name.",
                "profile_not_found",
            )
        label = f"Profile {name!r}"
        data = read_config_object(path, label)
        _note_unknown_keys(data, label)
        values = {k: _text(data.get(k)) for k in _KEYS}
        self.look.found = values
        missing = _missing(values)
        if _fields(data):
            self.files.append(path)
            self._add(
                [
                    Source(
                        kind,
                        f"profile {name}",
                        _fields(data),
                        path,
                        not missing,
                        "partial" if missing else None,
                        missing,
                    )
                ]
            )
        self._add(self._ahd_source() + self._plain_source())
        self.look.credentials = _build(values, f"profile {name}", kind=kind)

    def all_variables(self, path: Path | None, from_option: bool) -> None:
        """All four AHD_* variables are set."""
        if path is not None and from_option:
            self._add(self._ahd_source() + self._plain_source())
            raise ConfigError(CONFIG_AND_VARIABLES)
        self._add(self._ahd_source(used=True) + self._plain_source())
        self.look.credentials = _build(self.ahd, "environment variables", kind="environment")
        self._forms_differ()

    def no_file(self) -> None:
        """No profile, not all four AHD_* variables, and no config file."""
        if any(self.ahd.values()):
            self.look.found = self.ahd
            self._add(self._ahd_source() + self._plain_source())
            _build(self.ahd, "environment variables", variables=True)
        # Before the plain variables and the .env file were read, this case had no credential.
        if not _missing(self.plain):
            self._add(self._plain_source(used=True))
            self.look.credentials = _build(
                self.plain, f"environment variables {PLAIN_VARIABLES}", kind="environment"
            )
            return
        values, path = self._dotenv()
        if not _missing(values):
            self._add(self._plain_source() + self._dotenv_source(values, path, used=True))
            self.look.credentials = _build(values, DOTENV, kind="dotenv")
            self._plain_not_used("the .env file")
            return
        self._add(self._plain_source() + self._dotenv_source(values, path))
        partial = [
            f"{what} {_names(_missing(found))}"
            for what, found in (
                (f"Of the variables {PLAIN_VARIABLES}, these are not set:", self.plain),
                ("The .env file in this folder lacks", values),
            )
            if any(found.values())
        ]
        raise ConfigError(" ".join([NO_CREDENTIAL, *(f"{p}." for p in partial)]))

    def file(self, path: Path, from_option: bool) -> None:
        """A config file, with any AHD_* variable in place of the same key."""
        kind = "flag_config" if from_option else "default_config"
        data = read_config_object(Path(path), "Config file")
        _note_unknown_keys(data, "Config file")
        own = {k: _text(data.get(k)) for k in _KEYS}
        values = {k: self.ahd[k] or own[k] for k in _KEYS}
        self.look.found = values
        name = Path(path).name
        mixed = any(self.ahd.values())
        source = f"{name} and environment variables" if mixed else name
        missing = _missing(values)
        entries = _variables_source(
            self.ahd, "AHD_ environment variables", used=not missing, complete_skip=None
        )
        if _fields(data):
            self.files.append(Path(path))
            entries.append(
                Source(
                    kind,
                    name,
                    _fields(data),
                    Path(path).absolute(),
                    not missing and any(own[k] and not self.ahd[k] for k in _KEYS),
                    "partial" if missing else None,
                    missing,
                )
            )
        self._add(entries + self._plain_source())
        self.look.credentials = _build(values, source, kind=kind)
        if mixed:
            self.look.notice(
                "mixed_sources",
                f"The credential comes from two sources: {_names(_fields(self.ahd), 'AHD_')} "
                f"from the variables and the other keys from {name}. From {STRICT_RELEASE}, "
                "ahd takes all four from one source and stops when a source has only some.",
            )
        if from_option:
            # --config stays above the variables and the .env file in the strict order.
            return
        if not self._forms_differ():
            self._plain_not_used(source)
        dotenv, dotenv_path = self._dotenv()
        self._add(self._dotenv_source(dotenv, dotenv_path))
        if not any(dotenv.values()):
            return
        lacks = _missing(dotenv)
        if lacks:
            self.look.notice(
                "partial_source_skipped",
                f"The .env file in this folder holds some of {PLAIN_VARIABLES}, and "
                f"{_names(lacks)} {'is' if len(lacks) == 1 else 'are'} not in it. This release "
                f"does not read it and uses {source}. From {STRICT_RELEASE}, ahd stops when "
                "the file holds only some.",
            )
        elif dotenv != own:
            self.look.notice(
                "dotenv_and_config_differ",
                f"The .env file and {name} in this folder hold different credentials. This "
                f"release uses {source}. From {STRICT_RELEASE}, ahd uses the .env file first.",
            )


def _look_up(
    path: Path | None, profile: str | None, from_option: bool, profile_kind: str
) -> Lookup:
    finder = _Finder()
    look = finder.look
    try:
        if profile:
            finder.profile(profile, profile_kind)
        elif not _missing(finder.ahd):
            finder.all_variables(path, from_option)
        elif path is None:
            finder.no_file()
        else:
            finder.file(path, from_option)
    except ConfigError as e:
        look.error = e
        look.credentials = None
        look.notices.clear()
    open_files = [str(p) for p in finder.files if others_can_read(p)]
    if open_files:
        look.warnings.append(
            Message(
                "file_readable_by_others",
                f"Other users of this computer can read {' and '.join(open_files)}. "
                f"Make the file private, e.g., with chmod 600 {open_files[0]}.",
            )
        )
    return look


def find(config: Path | None, profile_option: str | None = None) -> Lookup:
    """Look for the credential as a command does. It never raises for a credential problem:
    the result holds the credential or the error.

    config is the path of --config, and profile_option the name of --profile. Without
    --config, the file ./config.json is used when it exists.
    """
    name = chosen_profile(profile_option)
    if config is not None and name:
        where = "--profile" if profile_option else "the AA_PROFILE variable"
        return Lookup(
            profile=name,
            error=ConfigError(
                PROFILE_AND_CONFIG.format(name=name, where=where), "profile_and_config_both_given"
            ),
        )
    path = config
    if path is None and Path("config.json").exists():
        path = Path("config.json")
    kind = "flag_profile" if profile_option else "profile_variable"
    return _look_up(path, name, config is not None, kind)


def load_credentials(
    path: Path | None, profile: str | None = None, *, from_option: bool = False
) -> Credentials:
    """The credential of the profile, else of the AHD_* variables, else of the file at path,
    else of the plain variables, else of the .env file in the working folder.

    from_option is true when path came from --config. Then the file and all four variables
    together are an error, so that neither is ignored without a word.
    """
    look = _look_up(path, profile, from_option, "flag_profile")
    if look.error is not None:
        raise look.error
    assert look.credentials is not None
    return look.credentials


def check_report(look: Lookup, tool: str, version: str) -> dict[str, Any]:
    """The config check as data, in the shape adobe-credentials/v1/config-check. It holds no
    secret: the secret is true or false, and the client ID is masked."""
    creds = look.credentials
    found = (
        {k: getattr(creds, k) for k in _KEYS} if creds else {**dict.fromkeys(_KEYS), **look.found}
    )
    client_id = found["client_id"]
    problems = []
    if look.error is not None:
        problem: dict[str, Any] = {"code": look.error.code, "message": str(look.error)}
        if look.error.missing:
            problem["missing"] = list(look.error.missing)
        problems.append(problem)
    return {
        "schema": "adobe-credentials/v1/config-check",
        "tool": tool,
        "tool_version": version,
        "product": "aa",
        "ok": creds is not None,
        "profile": look.profile,
        "sources": [
            {
                "kind": s.kind,
                "name": s.name,
                "path": str(s.path) if s.path else None,
                "used": s.used,
                "fields": list(s.fields),
                "skipped": s.skipped,
                "missing": list(s.missing),
            }
            for s in look.sources
        ],
        "credential": {
            "org_id": found["org_id"],
            "client_id": mask_client_id(client_id) if client_id else None,
            "secret_set": bool(found["secret"]),
            "scopes": found["scopes"],
        },
        "problems": problems,
        "notices": [{"code": m.code, "message": m.text} for m in look.notices],
        "warnings": [{"code": m.code, "message": m.text} for m in look.warnings],
    }
