"""Shared fixtures. Every test runs without network access and without the user's settings."""

from __future__ import annotations

import os
import socket

import pytest

# Each file that a test makes is private, as a credential file should be. A test of the warning
# for a file that others can read sets the mode of its file itself.
os.umask(0o077)


class NetworkBlocked(AssertionError):
    """A test tried to open a network connection."""


def _blocked(*args, **kwargs):
    raise NetworkBlocked("Tests must not use the network. Use the fakes in tests/fakes.py.")


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    """Fail any test that resolves a host name or opens a non-local socket connection."""
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def connect(self, address):
        if self.family == getattr(socket, "AF_UNIX", None):
            return real_connect(self, address)
        _blocked()

    def connect_ex(self, address):
        if self.family == getattr(socket, "AF_UNIX", None):
            return real_connect_ex(self, address)
        _blocked()

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


# The variables of the credential without the AHD_ in front.
PLAIN_VARIABLES = ("ORG_ID", "CLIENT_ID", "SECRET", "SCOPES")


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path_factory):
    """No AA_PROFILE, no AHD_* variable, no ORG_ID, CLIENT_ID, SECRET or SCOPES, a HOME with no
    ~/.aa profiles in it, and an empty working folder.

    The AHD_* variables hold the credential and the defaults of the anomaly options, e.g.,
    AHD_ANOMALY_METHOD. AHD_CHROME stays, because it only chooses the Chrome of the page tests.
    The tool reads config.json and .env from the working folder, so each test starts in an empty
    one. A file of that name in the folder of the repository then never reaches a test.
    """
    for key in list(os.environ):
        if (
            key == "AA_PROFILE"
            or key in PLAIN_VARIABLES
            or (key.startswith("AHD_") and key != "AHD_CHROME")
        ):
            monkeypatch.delenv(key)
    monkeypatch.setenv("HOME", str(tmp_path_factory.mktemp("home")))
    monkeypatch.chdir(tmp_path_factory.mktemp("folder"))
