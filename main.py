import os
import sys
import time
import re
import threading
import queue
import logging
import shutil
from datetime import datetime

import config
from dotenv import load_dotenv
import psutil
import keyboard
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from colorama import init, Fore
from rapidfuzz import fuzz

try:
    import pygame
    import pygame.mixer
except ImportError:
    pygame = None

from jarvis_media import JarvisYouTube
from jarvis_scraper import JarvisScraper

from core.memory import JarvisDatabase
from core.memory_rag import JarvisSmartMemory
from core.audio_manager import AudioManager
from core.llm_handler import LLMHandler
from core.system_actions import SystemActions
from core.handlers import CommandHandlers

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

    def __init__(self):
        super().__init__()
        self.target_drive = config.SORTING_DRIVE

    def on_created(self, event):
        if event.is_directory:
            return
        file_path = event.src_path
        filename  = os.path.basename(file_path)
        file_ext  = os.path.splitext(filename)[1].lower()

        for folder, extensions in self.EXT_MAP.items():
            if file_ext in extensions:
                dest_dir = os.path.join(self.target_drive, folder)
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

        self.input_queue  = queue.Queue()
        self.output_queue = queue.Queue()

        # DI: AudioManager routes speak() through enqueue_speech — no monkey-patching
        self.audio = AudioManager(self.stop_event, output_callback=self.enqueue_speech)

        self.llm         = LLMHandler(self.db, self.brain, self.audio)
        self.sys_actions = SystemActions(self.audio, self.db)

        self.handlers = CommandHandlers(self)

        self.skills_dir = os.path.join(os.getcwd(), "skills")
        os.makedirs(self.skills_dir, exist_ok=True)
        os.makedirs(os.path.join(self.skills_dir, "pending"), exist_ok=True)
        self.dynamic_skills = []
        self.load_skills()

        self.start_file_monitor()
        self.start_hardware_monitor()

    # ------------------------------------------------------------------
    # SPEECH QUEUE
    # ------------------------------------------------------------------
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
                        self.audio.speak_tts(text)
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

    # ------------------------------------------------------------------
    # PROCESSING LOOP  (Problem 2: single implementation, used by run/server/hub)
    # ------------------------------------------------------------------
    def start_processing_loop(self) -> None:
        """Main command-processing loop.

        Pulls commands from ``input_queue`` and dispatches each one in a
        dedicated BrainWorker thread.  Runs until ``self.is_running`` is False.
        Call this method from any entry-point (CLI, server, UI hub).
        """
        while self.is_running:
            try:
                cmd = self.input_queue.get(timeout=1)
                if cmd:
                    threading.Thread(
                        target=self._process_command,
                        args=(cmd,),
                        daemon=True,
                        name="BrainWorker",
                    ).start()
            except queue.Empty:
                pass
            except KeyboardInterrupt:
                break

    def run(self) -> None:
        self.start_ears()
        self.start_mouth()

        self.audio.play_fx("startup")
        self.enqueue_speech(
            f"Всі системи в нормі. Працюю в режимі мультизадачності. "
            f"Поточний інтелект: {self.llm.active_provider}."
        )

        self.start_processing_loop()
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

    # ------------------------------------------------------------------
    # ROUTING HELPERS
    # ------------------------------------------------------------------
    def fuzzy_in_command(self, command: str, trigger: str, threshold: int = None) -> bool:
        if threshold is None:
            threshold = self.fuzzy_threshold
        return fuzz.partial_ratio(command.lower(), trigger.lower()) >= threshold

    @staticmethod
    def _trigger_in_cmd(trigger: str, cmd: str) -> bool:
        """Word-boundary–aware trigger matching.

        Uses ``(?<!\\w)`` / ``(?!\\w)`` to avoid false positives on substrings
        (e.g. trigger "погода" must not fire on a hypothetical compound word).
        """
        pattern = r'(?<!\w)' + re.escape(trigger) + r'(?!\w)'
        return bool(re.search(pattern, cmd, re.IGNORECASE))

    # ------------------------------------------------------------------
    # SKILLS
    # ------------------------------------------------------------------
    def load_skills(self) -> None:
        import glob as _glob
        import importlib.util
        self.dynamic_skills = []
        skills_glob = os.path.join(self.skills_dir, "*.py")
        for file in _glob.glob(skills_glob):
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

    # ------------------------------------------------------------------
    # BACKGROUND MONITORS
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # COMMAND DISPATCH
    # ------------------------------------------------------------------
    def handle_ai_chat(self, cmd: str) -> str:
        if re.search(r'\b[A-Za-z0-9_\-]+\.(py|txt|json|md|js|html|css|cpp|c|cs)\b', cmd):
            return self.handlers.cmd_code_assistant(cmd)
        return self.handlers.cmd_ai_chat(cmd)

    def execute_command(self, cmd: str) -> str:
        logger.info(f"Команда: {cmd}")

        # Dynamic skills take priority
        for skill in self.dynamic_skills:
            if any(self._trigger_in_cmd(t, cmd) for t in skill['triggers']):
                try:
                    result = skill['func'](cmd, self)
                    if result:
                        self.audio.speak(result)
                except Exception as e:
                    logger.error(f"Помилка навички {skill['name']}: {e}")
                return "CONTINUE"

        ROUTES = [
            ("update_knowledge", self.handlers.cmd_update_knowledge),
            ("activate_skill",   self.handlers.cmd_activate_skill),
            ("learn_skill",      self.handlers.cmd_learn_skill),
            ("weather_exact",    self.handlers.cmd_scrape_weather),
            ("weather",          self.handlers.cmd_weather),
            ("youtube_play",     self.handlers.cmd_play_youtube),
            ("youtube_search",   self.handlers.cmd_search_youtube),
            ("news",             self.handlers.cmd_scrape_news),
            ("finance",          self.handlers.cmd_finance),
            ("switch_ai",        self.handlers.cmd_switch_ai),
            ("personality",      self.handlers.cmd_personality),
            ("check_ai",         self.handlers.cmd_check_ai),
            ("timer",            self.handlers.cmd_timer),
            ("hardware",         self.handlers.cmd_hardware_control),
            ("apps_os",          self.handlers.cmd_apps_and_os),
            ("vision",           self.handlers.cmd_vision),
            ("code_assistant",   self.handlers.cmd_code_assistant),
            ("calendar",         self.handlers.cmd_calendar),
            ("dictation",        self.handlers.cmd_dictation),
            ("system_exit",      self.handlers.cmd_system_exit),
            ("stop_voice",       self.handlers.cmd_stop_voice),
            ("memorize",         self.handlers.cmd_memorize),
            ("web_search",       self.handlers.cmd_web_search),
            ("find_file",        self.handlers.cmd_find_file),
        ]

        for key, handler in ROUTES:
            triggers = config.TRIGGERS.get(key, [])
            for trigger in sorted(triggers, key=len, reverse=True):
                if self._trigger_in_cmd(trigger, cmd):
                    logger.info(f"execute_command: маршрут '{key}' для команди {cmd!r}")
                    return handler(cmd)

        logger.info(f"execute_command: маршрут не знайдено, передаю в handle_ai_chat: {cmd!r}")
        return self.handle_ai_chat(cmd)

    # ------------------------------------------------------------------
    # Backward-compatible thin wrappers (kept for external callers / tests)
    # ------------------------------------------------------------------
    def cmd_weather(self, cmd: str) -> str:
        return self.handlers.cmd_weather(cmd)

    def cmd_stop_voice(self, cmd: str) -> str:
        # Operates on output_queue directly — no delegation needed
        self.output_queue.put("<CLEAR_QUEUE>")
        return "CONTINUE"


if __name__ == "__main__":
    try:
        jarvis = JarvisUltimate()
        jarvis.run()
    except KeyboardInterrupt:
        print(f"\n{Fore.RED}🛑 Екстрена зупинка системи.")
        sys.exit(0)
