"""SystemMixin — керування LLM, системні дії, фінанси, пошук файлів, календар."""
from __future__ import annotations

import os
import logging
from datetime import datetime, timedelta

import requests
from colorama import Fore

import config
from core.utils import strip_triggers

logger = logging.getLogger(__name__)


class SystemMixin:
    # ==================================================================
    # AI CONTROL
    # ==================================================================
    def cmd_switch_ai(self, cmd: str) -> str:
        logger.info(f"cmd_switch_ai: {cmd!r}")
        prev = self.llm.active_provider
        self.llm.switch_provider(cmd)
        # Notify UI about provider change so the indicator updates live.
        # The frontend (useJarvis.js) extracts provider from STATUS messages
        # matching the pattern /інтелект:\s*(\S+)/.
        if self.llm.active_provider != prev and self.jarvis.ui_callback:
            self.jarvis.ui_callback(
                "STATUS",
                f"Активний інтелект: {self.llm.active_provider}",
            )
        return "CONTINUE"

    def cmd_personality(self, cmd: str) -> str:
        logger.info(f"cmd_personality: {cmd!r}")
        self.llm.set_personality(cmd)
        return "CONTINUE"

    def cmd_check_ai(self, cmd: str) -> str:
        logger.info(f"cmd_check_ai: {cmd!r}")
        status_msg = self.llm.get_status()
        print(f"{Fore.CYAN}ℹ️  {status_msg}")
        self.audio.speak(status_msg)
        return "CONTINUE"

    # ==================================================================
    # FINANCE
    # ==================================================================
    def cmd_finance(self, cmd: str) -> str:
        logger.info(f"cmd_finance: {cmd!r}")
        try:
            res = requests.get("https://api.monobank.ua/bank/currency", timeout=5)
            res.raise_for_status()
            data = res.json()
            usd  = next(i for i in data if i['currencyCodeA'] == 840 and i['currencyCodeB'] == 980)
            eur  = next(i for i in data if i['currencyCodeA'] == 978 and i['currencyCodeB'] == 980)
            self.audio.speak(
                f"Долар: {usd['rateBuy']} — {usd['rateSell']}. "
                f"Євро: {eur['rateBuy']} — {eur['rateSell']}."
            )
        except StopIteration:
            self.audio.speak("Курс валют у відповіді банку не знайдено.")
        except Exception as e:
            logger.error(f"Помилка отримання курсу валют: {e}")
            self.audio.speak("Фінансові сервери не відповідають.")
        return "CONTINUE"

    # ==================================================================
    # SYSTEM ACTIONS (delegated)
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
        found: list[str] = []

        # Обмеження, щоб не сканувати вічно (AppData, node_modules тощо).
        MAX_DEPTH = 4
        MAX_RESULTS = 5
        SKIP_DIRS = {
            ".venv", "venv", "node_modules", ".git", "__pycache__",
            "AppData", "Application Data", ".cache", ".npm", ".gradle",
            "dist", "build", ".next", ".vscode", ".idea",
        }
        term_lower = search_term.lower()

        def _scan(path: str, depth: int) -> None:
            if depth > MAX_DEPTH or len(found) >= MAX_RESULTS:
                return
            try:
                with os.scandir(path) as it:
                    for entry in it:
                        if len(found) >= MAX_RESULTS:
                            return
                        name = entry.name
                        if name.startswith(".") or name in SKIP_DIRS:
                            continue
                        try:
                            if entry.is_file(follow_symlinks=False):
                                if term_lower in name.lower():
                                    found.append(entry.path)
                            elif entry.is_dir(follow_symlinks=False):
                                _scan(entry.path, depth + 1)
                        except OSError:
                            continue
            except (OSError, PermissionError):
                return

        for root in search_roots:
            if not os.path.exists(root):
                continue
            _scan(root, 0)
            if len(found) >= MAX_RESULTS:
                break

        if not found:
            self.audio.speak(f"Файл з назвою '{search_term}' не знайдено.")
        else:
            result = f"Знайдено {len(found)} файл(ів):\n" + "\n".join(found)
            print(f"{Fore.CYAN}{result}")
            self.audio.speak(f"Знайдено {len(found)} файл(ів). Результати виведено на екран.")
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
