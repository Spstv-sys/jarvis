"""
CommandHandlers — all cmd_* methods extracted from JarvisUltimate.

Receives the JarvisUltimate instance as ``jarvis`` for dependency access.
This keeps JarvisUltimate as a thin orchestrator (init + routing + lifecycle).
"""
import os
import re
import time
import logging
import threading
from datetime import datetime, timedelta

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


# ---------------------------------------------------------------------------
# Forbidden patterns for LLM-generated skill code (Problem 7)
# ---------------------------------------------------------------------------
_FORBIDDEN_SKILL_PATTERNS = [
    r'\bos\.system\b',
    r'\bsubprocess\b',
    r'\b__import__\b',
    r'\beval\b',
    r'\bexec\b',
]


def _check_skill_safety(code: str) -> tuple[bool, str]:
    """Return (True, '') if code is safe, (False, matched_pattern) otherwise."""
    for pattern in _FORBIDDEN_SKILL_PATTERNS:
        if re.search(pattern, code):
            return False, pattern
    return True, ""


class CommandHandlers:
    def __init__(self, jarvis) -> None:
        self.jarvis = jarvis

    # ------------------------------------------------------------------
    # Convenience accessors
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
    # KNOWLEDGE
    # ==================================================================
    def cmd_update_knowledge(self, cmd: str) -> str:
        logger.info(f"cmd_update_knowledge: {cmd!r}")
        self.audio.speak(
            "Починаю сканування документів та оновлення векторної бази знань. "
            "Це може зайняти деякий час."
        )
        try:
            self.brain.ingest_documents()
            self.audio.speak("Базу знань успішно оновлено.")
        except Exception as e:
            logger.error(f"Помилка оновлення бази знань: {e}")
            self.audio.speak("Виникла помилка під час індексації документів.")
        return "CONTINUE"

    # ==================================================================
    # SKILLS
    # ==================================================================
    def cmd_learn_skill(self, cmd: str) -> str:
        logger.info(f"cmd_learn_skill: {cmd!r}")
        request = strip_triggers(cmd, config.STOP_WORDS_LEARN_SKILL)

        if not request:
            self.audio.speak("Чому саме я маю навчитися?")
            return "CONTINUE"

        self.audio.speak("Пишу код для нової навички. Це займе кілька секунд.")
        prompt = (
            "Ти Senior Python Developer. Напиши плагін для голосового асистента Джарвіс.\n"
            f'Користувач просить: "{request}".\n'
            "Правила:\n"
            "1. ПОВЕРНИ ТІЛЬКИ ЧИСТИЙ PYTHON КОД — без пояснень, без markdown, без коментарів.\n"
            "2. Код ПОВИНЕН мати список `TRIGGERS` — список українських фраз-тригерів.\n"
            "3. Код ПОВИНЕН мати функцію `def execute(cmd, jarvis):`, яка повертає РЯДОК.\n"
            "4. НЕ додавай ```python або ``` — тільки голий код.\n"
            "5. НЕ пиши жодних пояснень до або після коду."
        )
        try:
            clean_code = self.llm.generate_response(prompt, is_system_call=True)
            if not clean_code:
                self.audio.speak("Нейромережа не змогла згенерувати навичку.")
                return "CONTINUE"

            clean_code = clean_code.replace("```python", "").replace("```", "").strip()

            safe, bad_pattern = _check_skill_safety(clean_code)
            if not safe:
                logger.warning(f"cmd_learn_skill: небезпечний патерн {bad_pattern!r} у згенерованому коді")
                self.audio.speak(
                    "Згенерований код містить небезпечні виклики та не може бути збережений."
                )
                return "CONTINUE"

            filename = f"skill_{int(time.time())}.py"
            pending_dir = os.path.join(self.jarvis.skills_dir, "pending")
            os.makedirs(pending_dir, exist_ok=True)
            filepath = os.path.join(pending_dir, filename)

            with open(filepath, "w", encoding="utf-8") as f:
                f.write(clean_code)

            logger.info(f"cmd_learn_skill: навичка збережена у pending: {filename}")
            self.audio.speak(
                f"Нову навичку збережено у файл {filename} у папку перевірки. "
                f"Скажіть 'активуй навичку {filename}', щоб інтегрувати її до системи."
            )
        except Exception as e:
            logger.error(f"Помилка створення навички: {e}")
            self.audio.speak("Виникла помилка під час програмування навички.")
        return "CONTINUE"

    def cmd_activate_skill(self, cmd: str) -> str:
        logger.info(f"cmd_activate_skill: {cmd!r}")
        pending_dir = os.path.join(self.jarvis.skills_dir, "pending")

        name = strip_triggers(cmd, config.TRIGGERS.get("activate_skill", []))
        name = name.strip()

        if not name:
            pending_files = [
                f for f in os.listdir(pending_dir) if f.endswith(".py")
            ] if os.path.exists(pending_dir) else []
            if not pending_files:
                self.audio.speak("Немає навичок, що очікують активації.")
            else:
                self.audio.speak(
                    f"Наступні навички очікують активації: {', '.join(pending_files)}."
                )
            return "CONTINUE"

        if not name.endswith(".py"):
            name += ".py"

        src = os.path.join(pending_dir, name)
        dst = os.path.join(self.jarvis.skills_dir, name)

        if not os.path.exists(src):
            self.audio.speak(f"Навичку {name} не знайдено у папці перевірки.")
            return "CONTINUE"

        try:
            import shutil
            shutil.move(src, dst)
            self.jarvis.load_skills()
            self.audio.speak(f"Навичку {name} активовано та інтегровано до системи.")
            logger.info(f"cmd_activate_skill: навичку активовано: {name}")
        except Exception as e:
            logger.error(f"cmd_activate_skill: помилка активації {name}: {e}")
            self.audio.speak("Виникла помилка під час активації навички.")
        return "CONTINUE"

    # ==================================================================
    # WEATHER
    # ==================================================================
    def cmd_scrape_weather(self, cmd: str) -> str:
        logger.info(f"cmd_scrape_weather: {cmd!r}")
        self.audio.speak("Збираю повне метеозведення...")
        sinoptik = self.scraper.get_sinoptik_weather(config.DEFAULT_CITY)
        wttr     = self.scraper.get_weather_from_wttr(config.DEFAULT_CITY_EN)
        if "недоступна" in sinoptik and "недоступна" in wttr:
            self.audio.speak("Всі метеосервери наразі недоступні. Спробуйте пізніше.")
            return "CONTINUE"
        if "недоступна" in sinoptik or "помилку" in sinoptik:
            self.audio.speak(wttr)
            return "CONTINUE"
        pressure = wind = humidity = None
        if "недоступна" not in wttr:
            p = re.search(r'тиск:\s*(.+?)\.', wttr)
            w = re.search(r'Вітер:\s*(.+?)\.', wttr)
            h = re.search(r'Вологість:\s*(.+?)\.', wttr)
            if p: pressure = p.group(1).strip()
            if w: wind     = w.group(1).strip()
            if h: humidity = h.group(1).strip()
        parts = [sinoptik.rstrip('.')]
        if pressure: parts.append(f"Атмосферний тиск: {pressure}.")
        if wind:     parts.append(f"Швидкість вітру: {wind}.")
        if humidity: parts.append(f"Відносна вологість: {humidity}.")
        self.audio.speak(" ".join(parts))
        return "CONTINUE"

    def cmd_weather(self, cmd: str) -> str:
        logger.info(f"cmd_weather: {cmd!r}")
        self.audio.speak("Запитую метеозведення...")
        report = self.scraper.get_weather_from_wttr("Sumy")
        self.audio.speak(report)
        return "CONTINUE"

    # ==================================================================
    # NEWS
    # ==================================================================
    def cmd_scrape_news(self, cmd: str) -> str:
        logger.info(f"cmd_scrape_news: {cmd!r}")
        self.audio.speak("Сканую стрічку Укрінформу...")
        result = self.scraper.get_ukrinform_news(limit=5)
        self.audio.speak(result)
        return "CONTINUE"

    # ==================================================================
    # YOUTUBE
    # ==================================================================
    def cmd_play_youtube(self, cmd: str) -> str:
        logger.info(f"cmd_play_youtube: {cmd!r}")
        query = strip_triggers(cmd, config.STOP_WORDS_YOUTUBE_PLAY)
        if not query:
            self.audio.speak("Що саме мені увімкнути?")
            return "CONTINUE"
        self.audio.speak(f"Виконую. Запускаю {query} в браузері.")
        return self._open_youtube_video(query)

    def _open_youtube_video(self, query: str) -> str:
        try:
            success = self.youtube.play_video(query)
            if not success:
                self.audio.speak("На жаль, за цим запитом нічого не знайдено.")
        except Exception as e:
            logger.error(f"Помилка доступу до YouTube: {e}")
            self.audio.speak("Виникла технічна помилка доступу до відеоплеєра.")
        return "CONTINUE"

    def cmd_search_youtube(self, cmd: str) -> str:
        logger.info(f"cmd_search_youtube: {cmd!r}")
        query = strip_triggers(cmd, config.STOP_WORDS_YOUTUBE_SEARCH)
        if not query:
            self.audio.speak("Яку тему відео мені знайти?")
            return "CONTINUE"
        self.audio.speak(f"Шукаю на YouTube: {query}...")
        try:
            raw_data = self.youtube.search_videos(query)
            if not raw_data or "помилка" in raw_data.lower():
                self.audio.speak("На жаль, за цим запитом нічого не знайдено.")
            else:
                self.audio.speak(raw_data)
        except Exception as e:
            logger.error(f"Помилка YouTube пошуку: {e}")
            self.audio.speak("Виникла помилка доступу до YouTube.")
        return "CONTINUE"

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

    # ==================================================================
    # AI CHAT
    # ==================================================================
    def cmd_ai_chat(self, cmd: str) -> str:
        logger.info(f"cmd_ai_chat: {cmd!r}")
        self.llm.process_chat(cmd)
        return "CONTINUE"
