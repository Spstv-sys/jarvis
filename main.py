import os
import sys
import time
import re
import threading
import queue
import logging
import shutil
from datetime import datetime, timedelta

import requests
import urllib.parse
import urllib.request
import webbrowser
import importlib.util
import glob

import config
from dotenv import load_dotenv
import psutil
import keyboard
import pyautogui
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from duckduckgo_search import DDGS
from colorama import init, Fore
from rapidfuzz import fuzz

from jarvis_media import JarvisYouTube
from jarvis_scraper import JarvisScraper

try:
    import pygame
    import pygame.mixer
except ImportError:
    pygame = None

try:
    from PIL import Image as PILImage
except ImportError:
    PILImage = None

from core.memory import JarvisDatabase
from core.memory_rag import JarvisSmartMemory
from core.audio_manager import AudioManager
from core.llm_handler import LLMHandler
from core.system_actions import SystemActions

init(autoreset=True)
load_dotenv()


logging.basicConfig(
    filename='jarvis_system.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [%(funcName)s] - %(message)s',
    encoding='utf-8'
)
logger = logging.getLogger(__name__)


class DownloadHandler(FileSystemEventHandler):
    EXT_MAP = {
        'Documents': ['.pdf', '.docx', '.txt', '.xlsx', '.pptx'],
        'Images':    ['.jpg', '.jpeg', '.png', '.svg'],
        'Code':      ['.py', '.kt', '.js', '.go', '.json', '.cpp', '.html', '.css'],
        'Archives':  ['.zip', '.rar', '.7z'],
        'Torrents':  ['.torrent'],
        'Programs':  ['.exe', '.msi'],
    }

    def on_created(self, event):
        if event.is_directory:
            return
        file_path    = event.src_path
        filename     = os.path.basename(file_path)
        file_ext     = os.path.splitext(filename)[1].lower()
        target_drive = os.getenv("JARVIS_DRIVE", r"H:\Jarvis_Sorted")

        for folder, extensions in self.EXT_MAP.items():
            if file_ext in extensions:
                dest_dir = os.path.join(target_drive, folder)
                os.makedirs(dest_dir, exist_ok=True)
                time.sleep(2)
                try:
                    shutil.move(file_path, os.path.join(dest_dir, filename))
                    logger.info(f"Файл {filename} відсортовано у {dest_dir}")
                except Exception as e:
                    logger.error(f"Помилка сортування файлу {filename}: {e}")
                break


class JarvisUltimate:

    def __init__(self):
        print(f"{Fore.CYAN}⚙️  Ініціалізація мультизадачних систем...")
        logger.info("=== ЗАПУСК JARVIS ULTIMATE ===")

        self.fuzzy_threshold = config.FUZZY_THRESHOLD
        self.is_running      = True
        self.ui_callback     = None

        self.db      = JarvisDatabase()
        self.brain   = JarvisSmartMemory(docs_dir="knowledge_base")
        self.scraper = JarvisScraper()
        self.youtube = JarvisYouTube()

        self.stop_event = threading.Event()
        keyboard.add_hotkey('esc', self.stop_event.set)
        self.audio = AudioManager(self.stop_event)

        self.input_queue  = queue.Queue()
        self.output_queue = queue.Queue()

        self._original_speak = self.audio.speak
        self.audio.speak     = self.enqueue_speech

        self.llm         = LLMHandler(self.db, self.brain, self.audio)
        self.sys_actions = SystemActions(self.audio, self.db)

        self.skills_dir     = os.path.join(os.getcwd(), "skills")
        os.makedirs(self.skills_dir, exist_ok=True)
        self.dynamic_skills = []
        self.load_skills()

        self.start_file_monitor()
        self.start_hardware_monitor()

    def enqueue_speech(self, text: str) -> None:
        if not text:
            return
        if self.ui_callback:
            self.ui_callback("Джарвіс", text)
        self.output_queue.put(text)

    def _clear_output_queue(self) -> None:
        while True:
            try:
                self.output_queue.get_nowait()
                self.output_queue.task_done()
            except queue.Empty:
                break

    def start_mouth(self) -> None:
        def mouth_loop():
            while self.is_running:
                try:
                    text = self.output_queue.get(timeout=1)
                    if text == "<CLEAR_QUEUE>":
                        self.audio.stop_event.set()
                        try:
                            if pygame and pygame.mixer.get_init():
                                pygame.mixer.music.stop()
                        except Exception:
                            pass
                        self._clear_output_queue()
                        time.sleep(0.3)
                        self.audio.stop_event.clear()
                    else:
                        self._original_speak(text)
                    self.output_queue.task_done()
                except queue.Empty:
                    continue
                except Exception as e:
                    logger.error(f"MouthThread: помилка озвучки: {e}")

        threading.Thread(target=mouth_loop, daemon=True, name="MouthThread").start()

    def start_ears(self) -> None:
        def ears_loop():
            while self.is_running:
                if not self.audio.listen_for_wake_word():
                    continue
                if self.ui_callback:
                    self.ui_callback("STATUS", "Слухаю вашу команду...")
                cmd = self.audio.listen_for_command()
                if not cmd:
                    continue
                if self.ui_callback:
                    self.ui_callback("Степан", cmd)
                if any(w in cmd for w in config.TRIGGERS["stop_voice"]):
                    print(f"{Fore.RED}🛑 Екстрене переривання голосу!")
                    self.output_queue.put("<CLEAR_QUEUE>")
                    if self.ui_callback:
                        self.ui_callback("STATUS", "Озвучку зупинено. Очікування...")
                else:
                    self.input_queue.put(cmd)
                    if self.ui_callback:
                        self.ui_callback("STATUS", "Нейромережа обробляє запит...")

        threading.Thread(target=ears_loop, daemon=True, name="EarsThread").start()

    def run(self) -> None:
        self.start_ears()
        self.start_mouth()

        self.audio.play_fx("startup")
        self.enqueue_speech(
            f"Всі системи в нормі. Працюю в режимі мультизадачності. "
            f"Поточний інтелект: {self.llm.active_provider}."
        )

        while self.is_running:
            try:
                cmd = self.input_queue.get(timeout=1)
                if cmd:
                    threading.Thread(
                        target=self._process_command,
                        args=(cmd,),
                        daemon=True,
                        name="BrainWorker"
                    ).start()
            except queue.Empty:
                pass
            except KeyboardInterrupt:
                break

        print(f"\n{Fore.RED}🛑 Зупинка системи.")
        sys.exit(0)

    def _process_command(self, cmd: str) -> None:
        try:
            status = self.execute_command(cmd)
            if status == "STOP":
                self.is_running = False
        except Exception as e:
            logger.error(f"BrainWorker: необроблена помилка: {e}", exc_info=True)
            self.audio.speak("Виникла системна помилка.")
        finally:
            self.input_queue.task_done()

    def fuzzy_in_command(self, command: str, trigger: str, threshold: int = None) -> bool:
        if threshold is None:
            threshold = self.fuzzy_threshold
        return fuzz.partial_ratio(command.lower(), trigger.lower()) >= threshold

    def _normalize_filename(self, text: str) -> str:
        result = text.replace(" крапка ", ".").replace(" точка ", ".")
        result = result.replace(" пай", ".py").replace(" джес", ".js").replace(" текст", ".txt")
        for ukr, eng in {"тест": "test", "мейн": "main", "скрипт": "script", "індекс": "index"}.items():
            result = result.replace(ukr, eng)
        return result

    @staticmethod
    def _strip_words(text: str, stop_words: list) -> str:
        for word in stop_words:
            text = re.sub(rf'\b{re.escape(word)}\b', '', text, flags=re.IGNORECASE)
        return re.sub(r'\s+', ' ', text).strip(" .")

    def start_file_monitor(self) -> None:
        download_path = os.path.join(os.path.expanduser('~'), 'Downloads')
        if not os.path.exists(download_path):
            return
        observer = Observer()
        observer.schedule(DownloadHandler(), download_path, recursive=False)
        observer.daemon = True
        observer.start()
        logger.info("Файловий моніторинг запущено.")

    def start_hardware_monitor(self) -> None:
        def monitor():
            while self.is_running:
                try:
                    battery = psutil.sensors_battery()
                    if battery and battery.percent <= 20 and not battery.power_plugged:
                        self.enqueue_speech(f"Увага. Заряд батареї {battery.percent} відсотків.")
                except Exception as e:
                    logger.error(f"Помилка моніторингу батареї: {e}")
                time.sleep(300)

        threading.Thread(target=monitor, daemon=True, name="BatteryMonitor").start()

    def cmd_update_knowledge(self, cmd: str) -> str:
        logger.info(f"cmd_update_knowledge: {cmd!r}")
        self.audio.speak("Починаю сканування документів та оновлення векторної бази знань. Це може зайняти деякий час.")
        try:
            self.brain.ingest_documents()
            self.audio.speak("Базу знань успішно оновлено.")
        except Exception as e:
            logger.error(f"Помилка оновлення бази знань: {e}")
            self.audio.speak("Виникла помилка під час індексації документів.")
        return "CONTINUE"

    def load_skills(self) -> None:
        self.dynamic_skills = []
        for file in glob.glob(os.path.join(self.skills_dir, "*.py")):
            module_name = os.path.basename(file)[:-3]
            try:
                sys.modules.pop(module_name, None)
                spec   = importlib.util.spec_from_file_location(module_name, file)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                if hasattr(module, 'TRIGGERS') and hasattr(module, 'execute'):
                    self.dynamic_skills.append({
                        'triggers': module.TRIGGERS,
                        'func':     module.execute,
                        'name':     module_name,
                    })
                    logger.info(f"Навичку завантажено: {module_name}")
            except Exception as e:
                logger.error(f"Помилка завантаження навички {module_name}: {e}")

        if self.dynamic_skills:
            print(f"{Fore.MAGENTA}🧠 Завантажено динамічних навичок: {len(self.dynamic_skills)}")

    def cmd_learn_skill(self, cmd: str) -> str:
        logger.info(f"cmd_learn_skill: {cmd!r}")
        STOP = ["створи навичку", "навчись", "напиши нову функцію", "щоб ти", "для"]
        request = self._strip_words(cmd, STOP)

        if not request:
            self.audio.speak("Чому саме я маю навчитися?")
            return "CONTINUE"

        self.audio.speak("Пишу код для нової навички. Це займе кілька секунд.")
        prompt = (
            f'Ти Senior Python Developer. Напиши плагін для голосового асистента Джарвіс.
'
            f'Користувач просить: "{request}".
'
            f'Правила:
'
            f'1. ПОВЕРНИ ТІЛЬКИ ЧИСТИЙ PYTHON КОД — без пояснень, без markdown, без коментарів.
'
            f'2. Код ПОВИНЕН мати список `TRIGGERS` — список українських фраз-тригерів.
'
            f'3. Код ПОВИНЕН мати функцію `def execute(cmd, jarvis):`, яка повертає РЯДОК.
'
            f'4. НЕ додавай ```python або ``` — тільки голий код.
'
            f'5. НЕ пиши жодних пояснень до або після коду.'
        )
        try:
            clean_code = self.llm.generate_response(prompt, is_system_call=True)
            if not clean_code:
                self.audio.speak("Нейромережа не змогла згенерувати навичку.")
                return "CONTINUE"
            clean_code = clean_code.replace("```python", "").replace("```", "").strip()
            filename   = f"skill_{int(time.time())}.py"
            filepath   = os.path.join(self.skills_dir, filename)
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(clean_code)
            self.load_skills()
            self.audio.speak(f"Нову навичку збережено у файл {filename} та інтегровано до системи.")
        except Exception as e:
            logger.error(f"Помилка створення навички: {e}")
            self.audio.speak("Виникла помилка під час програмування навички.")
        return "CONTINUE"

    def cmd_scrape_weather(self, cmd: str) -> str:
        logger.info(f"cmd_scrape_weather: {cmd!r}")
        self.audio.speak("Збираю повне метеозведення...")
        sinoptik = self.scraper.get_sinoptik_weather("суми")
        wttr     = self.scraper.get_weather_from_wttr("Sumy")
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

    def cmd_scrape_news(self, cmd: str) -> str:
        logger.info(f"cmd_scrape_news: {cmd!r}")
        self.audio.speak("Сканую стрічку Укрінформу...")
        result = self.scraper.get_ukrinform_news(limit=5)
        self.audio.speak(result)
        return "CONTINUE"

    def cmd_play_youtube(self, cmd: str) -> str:
        logger.info(f"cmd_play_youtube: {cmd!r}")
        STOP = ["увімкни", "вруби", "запусти пісню", "запусти відео", "грай", "включи", "на ютубі", "на youtube", "в ютубі", "в youtube", "джарвіс"]
        query = self._strip_words(cmd, STOP)
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
        STOP = ["знайди", "пошук", "що є на ютубі про", "на ютубі", "на youtube", "в ютубі", "в youtube", "в ютуб", "ютуб", "youtube", "відео", "про", "джарвіс"]
        query = self._strip_words(cmd, STOP)
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

    def cmd_finance(self, cmd: str) -> str:
        logger.info(f"cmd_finance: {cmd!r}")
        try:
            res = requests.get("https://api.monobank.ua/bank/currency", timeout=5)
            res.raise_for_status()
            data = res.json()
            usd  = next(i for i in data if i['currencyCodeA'] == 840 and i['currencyCodeB'] == 980)
            eur  = next(i for i in data if i['currencyCodeA'] == 978 and i['currencyCodeB'] == 980)
            self.audio.speak(f"Долар: {usd['rateBuy']} — {usd['rateSell']}. Євро: {eur['rateBuy']} — {eur['rateSell']}.")
        except StopIteration:
            self.audio.speak("Курс валют у відповіді банку не знайдено.")
        except Exception as e:
            logger.error(f"Помилка отримання курсу валют: {e}")
            self.audio.speak("Фінансові сервери не відповідають.")
        return "CONTINUE"

    def cmd_timer(self, cmd: str) -> str:
        logger.info(f"cmd_timer: {cmd!r}")
        return self.sys_actions.cmd_timer(cmd)

    def cmd_hardware_control(self, cmd: str) -> str:
        logger.info(f"cmd_hardware_control: {cmd!r}")
        return self.sys_actions.cmd_hardware_control(cmd)

    def cmd_apps_and_os(self, cmd: str) -> str:
        logger.info(f"cmd_apps_and_os: {cmd!r}")
        return self.sys_actions.cmd_apps_and_os(cmd)

    def cmd_system_exit(self, cmd: str) -> str:
        logger.info(f"cmd_system_exit: {cmd!r}")
        return self.sys_actions.cmd_system_exit(cmd)

    def cmd_stop_voice(self, cmd: str) -> str:
        logger.info(f"cmd_stop_voice: {cmd!r}")
        self.output_queue.put("<CLEAR_QUEUE>")
        return "CONTINUE"

    # FIX #3: доданий відсутній обробник find_file
    def cmd_find_file(self, cmd: str) -> str:
        logger.info(f"cmd_find_file: {cmd!r}")
        search_term = cmd.replace("знайди файл", "").strip()

        if not search_term:
            self.audio.speak("Назву якого файлу шукати?")
            return "CONTINUE"

        search_roots = [
            os.path.expanduser("~"),
            os.getenv("JARVIS_DRIVE", r"H:\Jarvis_Sorted"),
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

    def cmd_vision(self, cmd: str) -> str:
        logger.info(f"cmd_vision: {cmd!r}")
        self.audio.speak("Аналізую екран...")
        shot_path = "temp_screen.png"
        try:
            if not getattr(self.llm, 'gemini_client', None):
                self.audio.speak("Vision API недоступне. Активуйте Gemini.")
                return "CONTINUE"
            pyautogui.screenshot(shot_path)
            if PILImage is None:
                self.audio.speak("Бібліотека PIL не встановлена.")
                return "CONTINUE"
            img   = PILImage.open(shot_path)
            query = (
                cmd.replace("що на екрані", "").replace("проаналізуй монітор", "").replace("що ти бачиш", "").strip()
                or "Що на екрані? Відповідай коротко."
            )
            res = self.llm.gemini_client.models.generate_content(model='gemini-2.5-flash', contents=[img, query])
            self.audio.speak(res.text)
        except Exception as e:
            logger.error(f"Помилка Vision API: {e}")
            self.audio.speak("Не вдалося проаналізувати зображення.")
        finally:
            if os.path.exists(shot_path):
                os.remove(shot_path)
        return "CONTINUE"

    def cmd_code_assistant(self, cmd: str) -> str:
        logger.info(f"cmd_code_assistant: {cmd!r}")
        n_cmd = self._normalize_filename(cmd)
        match = re.search(r'([a-zA-Z0-9_\-\.]+\.[a-zA-Z0-9]{2,5})', n_cmd)
        if not match:
            self.audio.speak("Будь ласка, скажіть точну назву файлу з розширенням.")
            return "CONTINUE"
        filename    = match.group(1)
        search_dirs = [os.getcwd(), os.path.join(os.getenv("JARVIS_DRIVE", r"H:\Jarvis_Sorted"), "Code")]
        file_path = next((os.path.join(d, filename) for d in search_dirs if os.path.exists(os.path.join(d, filename))), None)
        if not file_path:
            self.audio.speak(f"Не можу знайти файл {filename}.")
            return "CONTINUE"
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()[:8000]
            if "виправ" in cmd:
                self.audio.speak("Виправляю архітектуру...")
                fixed = self.llm.generate_response(
                    prompt="Виправ цей код. ПОВЕРНИ ТІЛЬКИ КОД без форматування markdown та коментарів:\n" + content,
                    is_system_call=True
                )
                if not fixed:
                    self.audio.speak("Нейромережа не змогла згенерувати виправлення.")
                    return "CONTINUE"
                base, ext = os.path.splitext(file_path)
                new_path  = f"{base}_fixed{ext}"
                with open(new_path, 'w', encoding='utf-8') as f:
                    f.write(fixed.replace("```python", "").replace("```", "").strip())
                self.audio.speak(f"Код збережено у файл {os.path.basename(new_path)}.")
            else:
                self.audio.speak("Аналізую логіку...")
                prompt = f"Ти {self.llm.personality_mode} Senior Developer. Зроби короткий аудіт файлу {filename}. Поясни 2-3 реченнями що робить цей код і чи є явні недоліки.\n\nКод:\n{content}"
                answer = self.llm.generate_response(prompt, is_system_call=True)
                if answer:
                    self.audio.speak(answer)
        except Exception as e:
            logger.error(f"Помилка при читанні файлу {filename}: {e}")
            self.audio.speak("Виникла системна помилка доступу до файлу.")
        return "CONTINUE"

    def cmd_calendar(self, cmd: str) -> str:
        logger.info(f"cmd_calendar: {cmd!r}")
        date_str = datetime.now().strftime("%Y-%m-%d")
        if "завтра" in cmd:
            date_str = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        if "додай" in cmd or "заплануй" in cmd:
            task = cmd.replace("додай задачу", "").replace("заплануй", "").replace("на завтра", "").strip()
            self.db.add_task(date_str, task)
            self.audio.speak("Задачу внесено до реєстру.")
        else:
            tasks = self.db.get_tasks(date_str)
            self.audio.speak(f"Плани: {'. '.join(tasks)}." if tasks else "Розклад чистий.")
        return "CONTINUE"

    def cmd_dictation(self, cmd: str) -> str:
        logger.info(f"cmd_dictation: {cmd!r}")
        self.audio.speak("Режим диктування. Слухаю.")
        text = self.audio.listen_for_command(dictation_mode=True)
        if not text:
            self.audio.speak("Диктування скасовано.")
            return "CONTINUE"
        self.audio.speak("Обробляю...")
        try:
            summary = self.llm.generate_response(prompt=f"Зроби конспект цього тексту: {text}", is_system_call=True)
            if summary:
                self.audio.speak("Конспект готовий:")
                self.audio.speak(summary)
        except Exception as e:
            logger.error(f"Помилка ШІ при диктуванні: {e}")
            self.audio.speak("Помилка обробки тексту.")
        return "CONTINUE"

    def _web_search(self, query: str) -> list:
        for attempt in range(3):
            try:
                with DDGS() as ddgs:
                    results = list(ddgs.text(query, max_results=3, region="ua-uk", safesearch="off", backend="lite"))
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
            params = {"action": "query", "list": "search", "srsearch": query, "srlimit": 3, "format": "json", "utf8": 1}
            res = requests.get("https://uk.wikipedia.org/w/api.php", params=params, timeout=8)
            res.raise_for_status()
            hits = res.json().get("query", {}).get("search", [])
            if not hits:
                return []
            return [{"body": re.sub(r'<[^>]+>', '', h["snippet"]) + "..."} for h in hits]
        except Exception as e:
            logger.error(f"Wikipedia fallback помилка: {e}")
            return []

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

    def cmd_web_search(self, cmd: str) -> str:
        logger.info(f"cmd_web_search: {cmd!r}")
        search_triggers = config.TRIGGERS.get("web_search", [])
        query = cmd
        for t in search_triggers:
            query = re.sub(rf'\b{re.escape(t)}\b', '', query, flags=re.IGNORECASE)
        query = query.strip()
        if not query:
            self.audio.speak("Що саме мені знайти?")
            return "CONTINUE"
        self.audio.speak(f"Шукаю в мережі інформацію про {query}...")
        try:
            results = self._web_search(query)
            if results:
                search_context = "\n".join(r['body'] for r in results)
                answer = self.llm.generate_response(
                    f"Користувач шукає: '{query}'.\nОсь що знайдено в мережі:\n{search_context}\nЗроби чіткий інформативний підсумок українською мовою.",
                    is_system_call=True
                )
                if answer:
                    self.audio.speak(answer)
                    threading.Thread(target=self.brain.memorize, args=(f"Довідка з мережі щодо '{query}': {answer}",), kwargs={"source": "web_search"}, daemon=True).start()
                    print(f"{Fore.MAGENTA}🧠 Знання завантажено у пам'ять (Web-RAG).")
                else:
                    self.audio.speak("Не вдалося сформувати відповідь.")
            else:
                self.audio.speak("На жаль, я нічого не знайшов за цим запитом.")
        except Exception as e:
            logger.error(f"Помилка пошуку: {e}", exc_info=True)
            self.audio.speak("Виникла проблема з доступом до інтернету.")
        return "CONTINUE"

    def cmd_ai_chat(self, cmd: str) -> str:
        logger.info(f"cmd_ai_chat: {cmd!r}")
        self.llm.process_chat(cmd)
        return "CONTINUE"

    def handle_ai_chat(self, cmd: str) -> str:
        if re.search(r'\b[A-Za-z0-9_\-]+\.(py|txt|json|md|js|html|css|cpp|c|cs)\b', cmd):
            return self.cmd_code_assistant(cmd)
        return self.cmd_ai_chat(cmd)

    def execute_command(self, cmd: str) -> str:
        logger.info(f"Команда: {cmd}")

        for skill in self.dynamic_skills:
            if any(t in cmd for t in skill['triggers']):
                try:
                    result = skill['func'](cmd, self)
                    if result:
                        self.audio.speak(result)
                except Exception as e:
                    logger.error(f"Помилка навички {skill['name']}: {e}")
                return "CONTINUE"

        ROUTES = [
            ("update_knowledge", self.cmd_update_knowledge),
            ("learn_skill",      self.cmd_learn_skill),
            ("weather_exact",    self.cmd_scrape_weather),
            ("weather",          self.cmd_weather),
            ("youtube_play",     self.cmd_play_youtube),
            ("youtube_search",   self.cmd_search_youtube),
            ("news",             self.cmd_scrape_news),
            ("finance",          self.cmd_finance),
            ("switch_ai",        self.cmd_switch_ai),
            ("personality",      self.cmd_personality),
            ("check_ai",         self.cmd_check_ai),
            ("timer",            self.cmd_timer),
            ("hardware",         self.cmd_hardware_control),
            ("apps_os",          self.cmd_apps_and_os),
            ("vision",           self.cmd_vision),
            ("code_assistant",   self.cmd_code_assistant),
            ("calendar",         self.cmd_calendar),
            ("dictation",        self.cmd_dictation),
            ("system_exit",      self.cmd_system_exit),
            ("stop_voice",       self.cmd_stop_voice),
            ("memorize",         self.cmd_memorize),
            ("web_search",       self.cmd_web_search),
            ("find_file",        self.cmd_find_file),  # FIX #3
        ]

        for key, handler in ROUTES:
            triggers = config.TRIGGERS.get(key, [])
            for trigger in sorted(triggers, key=len, reverse=True):
                if trigger in cmd:
                    logger.info(f"execute_command: маршрут '{key}' для команди {cmd!r}")
                    return handler(cmd)

        logger.info(f"execute_command: маршрут не знайдено, передаю в handle_ai_chat: {cmd!r}")
        return self.handle_ai_chat(cmd)


if __name__ == "__main__":
    try:
        jarvis = JarvisUltimate()
        jarvis.run()
    except KeyboardInterrupt:
        print(f"\n{Fore.RED}🛑 Екстрена зупинка системи.")
        sys.exit(0)