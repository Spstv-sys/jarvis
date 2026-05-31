"""Юніт-тести core.command_router.dispatch: пріоритет скілів, маршрути, фолбек."""
import re
from unittest.mock import MagicMock

import config
from core.command_router import dispatch


class _Handlers:
    def __init__(self):
        self.cmd_weather = MagicMock(return_value="WEATHER")
        self.cmd_system_exit = MagicMock(return_value="STOP")
    def __getattr__(self, name):
        # будь-який інший cmd_* -> безпечний no-op
        return MagicMock(return_value="CONTINUE")


class FakeJarvis:
    def __init__(self):
        self.handlers = _Handlers()
        self.dynamic_skills = []
        self.intent_router = None
        self.fuzzy_threshold = 80
        self.audio = MagicMock()
    def _trigger_in_cmd(self, trigger, cmd):
        return bool(re.search(r'(?<!\w)' + re.escape(trigger) + r'(?!\w)', cmd, re.IGNORECASE))
    def fuzzy_in_command(self, command, trigger, threshold=None):
        return False
    def handle_ai_chat(self, cmd):
        return "CHAT"


def _triggers(monkeypatch):
    monkeypatch.setattr(config, "TRIGGERS",
                        {"weather": ["погода"], "system_exit": ["завершити роботу"]})


def test_dynamic_skill_takes_priority(monkeypatch):
    _triggers(monkeypatch)
    j = FakeJarvis()
    j.dynamic_skills = [{"triggers": ["склади план"],
                         "func": lambda c, jr: "PLANNED", "name": "plan"}]
    res = dispatch(j, "склади план на день")
    assert res == "CONTINUE"
    j.audio.speak.assert_called_once_with("PLANNED")


def test_builtin_route_match(monkeypatch):
    _triggers(monkeypatch)
    j = FakeJarvis()
    res = dispatch(j, "яка сьогодні погода")
    assert res == "WEATHER"
    j.handlers.cmd_weather.assert_called_once()


def test_system_exit_returns_stop(monkeypatch):
    _triggers(monkeypatch)
    j = FakeJarvis()
    assert dispatch(j, "завершити роботу") == "STOP"


def test_fallback_to_ai_chat(monkeypatch):
    _triggers(monkeypatch)
    j = FakeJarvis()
    assert dispatch(j, "розкажи щось цікаве xyz") == "CHAT"


def test_skill_exception_is_handled(monkeypatch):
    _triggers(monkeypatch)
    j = FakeJarvis()
    def boom(c, jr):
        raise RuntimeError("skill failed")
    j.dynamic_skills = [{"triggers": ["зламайся"], "func": boom, "name": "bad"}]
    # не має прокидати виняток назовні
    assert dispatch(j, "зламайся зараз") == "CONTINUE"


def test_intent_router_fallback(monkeypatch):
    _triggers(monkeypatch)
    j = FakeJarvis()
    guess = MagicMock(); guess.name = "weather"; guess.confidence = 0.9; guess.source = "llm"
    j.intent_router = MagicMock(); j.intent_router.classify.return_value = guess
    monkeypatch.setattr(config, "INTENT_ROUTER_MIN_CONF", 0.55, raising=False)
    res = dispatch(j, "хочу знати чи брати парасолю")
    assert res == "WEATHER"
    j.handlers.cmd_weather.assert_called_once()
