"""Юніт-тести core.runtime: cancel, цикл обробки, process_command, shutdown."""
import os
import queue
import logging
import threading
from unittest.mock import MagicMock

import config
from core import runtime


class _InlinePool:
    """Пул, що виконує задачу синхронно — для детермінованих тестів."""
    def submit(self, fn, *args):
        fn(*args)
    def shutdown(self, **kw):
        pass


def test_request_cancel_sets_event():
    j = MagicMock()
    j.cancel_event = threading.Event()
    runtime.request_cancel(j)
    assert j.cancel_event.is_set()


def test_processing_loop_dispatches_via_bound_method():
    j = MagicMock()
    j.is_running = True
    j.input_queue = queue.Queue()
    j._brain_pool = _InlinePool()
    seen = []
    def fake_proc(cmd):
        seen.append(cmd)
        j.is_running = False    # зупиняємо цикл після першої команди
    j._process_command = fake_proc
    j.input_queue.put("привіт")
    runtime.processing_loop(j)
    assert seen == ["привіт"]


def _proc_jarvis():
    j = MagicMock()
    j.cancel_event = threading.Event()
    j.input_queue = queue.Queue(); j.input_queue.put("_")
    j.audio = MagicMock()
    return j


def test_process_command_stop_triggers_shutdown():
    j = _proc_jarvis()
    j.execute_command.return_value = "STOP"
    runtime.process_command(j, "вимкнись")
    assert j.is_running is False
    j._force_shutdown.assert_called_once()
    # епізодична пам'ять логнула команду зі статусом STOP
    assert j.episodic_memory.log.called


def test_process_command_error_logged_no_shutdown():
    j = _proc_jarvis()
    j.execute_command.side_effect = RuntimeError("boom")
    runtime.process_command(j, "щось")
    j._force_shutdown.assert_not_called()
    j.audio.speak.assert_called_once()  # «Виникла системна помилка.»
    kind = j.episodic_memory.log.call_args.kwargs.get("kind")
    assert kind == "error"


def test_process_command_clears_cancel_flag():
    j = _proc_jarvis()
    j.cancel_event.set()
    j.execute_command.return_value = "CONTINUE"
    runtime.process_command(j, "команда")
    assert not j.cancel_event.is_set()   # очищено на старті


def test_force_shutdown_calls_os_exit(monkeypatch):
    exited = {}
    monkeypatch.setattr(os, "_exit", lambda code: exited.setdefault("code", code))
    monkeypatch.setattr(logging, "shutdown", lambda: None)
    j = MagicMock()
    j.output_queue = queue.Queue()        # порожня -> _kill не чекає
    runtime.force_shutdown(j)
    assert j.is_running is False
    # _kill виконується у daemon-потоці — дочекаймося
    for t in threading.enumerate():
        if t.name == "ShutdownThread":
            t.join(timeout=3)
    assert exited.get("code") == 0
    j._persist_working_memory.assert_called_once()
