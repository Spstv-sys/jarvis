"""Юніт-тести core.playbook_loader: фолбек, класифікація домену, незмінність."""
import dataclasses

import core.playbook_loader as pl
from core.playbook_loader import Playbook, get_playbook, classify_domain


def test_get_playbook_unknown_returns_generic():
    assert get_playbook("неіснуючий_домен").domain == "generic"


def test_classify_empty_is_generic():
    assert classify_domain("") == "generic"
    assert classify_domain("   ") == "generic"


def test_classify_scoring(monkeypatch):
    fake = {
        "generic": pl._FALLBACK_GENERIC,
        "security": Playbook(domain="security", keywords=("sql", "ін'єкція")),
        "web": Playbook(domain="web", keywords=("http", "скрапінг")),
    }
    monkeypatch.setattr(pl, "load_playbooks", lambda: fake)
    assert classify_domain("захистись від sql ін'єкція") == "security"
    assert classify_domain("зроби http скрапінг сайту") == "web"
    assert classify_domain("щось геть нейтральне") == "generic"


def test_classify_only_generic(monkeypatch):
    monkeypatch.setattr(pl, "load_playbooks", lambda: {"generic": pl._FALLBACK_GENERIC})
    assert classify_domain("будь-який текст") == "generic"


def test_playbook_is_frozen():
    pb = Playbook(domain="x")
    raised = False
    try:
        pb.domain = "y"   # frozen dataclass → має кинути
    except dataclasses.FrozenInstanceError:
        raised = True
    assert raised
