"""Вартовий продуктового рішення: автономна проактивність — opt-in (вимкнена).

reflector/anticipator увімкнені (отримують дані за тригером), але їхній
АВТОНОМНИЙ фоновий цикл за замовчуванням вимкнено (*_INTERVAL_SEC=0).
Цей тест ловить випадкове увімкнення дефолтів. Конфіг перевантажується з
очищеним env, щоб результат не залежав від оточення розробника/CI.
"""
import importlib

import config as _config_mod


def _reload_config_clean_env(monkeypatch):
    for var in ("REFLECTOR_INTERVAL_SEC", "ANTICIPATOR_INTERVAL_SEC", "KG_ENABLED"):
        monkeypatch.delenv(var, raising=False)
    return importlib.reload(_config_mod)


def test_reflector_autonomous_loop_off_by_default(monkeypatch):
    cfg = _reload_config_clean_env(monkeypatch)
    assert cfg.REFLECTOR_INTERVAL_SEC == 0, "автономний цикл reflector має бути off (0) за умовч."


def test_anticipator_autonomous_loop_off_by_default(monkeypatch):
    cfg = _reload_config_clean_env(monkeypatch)
    assert cfg.ANTICIPATOR_INTERVAL_SEC == 0, "автономний цикл anticipator має бути off (0) за умовч."


def test_knowledge_graph_off_by_default(monkeypatch):
    cfg = _reload_config_clean_env(monkeypatch)
    assert cfg.KG_ENABLED is False, "Knowledge Graph має бути вимкнено за умовч. (важка spaCy-залежність)"


def test_layers_still_subscribed_enabled(monkeypatch):
    # шари НЕ вимкнені повністю — вони отримують дані за тригером (ENABLED=1)
    cfg = _reload_config_clean_env(monkeypatch)
    assert cfg.REFLECTOR_ENABLED is True and cfg.ANTICIPATOR_ENABLED is True
