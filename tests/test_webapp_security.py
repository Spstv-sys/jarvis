"""API regressions: authorization must guard side effects and private state."""
import base64
import queue
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from webapp import security, state
from webapp.factory import create_app
from webapp.ws_manager import ConnectionManager


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(security, "JARVIS_API_KEY", "test-access-key")
    monkeypatch.setattr(security, "RATE_LIMIT", 100)
    security._rate_counters.clear()
    jarvis = SimpleNamespace(input_queue=queue.Queue(), llm=SimpleNamespace(active_provider="fake"))
    monkeypatch.setattr(state, "jarvis", jarvis)
    monkeypatch.setattr(state, "startup_error", None)
    monkeypatch.setattr(state, "manager", ConnectionManager())
    state.jarvis_ready.set()
    # Deliberately do not enter lifespan: no models, microphone, or network.
    client = TestClient(create_app(), raise_server_exceptions=False)
    yield client, jarvis
    client.close()
    state.jarvis_ready.clear()
    security._rate_counters.clear()


@pytest.mark.parametrize("method,path", [
    ("post", "/anticipator/known-id/accept"),
    ("post", "/anticipator/known-id/dismiss"),
    ("post", "/anticipator/scan"),
    ("post", "/reflection/run"),
    ("get", "/cognition"),
    ("get", "/cognition/history"),
    ("get", "/goal_stack"),
    ("get", "/reflection"),
    ("get", "/anticipator"),
])
def test_private_routes_require_key(api, method, path):
    client, jarvis = api
    assert client.request(method, path).status_code == 401
    assert jarvis.input_queue.empty()


def test_authenticated_suggestion_can_enqueue_only_after_authorization(api, monkeypatch):
    from core import anticipator
    client, jarvis = api
    suggestion = SimpleNamespace(command="test-command", to_dict=lambda: {"id": "known-id"})
    accept = MagicMock(return_value=suggestion)
    monkeypatch.setattr(anticipator, "get_anticipator", lambda: SimpleNamespace(accept=accept))
    assert client.post("/anticipator/known-id/accept").status_code == 401
    accept.assert_not_called()
    response = client.post("/anticipator/known-id/accept",
                           headers={"X-API-Key": "test-access-key"})
    assert response.status_code == 200 and response.json()["executed"]
    assert jarvis.input_queue.get_nowait() == "test-command"


def test_failed_startup_is_not_ready_and_command_returns_503(api, monkeypatch):
    from webapp.lifecycle import start_jarvis
    client, _ = api
    monkeypatch.setattr(state, "jarvis", None)
    monkeypatch.setattr(state, "JarvisUltimate", MagicMock(side_effect=RuntimeError("init failed")))
    start_jarvis()
    assert not state.jarvis_ready.is_set()
    status = client.get("/status").json()
    assert status["ready"] is False and status["state"] == "error"
    assert "init failed" not in str(status)  # details stay in the server log
    response = client.post("/command", json={"text": "hello"},
                           headers={"X-API-Key": "test-access-key"})
    assert response.status_code == 503


def test_partial_startup_failure_does_not_run_processing_loop(api, monkeypatch):
    from webapp.lifecycle import start_jarvis
    partial = MagicMock()
    partial.start_ears.side_effect = RuntimeError("audio failed")
    monkeypatch.setattr(state, "JarvisUltimate", lambda: partial)
    start_jarvis()
    assert state.jarvis is None and not state.jarvis_ready.is_set()
    assert partial.is_running is False
    partial.start_processing_loop.assert_not_called()


def test_websocket_rejects_missing_key(api):
    client, jarvis = api
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws"):
            pass
    assert jarvis.input_queue.empty()


def test_websocket_subprotocol_auth_validation_and_rate_limit(api, monkeypatch):
    client, jarvis = api
    encoded = base64.urlsafe_b64encode(b"test-access-key").decode().rstrip("=")
    monkeypatch.setattr(security, "RATE_LIMIT", 1)
    with client.websocket_connect("/ws", subprotocols=["jarvis", f"jarvis-key.{encoded}"]) as ws:
        assert ws.accepted_subprotocol == "jarvis"
        ws.send_json({"type": "command", "text": []})
        assert ws.receive_json()["text"] == "Invalid command"
        ws.send_json({"type": "command", "text": "first"})
        assert jarvis.input_queue.get(timeout=2) == "first"
        ws.send_json({"type": "command", "text": "second"})
        assert ws.receive_json()["text"] == "Rate limit exceeded"
    assert jarvis.input_queue.empty()
    assert state.manager._clients == []


def test_websocket_disconnects_on_malformed_payload(api):
    client, _ = api
    with client.websocket_connect("/ws?token=test-access-key") as ws:
        ws.send_text("not-json")
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    assert state.manager._clients == []


def test_auth_check_does_not_enqueue_commands(api):
    client, jarvis = api
    assert client.post("/auth/check").status_code == 401
    assert client.post("/auth/check", headers={"X-API-Key": "test-access-key"}).json() == {"ok": True}
    assert jarvis.input_queue.empty()


def test_status_identifies_compatible_api(api):
    client, _ = api
    status = client.get("/status").json()
    assert status["api_version"] == 1
    assert status["auth_required"] is True


def test_cognition_reads_real_working_memory_entity_snapshot(api):
    from core.working_memory import Entity, WorkingMemory
    client, jarvis = api
    wm = WorkingMemory()
    wm.entities["київ"] = Entity(name="Київ", kind="location")
    jarvis.working_memory = wm
    response = client.get("/cognition", headers={"X-API-Key": "test-access-key"})
    data = response.json()
    assert "wm_error" not in data
    assert data["working_memory"]["entities"][0]["name"] == "Київ"
    snapshot = wm.entity_snapshot()
    snapshot[0]["name"] = "Changed copy"
    assert wm.entities["київ"].name == "Київ"
