"""
Unit-тести для JarvisUltimate.
Всі тести запускаються без мікрофону, динаміків або LLM.
"""
import queue
from unittest.mock import MagicMock, patch

import config


# ---------------------------------------------------------------------------
# 1. fuzzy_in_command — True для близького рядка
# ---------------------------------------------------------------------------
def test_fuzzy_in_command_true(jarvis_instance):
    assert jarvis_instance.fuzzy_in_command("яка погода сьогодні", "погода") is True


# ---------------------------------------------------------------------------
# 2. fuzzy_in_command — False для нерелевантного рядка
# ---------------------------------------------------------------------------
def test_fuzzy_in_command_false(jarvis_instance):
    assert jarvis_instance.fuzzy_in_command("відкрий браузер", "погода") is False


# ---------------------------------------------------------------------------
# 3. _strip_words — видаляє стоп-слова з тексту
# ---------------------------------------------------------------------------
def test_strip_words(jarvis_instance):
    result = jarvis_instance._strip_words(
        "навчись грати на гітарі",
        ["навчись"]
    )
    assert result == "грати на гітарі"


# ---------------------------------------------------------------------------
# 4. _normalize_filename — нормалізує імена файлів
# ---------------------------------------------------------------------------
def test_normalize_filename(jarvis_instance):
    result = jarvis_instance._normalize_filename("відкрий тест пай")
    assert ".py" in result


# ---------------------------------------------------------------------------
# 5. enqueue_speech — кладе текст в output_queue
# ---------------------------------------------------------------------------
def test_enqueue_speech_puts_to_queue(jarvis_instance):
    jarvis_instance.enqueue_speech("Привіт, Земля!")
    assert not jarvis_instance.output_queue.empty()
    item = jarvis_instance.output_queue.get_nowait()
    assert item == "Привіт, Земля!"


# ---------------------------------------------------------------------------
# 6. enqueue_speech — викликає ui_callback
# ---------------------------------------------------------------------------
def test_enqueue_speech_calls_ui_callback(jarvis_instance):
    callback = MagicMock()
    jarvis_instance.ui_callback = callback

    jarvis_instance.enqueue_speech("Тест колбеку")

    callback.assert_called_once_with("Джарвіс", "Тест колбеку")


# ---------------------------------------------------------------------------
# 7. cmd_stop_voice — кладе <CLEAR_QUEUE> в output_queue
# ---------------------------------------------------------------------------
def test_cmd_stop_voice(jarvis_instance):
    jarvis_instance.cmd_stop_voice("стоп")
    item = jarvis_instance.output_queue.get_nowait()
    assert item == "<CLEAR_QUEUE>"


# ---------------------------------------------------------------------------
# 8. execute_command — маршрутизує погоду до cmd_weather
# ---------------------------------------------------------------------------
def test_execute_command_routes_weather(jarvis_instance):
    trigger = config.TRIGGERS["weather"][0]   # "погода"
    cmd     = f"{trigger} зараз"              # "погода зараз" — не збігається з weather_exact

    jarvis_instance.cmd_weather = MagicMock(return_value="CONTINUE")
    result = jarvis_instance.execute_command(cmd)

    jarvis_instance.cmd_weather.assert_called_once_with(cmd)
    assert result == "CONTINUE"
