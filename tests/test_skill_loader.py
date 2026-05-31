"""Юніт-тести core.skill_loader: динамічне завантаження навичок + колізії."""
import logging

from core.skill_loader import load_skills, warn_on_trigger_collisions


def _write_skill(path, body):
    path.write_text(body, encoding="utf-8")


def test_loads_valid_skill(tmp_path):
    _write_skill(tmp_path / "skill_hi.py",
                 "TRIGGERS = ['привіт', 'хай']\n"
                 "def execute(cmd, jarvis):\n    return 'hi'\n")
    skills = load_skills(str(tmp_path))
    assert len(skills) == 1
    s = skills[0]
    assert s["name"] == "skill_hi"
    assert s["triggers"] == ["привіт", "хай"]
    assert s["func"]("привіт", None) == "hi"


def test_skips_module_without_contract(tmp_path):
    # немає TRIGGERS/execute — має бути проігноровано
    _write_skill(tmp_path / "not_a_skill.py", "X = 1\n")
    _write_skill(tmp_path / "skill_ok.py",
                 "TRIGGERS = ['тест']\ndef execute(cmd, jarvis):\n    return 'ok'\n")
    skills = load_skills(str(tmp_path))
    names = {s["name"] for s in skills}
    assert names == {"skill_ok"}


def test_broken_skill_does_not_break_loading(tmp_path):
    _write_skill(tmp_path / "skill_bad.py", "TRIGGERS = ['x']\nthis is not python\n")
    _write_skill(tmp_path / "skill_good.py",
                 "TRIGGERS = ['добре']\ndef execute(cmd, jarvis):\n    return 'g'\n")
    skills = load_skills(str(tmp_path))   # не має кидати виняток
    assert {s["name"] for s in skills} == {"skill_good"}


def test_empty_dir_returns_empty(tmp_path):
    assert load_skills(str(tmp_path)) == []


def test_collision_warning(caplog):
    dynamic = [{"triggers": ["погода"], "func": lambda c, j: None, "name": "myskill"}]
    builtin = {"weather": ["погода", "яка погода"]}
    with caplog.at_level(logging.WARNING):
        warn_on_trigger_collisions(dynamic, builtin)
    assert any("shadows" in r.message and "weather" in r.message for r in caplog.records)


def test_no_collision_when_unique(caplog):
    dynamic = [{"triggers": ["унікальний тригер"], "func": lambda c, j: None, "name": "s"}]
    builtin = {"weather": ["погода"]}
    with caplog.at_level(logging.WARNING):
        warn_on_trigger_collisions(dynamic, builtin)
    assert not any("shadows" in r.message for r in caplog.records)


# ----------------------------------------------- load-time safety gate
_UNSAFE = (
    "import os\n"
    "TRIGGERS = ['небезпека']\n"
    "def execute(cmd, jarvis):\n"
    "    os.system('rm -rf /')  # небезпечний патерн (не виконується при import)\n"
    "    return 'done'\n"
)
_SAFE = (
    "TRIGGERS = ['привіт']\n"
    "def execute(cmd, jarvis):\n    return 'hi'\n"
)


def test_unsafe_skill_warned_but_loaded_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("JARVIS_SKILL_SAFETY_ENFORCE", raising=False)
    (tmp_path / "skill_danger.py").write_text(_UNSAFE, encoding="utf-8")
    skills = load_skills(str(tmp_path))
    # за умовчанням (warn) — навичка все одно завантажується
    assert any(s["name"] == "skill_danger" for s in skills)


def test_unsafe_skill_skipped_when_enforced(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_SKILL_SAFETY_ENFORCE", "1")
    (tmp_path / "skill_danger.py").write_text(_UNSAFE, encoding="utf-8")
    (tmp_path / "skill_ok.py").write_text(_SAFE, encoding="utf-8")
    skills = load_skills(str(tmp_path))
    names = {s["name"] for s in skills}
    assert "skill_danger" not in names   # пропущено в суворому режимі
    assert "skill_ok" in names           # безпечна завантажилась


def test_safe_skill_loads_under_enforce(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_SKILL_SAFETY_ENFORCE", "1")
    (tmp_path / "skill_ok.py").write_text(_SAFE, encoding="utf-8")
    skills = load_skills(str(tmp_path))
    assert {s["name"] for s in skills} == {"skill_ok"}
