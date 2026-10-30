"""Headless mode and persistent storage wiring, without real models/services."""
from unittest.mock import MagicMock

import config


def test_headless_audio_does_not_open_devices(monkeypatch):
    from core import audio_manager
    monkeypatch.setattr(config, "HEADLESS", True)
    pyaudio = MagicMock()
    monkeypatch.setattr(audio_manager, "pyaudio", pyaudio)
    monkeypatch.setattr(audio_manager.threading.Thread, "start", lambda self: None)
    audio = audio_manager.AudioManager(MagicMock())
    assert audio.mic_available is False
    pyaudio.PyAudio.assert_not_called()
    audio.speak_tts("No network synthesis in headless mode")


def test_core_database_uses_configured_persistent_path(tmp_path, monkeypatch):
    from core.memory import JarvisDatabase
    path = tmp_path / "persistent.db"
    monkeypatch.setattr(config, "CORE_DB_PATH", str(path))
    db = JarvisDatabase()
    db.set_setting("restart-check", "saved")
    db.close()
    reopened = JarvisDatabase()
    assert reopened.get_setting("restart-check") == "saved"
    reopened.close()


def test_remote_chroma_uses_http_client(monkeypatch):
    from core import memory_rag
    monkeypatch.setenv("CHROMA_HOST", "chroma-test")
    monkeypatch.setenv("CHROMA_PORT", "8123")
    http_client = MagicMock()
    vector = MagicMock()
    monkeypatch.setattr(memory_rag.chromadb, "HttpClient", http_client)
    monkeypatch.setattr(memory_rag, "Chroma", vector)
    prepare = MagicMock(return_value=("model_collection", 768, 5))
    monkeypatch.setattr(memory_rag, "prepare_collection", prepare)
    memory = object.__new__(memory_rag.JarvisSmartMemory)
    memory.embedding_model = "fake-model"
    memory.db_path = "unused-local-storage"
    memory._ready = MagicMock()
    memory._init_embeddings()
    http_client.assert_called_once_with(host="chroma-test", port=8123)
    assert vector.call_args.kwargs["client"] is http_client.return_value
    assert "persist_directory" not in vector.call_args.kwargs
    assert vector.call_args.kwargs["collection_name"] == "model_collection"
    assert prepare.call_args.args[0] is http_client.return_value
