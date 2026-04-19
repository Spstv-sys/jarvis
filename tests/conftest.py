"""
Фікстури для тестів Jarvis.
Всі зовнішні залежності (аудіо, БД, LLM тощо) замокано,
тому тести не потребують мікрофону, динаміків чи мережі.
"""
import sys
import queue
import threading
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


# ---------------------------------------------------------------------------
# Заглушки для модулів, недоступних у CI
# ---------------------------------------------------------------------------
def _stub_module(name: str) -> MagicMock:
    mod = MagicMock()
    sys.modules.setdefault(name, mod)
    return mod


for _mod in (
    "keyboard",
    "pyautogui",
    "pygame",
    "pygame.mixer",
    "openwakeword",
    "openwakeword.model",
    "speech_recognition",
    "pyaudio",
    "faster_whisper",
    "edge_tts",
    "numpy",
    "screen_brightness_control",
    "chromadb",
    "watchdog",
    "watchdog.observers",
    "watchdog.events",
    "duckduckgo_search",
    "langchain_community",
    "langchain_community.document_loaders",
    "langchain_text_splitters",
    "langchain_huggingface",
    "langchain_chroma",
    "openai",
    "google",
    "google.genai",
    "bs4",
    # provider sub-modules — stub so LLMHandler import doesn't fail
    "core.providers",
    "core.providers.base",
    "core.providers.deepseek",
    "core.providers.gemini",
    "core.providers.ollama",
):
    _stub_module(_mod)


@pytest.fixture
def jarvis_instance():
    """Return a JarvisUltimate instance with all external dependencies mocked."""

    mock_audio = MagicMock()
    mock_audio.speak = MagicMock()
    mock_audio.speak_tts = MagicMock()
    mock_audio.current_volume = 50

    mock_observer = MagicMock()
    mock_observer_instance = MagicMock()
    mock_observer.return_value = mock_observer_instance

    patches = [
        patch("main.JarvisDatabase",    return_value=MagicMock()),
        patch("main.JarvisSmartMemory", return_value=MagicMock()),
        patch("main.JarvisScraper",     return_value=MagicMock()),
        patch("main.JarvisYouTube",     return_value=MagicMock()),
        patch("main.AudioManager",      return_value=mock_audio),
        patch("main.LLMHandler",        return_value=MagicMock()),
        patch("main.SystemActions",     return_value=MagicMock()),
        patch("main.CommandHandlers",   return_value=MagicMock()),
        patch("main.keyboard.add_hotkey"),
        patch("main.Observer",          mock_observer),
        patch("main.DownloadHandler",   return_value=MagicMock()),
    ]

    started = []
    try:
        for p in patches:
            p.start()
            started.append(p)

        from main import JarvisUltimate
        jarvis = JarvisUltimate()
        yield jarvis
    finally:
        for p in reversed(started):
            try:
                p.stop()
            except RuntimeError:
                pass
