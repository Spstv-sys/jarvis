"""
CommandHandlers — thin orchestrator that composes domain-specific handler mixins.

Domain mixins (single responsibility):
  - KnowledgeHandlersMixin  → knowledge_handlers.py
  - MediaHandlersMixin      → media_handlers.py
  - AIHandlersMixin         → ai_handlers.py

This file retains only the remaining utility handlers and wires everything
together so that existing callers remain unaffected.
"""
import os
import re
import logging
from datetime import datetime, timedelta

from colorama import Fore

import config
from core.utils import strip_triggers

# Re-export safety checker so existing callers (e.g. tests) can still do:
#   from core.handlers.command_handlers import _check_skill_safety
from core.handlers.knowledge_handlers import (
    _check_skill_safety,          # noqa: F401 – re-exported for backward compat
    _FORBIDDEN_SKILL_PATTERNS,    # noqa: F401
    KnowledgeHandlersMixin,
)
from core.handlers.media_handlers import MediaHandlersMixin
from core.handlers.ai_handlers import AIHandlersMixin

logger = logging.getLogger(__name__)

try:
    from PIL import Image as PILImage
except ImportError:
    PILImage = None

try:
    import pyautogui
except ImportError:
    pyautogui = None


class CommandHandlers(KnowledgeHandlersMixin, MediaHandlersMixin, AIHandlersMixin):
    """Unified command handler — aggregates all domain mixins."""

    def __init__(self, jarvis) -> None:
        self.jarvis = jarvis

    # ------------------------------------------------------------------
    # Convenience accessors (shared by all mixins via self.*)
    # ------------------------------------------------------------------
    @property
    def audio(self):
        return self.jarvis.audio

    @property
    def db(self):
        return self.jarvis.db

    @property
    def brain(self):
        return self.jarvis.brain

    @property
    def llm(self):
        return self.jarvis.llm

    @property
    def scraper(self):
        return self.jarvis.scraper

    @property
    def youtube(self):
        return self.jarvis.youtube

    # ==================================================================
    # SYSTEM ACTIONS (delegated to SystemActions)
    # ==================================================================
    def cmd_timer(self, cmd: str) -> str:
        logger.info(f"cmd_timer: {cmd!r}")
        return self.jarvis.sys_actions.cmd_timer(cmd)

    def cmd_hardware_control(self, cmd: str) -> str:
        logger.info(f"cmd_hardware_control: {cmd!r}")
        return self.jarvis.sys_actions.cmd_hardware_control(cmd)

    def cmd_apps_and_os(self, cmd: str) -> str:
        logger.info(f"cmd_apps_and_os: {cmd!r}")
        return self.jarvis.sys_actions.cmd_apps_and_os(cmd)

    def cmd_system_exit(self, cmd: str) -> str:
        logger.info(f"cmd_system_exit: {cmd!r}")
        return self.jarvis.sys_actions.cmd_system_exit(cmd)

    def cmd_stop_voice(self, cmd: str) -> str:
        logger.info(f"cmd_stop_voice: {cmd!r}")
        self.jarvis.output_queue.put("<CLEAR_QUEUE>")
        return "CONTINUE"

    # ==================================================================
    # VISION
    # ==================================================================
    def cmd_vision(self, cmd: str) -> str:
        logger.info(f"cmd_vision: {cmd!r}")
        self.audio.speak("Аналізую екран...")
        shot_path = "temp_screen.png"
        try:
            if not self.llm.gemini_client:
                self.audio.speak("Vision API недоступне. Активуйте Gemini.")
                return "CONTINUE"
            if pyautogui is None:
                self.audio.speak("Бібліотека pyautogui не встановлена.")
                return "CONTINUE"
            pyautogui.screenshot(shot_path)
            if PILImage is None:
                self.audio.speak("Бібліотека PIL не встановлена.")
                return "CONTINUE"
            img   = PILImage.open(shot_path)
            query = (
                strip_triggers(cmd, config.TRIGGERS["vision"])
                or "Що на екрані? Відповідай коротко."
            )
            res = self.llm.gemini_client.models.generate_content(
                model='gemini-2.5-flash', contents=[img, query]
            )
            self.audio.speak(res.text)
        except Exception as e:
            logger.error(f"Помилка Vision API: {e}")
            self.audio.speak("Не вдалося проаналізувати зображення.")
        finally:
            if os.path.exists(shot_path):
                os.remove(shot_path)
        return "CONTINUE"

    # ==================================================================
    # CALENDAR
    # ==================================================================
    def cmd_calendar(self, cmd: str) -> str:
        logger.info(f"cmd_calendar: {cmd!r}")
        date_str = datetime.now().strftime("%Y-%m-%d")
        if "завтра" in cmd:
            date_str = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        if "додай" in cmd or "заплануй" in cmd:
            task = (
                cmd.replace("додай задачу", "")
                   .replace("заплануй", "")
                   .replace("на завтра", "")
                   .strip()
            )
            self.db.add_task(date_str, task)
            self.audio.speak("Задачу внесено до реєстру.")
        else:
            tasks = self.db.get_tasks(date_str)
            self.audio.speak(f"Плани: {'. '.join(tasks)}." if tasks else "Розклад чистий.")
        return "CONTINUE"

    # ==================================================================
    # MEMORY
    # ==================================================================
    def cmd_memorize(self, cmd: str) -> str:
        logger.info(f"cmd_memorize: {cmd!r}")
        fact = re.sub(r"\bщо\b", "", cmd.replace("запам'ятай", ""), flags=re.IGNORECASE).strip()
        if not fact:
            self.audio.speak("Уточніть, що саме запам'ятати.")
            return "CONTINUE"
        self.db.add_fact(fact)
        self.brain.memorize(fact, source="user_fact")
        self.audio.speak("Інформацію завантажено у бази даних.")
        return "CONTINUE"

    # ==================================================================
    # FILE SEARCH
    # ==================================================================
    def cmd_find_file(self, cmd: str) -> str:
        logger.info(f"cmd_find_file: {cmd!r}")
        search_term = strip_triggers(cmd, config.TRIGGERS["find_file"])
        if not search_term:
            self.audio.speak("Назву якого файлу шукати?")
            return "CONTINUE"

        search_roots = [
            os.path.expanduser("~"),
            config.SORTING_DRIVE,
        ]

        self.audio.speak(f"Шукаю файл {search_term}...")
        found = []

        for root in search_roots:
            if not os.path.exists(root):
                continue
            for dirpath, _, filenames in os.walk(root):
                for fname in filenames:
                    if search_term.lower() in fname.lower():
                        found.append(os.path.join(dirpath, fname))
                if len(found) >= 5:
                    break
            if len(found) >= 5:
                break

        if not found:
            self.audio.speak(f"Файл з назвою '{search_term}' не знайдено.")
        else:
            result = f"Знайдено {len(found)} файл(ів):\n" + "\n".join(found)
            print(f"{Fore.CYAN}{result}")
            self.audio.speak(f"Знайдено {len(found)} файл(ів). Результати виведено на екран.")
        return "CONTINUE"
