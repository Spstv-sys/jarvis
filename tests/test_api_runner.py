"""Do not initialize models when another server already owns the port."""
import socket
from unittest.mock import MagicMock

import pytest

from webapp import runner


def test_occupied_port_does_not_start_lifespan(monkeypatch):
    server = MagicMock()
    monkeypatch.setattr(runner.uvicorn, "Server", server)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        with pytest.raises(OSError):
            runner.run_api(object(), "127.0.0.1", occupied.getsockname()[1])
    server.assert_not_called()


def test_bound_listener_is_passed_to_uvicorn_and_closed(monkeypatch):
    listeners = []

    def serve(*, sockets):
        listeners.extend(sockets)
        assert sockets[0].getsockname()[1] > 0

    server = MagicMock()
    server.return_value.run.side_effect = serve
    monkeypatch.setattr(runner.uvicorn, "Server", server)
    runner.run_api(object(), "127.0.0.1", 0)
    assert len(listeners) == 1 and listeners[0].fileno() == -1
