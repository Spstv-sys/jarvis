"""
Unit-тести для JarvisUltimate та супутніх модулів.
Всі тести запускаються без мікрофону, динаміків або LLM.
"""
import queue
import sqlite3
import sys
import tempfile
import threading
import os
from unittest.mock import MagicMock, patch, call

import pytest

import config


# ===========================================================================
# JarvisUltimate — базові тести
# ===========================================================================

def test_fuzzy_in_command_true(jarvis_instance):
    assert jarvis_instance.fuzzy_in_command("яка погода сьогодні", "погода") is True


def test_fuzzy_in_command_false(jarvis_instance):
    assert jarvis_instance.fuzzy_in_command("відкрий браузер", "погода") is False


def test_enqueue_speech_puts_to_queue(jarvis_instance):
    jarvis_instance.enqueue_speech("Привіт, Земля!")
    assert not jarvis_instance.output_queue.empty()
    item = jarvis_instance.output_queue.get_nowait()
    assert item == "Привіт, Земля!"


def test_enqueue_speech_calls_ui_callback(jarvis_instance):
    callback = MagicMock()
    jarvis_instance.ui_callback = callback
    jarvis_instance.enqueue_speech("Тест колбеку")
    callback.assert_called_once_with("Джарвіс", "Тест колбеку")


def test_cmd_stop_voice(jarvis_instance):
    # Backward-compat wrapper still works
    jarvis_instance.cmd_stop_voice("стоп")
    item = jarvis_instance.output_queue.get_nowait()
    assert item == "<CLEAR_QUEUE>"


def test_execute_command_routes_weather(jarvis_instance):
    trigger = config.TRIGGERS["weather"][0]  # "погода"
    cmd     = f"{trigger} зараз"

    jarvis_instance.handlers.cmd_weather = MagicMock(return_value="CONTINUE")
    result = jarvis_instance.execute_command(cmd)

    jarvis_instance.handlers.cmd_weather.assert_called_once_with(cmd)
    assert result == "CONTINUE"


# ===========================================================================
# _trigger_in_cmd — word-boundary matching (Problem 9)
# ===========================================================================

def test_trigger_in_cmd_exact_match(jarvis_instance):
    assert jarvis_instance._trigger_in_cmd("погода", "яка погода сьогодні") is True


def test_trigger_in_cmd_no_false_positive(jarvis_instance):
    # Trigger "погода" must NOT fire when the word doesn't appear as a whole token
    assert jarvis_instance._trigger_in_cmd("погода", "безпогодниця") is False


def test_trigger_in_cmd_multi_word_trigger(jarvis_instance):
    assert jarvis_instance._trigger_in_cmd("свіжі новини", "покажи свіжі новини") is True


# ===========================================================================
# start_processing_loop — deduplication (Problem 2)
# ===========================================================================

def test_start_processing_loop_dispatches_command(jarvis_instance):
    """start_processing_loop must dequeue one command and run _process_command."""
    processed = []

    def fake_process(cmd):
        processed.append(cmd)
        jarvis_instance.is_running = False  # stop after first command

    jarvis_instance._process_command = fake_process
    jarvis_instance.input_queue.put("тест")

    jarvis_instance.start_processing_loop()
    assert processed == ["тест"]


# ===========================================================================
# JarvisDatabase (Problem 10)
# ===========================================================================

@pytest.fixture
def db_instance():
    from core.memory import JarvisDatabase
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    db = JarvisDatabase(db_path=path)
    yield db
    db.close()
    os.unlink(path)


def test_db_set_and_get_setting(db_instance):
    db_instance.set_setting("theme", "dark")
    assert db_instance.get_setting("theme") == "dark"


def test_db_get_setting_default(db_instance):
    assert db_instance.get_setting("nonexistent", default="fallback") == "fallback"


def test_db_add_and_get_task(db_instance):
    db_instance.add_task("2025-01-01", "зустріч о 10:00")
    tasks = db_instance.get_tasks("2025-01-01")
    assert "зустріч о 10:00" in tasks


def test_db_add_fact_and_recall(db_instance):
    db_instance.add_fact("Python — мова програмування")
    facts = db_instance.get_recent_facts(limit=5)
    assert any("Python" in f for f in facts)


def test_db_fact_limit_enforced(db_instance):
    """Adding more facts than MAX_FACTS keeps at most MAX_FACTS and drops oldest."""
    first_fact = "перший факт — має бути видалений"
    db_instance.add_fact(first_fact)
    for i in range(db_instance.MAX_FACTS):
        db_instance.add_fact(f"новий факт {i}")

    facts = db_instance.get_recent_facts(limit=db_instance.MAX_FACTS + 20)
    assert len(facts) <= db_instance.MAX_FACTS
    # The very first fact must have been evicted
    assert first_fact not in facts


# ===========================================================================
# LLMHandler — provider switching & cache (Problem 10)
# ===========================================================================

@pytest.fixture
def llm_instance():
    from core.llm_handler import LLMHandler

    mock_db    = MagicMock()
    mock_brain = MagicMock()
    mock_brain.search.return_value = ""
    mock_brain.get_recent_facts = MagicMock(return_value=[])
    mock_audio = MagicMock()

    with patch("core.llm_handler.DeepSeekProvider"), \
         patch("core.llm_handler.GeminiProvider"), \
         patch("core.llm_handler.OllamaProvider"), \
         patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test", "GEMINI_API_KEY": "test"}):
        handler = LLMHandler(db=mock_db, brain=mock_brain, audio=mock_audio)

    return handler


def test_llm_looks_like_code_positive(llm_instance):
    code = "def hello():\n    return 'world'\nimport os\nfrom sys import path"
    assert llm_instance._looks_like_code(code) is True


def test_llm_looks_like_code_negative(llm_instance):
    text = "Сьогодні гарна погода. Небо ясне і сонячне."
    assert llm_instance._looks_like_code(text) is False


def test_llm_cache_set_and_get(llm_instance):
    llm_instance._set_cached("hello", "world")
    assert llm_instance._get_cached("hello") == "world"


def test_llm_cache_eviction(llm_instance):
    for i in range(llm_instance.CACHE_MAX + 5):
        llm_instance._set_cached(f"key_{i}", f"val_{i}")
    assert len(llm_instance._response_cache) <= llm_instance.CACHE_MAX


def test_llm_switch_provider_unknown(llm_instance):
    llm_instance.switch_provider("активуй невідомий провайдер")
    llm_instance.audio.speak.assert_called()
    spoken = llm_instance.audio.speak.call_args[0][0]
    assert "Невідомий" in spoken or "Доступні" in spoken


# ===========================================================================
# strip_triggers utility (Problem 5)
# ===========================================================================

def test_strip_triggers_removes_single_word():
    from core.utils import strip_triggers
    assert strip_triggers("навчись грати на гітарі", ["навчись"]) == "грати на гітарі"


def test_strip_triggers_removes_phrase_longest_first():
    from core.utils import strip_triggers
    result = strip_triggers("увімкни пісню queen", ["увімкни", "запусти пісню", "пісню"])
    assert "queen" in result
    assert "увімкни" not in result


def test_strip_triggers_case_insensitive():
    from core.utils import strip_triggers
    assert strip_triggers("НАВЧИСЬ робити тести", ["навчись"]) == "робити тести"


# ===========================================================================
# normalize_filename utility (Problem 5)
# ===========================================================================

def test_normalize_filename_py_extension():
    from core.utils import normalize_filename
    assert ".py" in normalize_filename("відкрий тест пай")


def test_normalize_filename_replaces_ukr_name():
    from core.utils import normalize_filename
    result = normalize_filename("скрипт точка джес")
    assert "script" in result


# ===========================================================================
# Skill security checker (Problem 7)
# ===========================================================================

def test_skill_safety_clean_code():
    from core.handlers.command_handlers import _check_skill_safety
    code = "TRIGGERS = ['тест']\ndef execute(cmd, jarvis):\n    return 'ok'"
    safe, pattern = _check_skill_safety(code)
    assert safe is True
    assert pattern == ""


def test_skill_safety_os_system_blocked():
    from core.handlers.command_handlers import _check_skill_safety
    code = "import os\nos.system('rm -rf /')"
    safe, pattern = _check_skill_safety(code)
    assert safe is False


def test_skill_safety_subprocess_blocked():
    from core.handlers.command_handlers import _check_skill_safety
    code = "import subprocess\nsubprocess.run(['ls'])"
    safe, pattern = _check_skill_safety(code)
    assert safe is False


def test_skill_safety_eval_blocked():
    from core.handlers.command_handlers import _check_skill_safety
    code = "result = eval(user_input)"
    safe, pattern = _check_skill_safety(code)
    assert safe is False


def test_skill_safety_exec_blocked():
    from core.handlers.command_handlers import _check_skill_safety
    code = "exec(compiled_code)"
    safe, pattern = _check_skill_safety(code)
    assert safe is False


# ===========================================================================
# AudioManager — перевірка наявності мікрофону
# ===========================================================================

def _make_audio_manager(mock_pa):
    """Helper: create an AudioManager backed by the provided PyAudio mock."""
    sys.modules["pyaudio"].PyAudio.return_value = mock_pa
    stop_event = threading.Event()

    with patch("core.audio_manager.AudioManager._load_oww_model"):
        from core.audio_manager import AudioManager
        am = AudioManager(stop_event)
    return am


def test_check_microphone_found():
    """check_microphone returns True when at least one input device exists."""
    mock_pa = MagicMock()
    mock_pa.get_device_count.return_value = 2
    mock_pa.get_device_info_by_index.side_effect = [
        {"maxInputChannels": 0, "name": "Speaker"},
        {"maxInputChannels": 1, "name": "Built-in Mic"},
    ]

    am = _make_audio_manager(mock_pa)

    assert am.mic_available is True


def test_check_microphone_not_found():
    """check_microphone returns False when no input device exists."""
    mock_pa = MagicMock()
    mock_pa.get_device_count.return_value = 1
    mock_pa.get_device_info_by_index.return_value = {"maxInputChannels": 0, "name": "Speaker"}

    am = _make_audio_manager(mock_pa)

    assert am.mic_available is False


def test_check_microphone_no_devices():
    """check_microphone returns False when PyAudio reports zero devices."""
    mock_pa = MagicMock()
    mock_pa.get_device_count.return_value = 0

    am = _make_audio_manager(mock_pa)

    assert am.mic_available is False


def test_listen_for_wake_word_returns_false_without_mic():
    """listen_for_wake_word returns False immediately when mic_available is False."""
    mock_pa = MagicMock()
    mock_pa.get_device_count.return_value = 0

    am = _make_audio_manager(mock_pa)
    am._oww_ready.set()  # unblock any wait

    result = am.listen_for_wake_word()

    assert result is False


def test_listen_for_command_returns_none_without_mic():
    """listen_for_command returns None immediately when mic_available is False."""
    mock_pa = MagicMock()
    mock_pa.get_device_count.return_value = 0

    am = _make_audio_manager(mock_pa)

    result = am.listen_for_command()

    assert result is None
