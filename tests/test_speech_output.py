"""Speech output must use configuration and never duplicate streaming chunks."""
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import config


def test_stream_threshold_does_not_duplicate_text_for_speech():
    from core.llm_handler import LLMHandler
    handler = object.__new__(LLMHandler)
    spoken = []
    handler.audio = SimpleNamespace(speak=spoken.append)
    chunks = ["а" * 245 + " ", "маркер ", "завершення."]
    handler._get_active_provider = lambda: SimpleNamespace(stream=lambda _: iter(chunks))
    assert handler._stream_and_speak("test") == "".join(chunks)
    assert " ".join(spoken).count("маркер") == 1


def _audio(monkeypatch):
    from core import audio_manager
    monkeypatch.setattr(config, "HEADLESS", False)
    monkeypatch.setattr(audio_manager, "_PYGAME_AVAILABLE", True)
    pygame = MagicMock()
    pygame.mixer.music.get_busy.return_value = False
    monkeypatch.setattr(audio_manager, "pygame", pygame)
    audio = object.__new__(audio_manager.AudioManager)
    audio._speak_lock = threading.Lock()
    audio._is_speaking = threading.Event()
    audio.stop_event = threading.Event()
    audio._tts_cooldown_sec = 0
    return audio, pygame, audio_manager


def test_tts_uses_configured_voice_and_rate_and_cleans_audio(monkeypatch):
    audio, pygame, module = _audio(monkeypatch)
    monkeypatch.setattr(config, "VOICE_MODEL", "uk-UA-PolinaNeural")
    monkeypatch.setattr(config, "VOICE_RATE", "+10%")
    received = []

    class Communicate:
        def __init__(self, text, voice, rate):
            received.append((text, voice, rate))

        async def stream(self):
            yield {"type": "audio", "data": b"test-mp3"}

    monkeypatch.setattr(module.edge_tts, "Communicate", Communicate)
    audio.speak_tts("Перевірка голосу")
    assert received == [("Перевірка голосу", "uk-UA-PolinaNeural", "+10%")]
    audio_file = Path(pygame.mixer.music.load.call_args.args[0])
    assert not audio_file.exists()
    pygame.mixer.music.play.assert_called_once()
    assert not audio._is_speaking.is_set()


def test_tts_failure_cleans_up_and_does_not_log_service_tokens(monkeypatch, caplog):
    audio, pygame, module = _audio(monkeypatch)

    class ServiceError(Exception):
        status = 403

    class Communicate:
        def __init__(self, *args, **kwargs):
            pass

        async def stream(self):
            raise ServiceError("https://tts.example?token=do-not-log-this")
            yield  # make an async generator

    monkeypatch.setattr(module.edge_tts, "Communicate", Communicate)
    audio.speak_tts("Перевірка")
    assert "403" in caplog.text and "do-not-log-this" not in caplog.text
    pygame.mixer.music.load.assert_not_called()
    assert not audio._is_speaking.is_set()
