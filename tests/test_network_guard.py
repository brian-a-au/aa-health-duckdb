import socket

import pytest
import requests
from conftest import NetworkBlocked


def test_http_request_is_blocked():
    with pytest.raises(NetworkBlocked):
        requests.get("https://analytics.adobe.io/discovery/me", timeout=1)


def test_raw_socket_connect_is_blocked():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(NetworkBlocked):
            s.connect(("127.0.0.1", 9))
        with pytest.raises(NetworkBlocked):
            s.connect_ex(("127.0.0.1", 9))
    finally:
        s.close()
    with pytest.raises(NetworkBlocked):
        socket.create_connection(("127.0.0.1", 9))
    with pytest.raises(NetworkBlocked):
        socket.getaddrinfo("adobe.io", 443)


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="no Unix sockets")
def test_local_unix_sockets_pass_through(tmp_path, monkeypatch):
    # Reaches the real connect: a missing socket file gives the normal OS error.
    # A short relative path, because Unix socket paths have a small length limit.
    monkeypatch.chdir(tmp_path)
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        with pytest.raises(FileNotFoundError):
            client.connect("missing")
        assert client.connect_ex("missing") != 0
    finally:
        client.close()
