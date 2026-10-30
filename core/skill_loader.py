"""Skill loader — динамічне завантаження навичок з теки ``skills/``.

Винесено з main.py у межах рефакторингу (розвантаження god-file).
Чисті функції без прив'язки до екземпляра JarvisUltimate:
  • ``load_skills(skills_dir, triggers)`` — повертає список навичок;
  • ``warn_on_trigger_collisions(dynamic_skills, triggers)`` — логує
    тригери динамічних навичок, що перекривають вбудовані маршрути.

Кожна навичка — це модуль у ``skills/*.py`` з атрибутами ``TRIGGERS``
(список фраз) та ``execute(cmd, jarvis)``.

Контракт ``jarvis`` для скілів описаний у :class:`core.context.SkillContext`:
плагіни гарантовано мають доступ лише до ``jarvis.audio``, ``jarvis.llm`` та
``jarvis.brain``. Покладатися на інші внутрішні атрибути не варто — їх може
змінити майбутній рефакторинг.
"""
from __future__ import annotations

import os
import sys
import glob
import logging
import importlib.util
import hashlib
import json
import threading
from pathlib import Path
from typing import Optional

from colorama import Fore

from core.utils import check_code_safety

logger = logging.getLogger(__name__)
_APPROVAL_LOCK = threading.Lock()


def _digest(source: bytes) -> str:
    # Git may check the same revision out as CRLF on Windows and LF on Linux.
    return hashlib.sha256(source.replace(b"\r\n", b"\n")).hexdigest()


def _read_manifest(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def approve_skill(path: str) -> None:
    """Trust this exact revision after an explicit activation by the user.

    Approval permits code to run with the assistant's host permissions. Static
    checks are advisory and must never substitute for this trust decision.
    """
    skill = Path(path)
    digest = _digest(skill.read_bytes())
    manifest = skill.parent / ".approved.json"
    with _APPROVAL_LOCK:
        approved = _read_manifest(manifest)
        approved[skill.name] = digest
        temporary = manifest.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(approved, indent=2), encoding="utf-8")
        os.replace(temporary, manifest)


def load_skills(skills_dir: str, triggers: Optional[dict] = None) -> list[dict]:
    """Завантажує всі навички з ``skills_dir`` (нерекурсивно, ``*.py``).

    Повертає список ``{'triggers', 'func', 'name'}``. Якщо передано
    ``triggers`` (вбудовані маршрути), додатково попереджає про колізії.
    """
    # Both manifests contain hashes, never executable code. A new or changed
    # file must be approved before even its module-level code can execute.
    bundled = _read_manifest(Path(__file__).with_name("bundled_skills.json"))
    approved = _read_manifest(Path(skills_dir) / ".approved.json")

    skills: list[dict] = []
    for file in glob.glob(os.path.join(skills_dir, "*.py")):
        module_name = os.path.basename(file)[:-3]
        try:
            source = Path(file).read_bytes()
            digest = _digest(source)
            name = Path(file).name
            if bundled.get(name) != digest and approved.get(name) != digest:
                logger.warning("Навичку %s пропущено: потрібна явна активація цієї версії", name)
                continue
            safe, reason = check_code_safety(source.decode("utf-8-sig"))
            if not safe:
                msg = f"Навичка {module_name} НЕ пройшла перевірку безпеки: {reason}"
                logger.warning(msg)
                print(f"{Fore.YELLOW}⚠ {msg}")

            sys.modules.pop(module_name, None)
            spec = importlib.util.spec_from_file_location(module_name, file)
            module = importlib.util.module_from_spec(spec)
            # Execute exactly the bytes whose hash was approved, not a second
            # disk read that could have changed since verification.
            exec(compile(source, file, "exec"), module.__dict__)
            if hasattr(module, 'TRIGGERS') and hasattr(module, 'execute'):
                skills.append({
                    'triggers': module.TRIGGERS,
                    'func':     module.execute,
                    'name':     module_name,
                })
                logger.info(f"Навичку завантажено: {module_name}")
        except Exception as e:
            logger.error(f"Помилка завантаження навички {module_name}: {e}")

    if skills:
        print(f"{Fore.MAGENTA}🧠 Завантажено динамічних навичок: {len(skills)}")
        if triggers:
            warn_on_trigger_collisions(skills, triggers)

    return skills


def warn_on_trigger_collisions(dynamic_skills: list[dict], triggers: dict) -> None:
    """Попереджає, коли тригер динамічної навички перекриває вбудований маршрут.

    Динамічні навички диспетчеризуються ПЕРЕД вбудованими маршрутами в
    ``JarvisUltimate.execute_command``, тож надто широкий тригер (напр.
    "погода") у користувацькому плагіні може тихо «затінити» базову
    функціональність. Логування цього на завантаженні робить конфлікт видимим.
    """
    builtin_triggers: dict[str, str] = {}
    for route_key, trigger_list in triggers.items():
        for trig in trigger_list:
            builtin_triggers[trig.lower()] = route_key

    for skill in dynamic_skills:
        for trig in skill['triggers']:
            shadowed = builtin_triggers.get(trig.lower())
            if shadowed:
                msg = (
                    f"Skill '{skill['name']}' trigger {trig!r} shadows "
                    f"built-in route '{shadowed}'. Built-in command will not run."
                )
                logger.warning(msg)
                print(f"{Fore.YELLOW}⚠ {msg}")
