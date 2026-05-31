"""Юніт-тести core.system_actions: парсинг таймера та ОС-гарди (headless)."""
from unittest.mock import MagicMock

import core.system_actions as sa
from core.system_actions import SystemActions


def _actions():
    return SystemActions(audio_manager=MagicMock(), db=MagicMock())


def test_timer_parses_duration():
    a = _actions()
    assert a.cmd_timer("постав таймер на 5 хвилин") == "CONTINUE"
    spoken = " ".join(str(c.args[0]) for c in a.audio.speak.call_args_list)
    assert "5 хвилин" in spoken and "запущено" in spoken


def test_timer_no_duration():
    a = _actions()
    a.cmd_timer("таймер")  # без числа
    assert any("Не зрозумів" in str(c.args[0]) for c in a.audio.speak.call_args_list)


def test_hardware_volume_guarded_when_no_keyboard(monkeypatch):
    monkeypatch.setattr(sa, "keyboard", None)
    a = _actions()
    assert a.cmd_hardware_control("зроби гучніше") == "CONTINUE"
    assert any("недоступне" in str(c.args[0]) for c in a.audio.speak.call_args_list)


def test_hardware_brightness_guarded_when_no_sbc(monkeypatch):
    monkeypatch.setattr(sa, "sbc", None)
    monkeypatch.setattr(sa, "keyboard", MagicMock())  # клавіатура є, але яскравість — ні
    a = _actions()
    assert a.cmd_hardware_control("яскравість 50") == "CONTINUE"
    assert any("яскравіст" in str(c.args[0]).lower() and "недоступне" in str(c.args[0])
               for c in a.audio.speak.call_args_list)
