"""Shared fixtures. Every test runs without network access and without the user's settings."""

from __future__ import annotations

import socket

import pytest


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


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path_factory):
    """No credential variables, no AA_PROFILE, and a HOME with no ~/.aa profiles in it."""
    for key in ("AA_PROFILE", "AHD_ORG_ID", "AHD_CLIENT_ID", "AHD_SECRET", "AHD_SCOPES"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path_factory.mktemp("home")))
