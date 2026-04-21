"""
AIHandlersMixin — управління LLM, асистент, диктування, веб-пошук.

Відокремлено від CommandHandlers для зменшення розміру God Class.
"""
import os
import re
import time
import logging
import threading

import requests
from colorama import Fore
from duckduckgo_search import DDGS

import config
from core.utils import strip_triggers

logger = logging.getLogger(__name__)

try:
    from PIL import Image as PILImage
except ImportError:
    PILImage = None

try:
    import pyautogui
except ImportError:
    pyautogui = None


class AIHandlersMixin:
    """Handlers for AI provider control, chat, code assistant, web search and dictation."""

    # ==================================================================
    # AI CONTROL
    # ==================================================================
    def cmd_switch_ai(self, cmd: str) -> str:
        logger.info(f"cmd_switch_ai: {cmd!r}")
        self.llm.switch_provider(cmd)
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
    # AI CHAT
    # ==================================================================
    def cmd_ai_chat(self, cmd: str) -> str:
        logger.info(f"cmd_ai_chat: {cmd!r}")
        self.llm.process_chat(cmd)
        return "CONTINUE"

    # ==================================================================
    # CODE ASSISTANT
    # ==================================================================
    def cmd_code_assistant(self, cmd: str) -> str:
        logger.info(f"cmd_code_assistant: {cmd!r}")
        from core.utils import normalize_filename
        n_cmd = normalize_filename(cmd)
        match = re.search(r'([a-zA-Z0-9_\-\.]+\.[a-zA-Z0-9]{2,5})', n_cmd)
        if not match:
            self.audio.speak("Будь ласка, скажіть точну назву файлу з розширенням.")
            return "CONTINUE"
        # Use basename to prevent path traversal (e.g. "../../etc/passwd.py")
        filename = os.path.basename(match.group(1))
        if not filename:
            self.audio.speak("Назва файлу некоректна.")
            return "CONTINUE"
        search_dirs = [
            os.getcwd(),
            os.path.join(os.getenv("JARVIS_DRIVE", r"H:\Jarvis_Sorted"), "Code"),
        ]
        file_path = None
        for d in search_dirs:
            real_d = os.path.realpath(d)
            candidate = os.path.realpath(os.path.join(d, filename))
            if candidate.startswith(real_d + os.sep) and os.path.exists(candidate):
                file_path = candidate
                break
        if not file_path:
            self.audio.speak(f"Не можу знайти файл {filename}.")
            return "CONTINUE"
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()[:8000]
            if "виправ" in cmd:
                self.audio.speak("Виправляю архітектуру...")
                fixed = self.llm.generate_response(
                    prompt=(
                        "Виправ цей код. ПОВЕРНИ ТІЛЬКИ КОД без форматування markdown та коментарів:\n"
                        + content
                    ),
                    is_system_call=True,
                )
                if not fixed:
                    self.audio.speak("Нейромережа не змогла згенерувати виправлення.")
                    return "CONTINUE"
                base, ext = os.path.splitext(file_path)
                new_path  = f"{base}_fixed{ext}"
                with open(new_path, "w", encoding="utf-8") as f:
                    f.write(fixed.replace("```python", "").replace("```", "").strip())
                self.audio.speak(f"Код збережено у файл {os.path.basename(new_path)}.")
            else:
                self.audio.speak("Аналізую логіку...")
                prompt = (
                    f"Ти {self.llm.personality_mode} Senior Developer. "
                    f"Зроби короткий аудіт файлу {filename}. "
                    f"Поясни 2-3 реченнями що робить цей код і чи є явні недоліки.\n\nКод:\n{content}"
                )
                answer = self.llm.generate_response(prompt, is_system_call=True)
                if answer:
                    self.audio.speak(answer)
        except Exception as e:
            logger.error(f"Помилка при читанні файлу {filename}: {e}")
            self.audio.speak("Виникла системна помилка доступу до файлу.")
        return "CONTINUE"

    # ==================================================================
    # DICTATION
    # ==================================================================
    def cmd_dictation(self, cmd: str) -> str:
        logger.info(f"cmd_dictation: {cmd!r}")
        self.audio.speak("Режим диктування. Слухаю.")
        text = self.audio.listen_for_command(dictation_mode=True)
        if not text:
            self.audio.speak("Диктування скасовано.")
            return "CONTINUE"
        self.audio.speak("Обробляю...")
        try:
            summary = self.llm.generate_response(
                prompt=f"Зроби конспект цього тексту: {text}",
                is_system_call=True,
            )
            if summary:
                self.audio.speak("Конспект готовий:")
                self.audio.speak(summary)
        except Exception as e:
            logger.error(f"Помилка ШІ при диктуванні: {e}")
            self.audio.speak("Помилка обробки тексту.")
        return "CONTINUE"

    # ==================================================================
    # WEB SEARCH
    # ==================================================================
    def cmd_web_search(self, cmd: str) -> str:
        logger.info(f"cmd_web_search: {cmd!r}")
        search_triggers = config.TRIGGERS.get("web_search", [])
        query = strip_triggers(cmd, search_triggers)
        if not query:
            self.audio.speak("Що саме мені знайти?")
            return "CONTINUE"
        self.audio.speak(f"Шукаю в мережі інформацію про {query}...")
        try:
            results = self._web_search(query)
            if results:
                search_context = "\n".join(r['body'] for r in results)
                answer = self.llm.generate_response(
                    f"Користувач шукає: '{query}'.\n"
                    f"Ось що знайдено в мережі:\n{search_context}\n"
                    "Зроби чіткий інформативний підсумок українською мовою.",
                    is_system_call=True,
                )
                if answer:
                    self.audio.speak(answer)
                    threading.Thread(
                        target=self.brain.memorize,
                        args=(f"Довідка з мережі щодо '{query}': {answer}",),
                        kwargs={"source": "web_search"},
                        daemon=True,
                    ).start()
                    print(f"{Fore.MAGENTA}🧠 Знання завантажено у пам'ять (Web-RAG).")
                else:
                    self.audio.speak("Не вдалося сформувати відповідь.")
            else:
                self.audio.speak("На жаль, я нічого не знайшов за цим запитом.")
        except Exception as e:
            logger.error(f"Помилка пошуку: {e}", exc_info=True)
            self.audio.speak("Виникла проблема з доступом до інтернету.")
        return "CONTINUE"

    def _web_search(self, query: str) -> list:
        for attempt in range(3):
            try:
                with DDGS() as ddgs:
                    results = list(
                        ddgs.text(query, max_results=3, region="ua-uk", safesearch="off", backend="lite")
                    )
                if results:
                    logger.info(f"_web_search: знайдено {len(results)} результатів для {query!r}")
                    return results
                logger.warning(f"DDGS: порожній результат (спроба {attempt + 1})")
            except Exception as e:
                logger.warning(f"DDGS спроба {attempt + 1} невдала: {e}")
            time.sleep(1.5 * (attempt + 1))
        logger.warning(f"DDGS заблоковано — використовую Wikipedia для '{query}'")
        return self._wikipedia_search(query)

    def _wikipedia_search(self, query: str) -> list:
        try:
            params = {
                "action": "query", "list": "search", "srsearch": query,
                "srlimit": 3, "format": "json", "utf8": 1,
            }
            res = requests.get("https://uk.wikipedia.org/w/api.php", params=params, timeout=8)
            res.raise_for_status()
            hits = res.json().get("query", {}).get("search", [])
            if not hits:
                return []
            return [{"body": re.sub(r'<[^>]+>', '', h["snippet"]) + "..."} for h in hits]
        except Exception as e:
            logger.error(f"Wikipedia fallback помилка: {e}")
            return []
