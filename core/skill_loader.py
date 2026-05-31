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
from typing import Optional

from colorama import Fore

from core.utils import check_code_safety

logger = logging.getLogger(__name__)


def load_skills(skills_dir: str, triggers: Optional[dict] = None) -> list[dict]:
    """Завантажує всі навички з ``skills_dir`` (нерекурсивно, ``*.py``).

    Повертає список ``{'triggers', 'func', 'name'}``. Якщо передано
    ``triggers`` (вбудовані маршрути), додатково попереджає про колізії.
    """
    # Перевірка безпеки ПЕРЕД виконанням коду навички (exec_module запускає
    # довільний код модуля). За умовчанням — лише попередження (бо комплектні
    # довірені скіли можуть легітимно вживати, напр., importlib). Суворий режим
    # JARVIS_SKILL_SAFETY_ENFORCE=1 пропускає (не виконує) небезпечні навички.
    enforce = os.getenv("JARVIS_SKILL_SAFETY_ENFORCE", "0").lower() in ("1", "true", "yes", "on")

    skills: list[dict] = []
    for file in glob.glob(os.path.join(skills_dir, "*.py")):
        module_name = os.path.basename(file)[:-3]
        try:
            try:
                safe, reason = check_code_safety(open(file, encoding="utf-8").read())
            except OSError:
                safe, reason = True, ""  # помилку читання обробить exec_module нижче
            if not safe:
                msg = f"Навичка {module_name} НЕ пройшла перевірку безпеки: {reason}"
                logger.warning(msg)
                print(f"{Fore.YELLOW}⚠ {msg}")
                if enforce:
                    logger.warning(f"Навичку {module_name} пропущено (JARVIS_SKILL_SAFETY_ENFORCE=1).")
                    continue

            sys.modules.pop(module_name, None)
            spec = importlib.util.spec_from_file_location(module_name, file)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
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
