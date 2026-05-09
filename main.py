import os
import sys
import time
import re
import threading
import queue
import logging
import logging.handlers
import shutil
from concurrent.futures import ThreadPoolExecutor
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


logging.basicConfig(level=logging.INFO)   # set root level first
_log_handler = logging.handlers.RotatingFileHandler(
    'jarvis_system.log',
    maxBytes=5 * 1024 * 1024,  # 5 MB
    backupCount=3,
    encoding='utf-8',
)
_log_handler.setFormatter(
    logging.Formatter('%(asctime)s - %(levelname)s - [%(funcName)s] - %(message)s')
)
logging.getLogger().handlers = [_log_handler]   # replace with rotating handler
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

        # Parallelise heavy I/O initialisation (SQLite, ChromaDB, embeddings,
        # network sessions). Each component is independent, so this saves 3-5s
        # of startup time vs. sequential init.
        init_results: dict = {}
        init_errors:  dict = {}

        def _safe_init(name, factory):
            try:
                init_results[name] = factory()
            except Exception as exc:
                init_errors[name] = exc
                logger.error(f"Init '{name}' failed: {exc}")

        init_threads = [
            threading.Thread(target=_safe_init, args=("db", JarvisDatabase), daemon=True),
            threading.Thread(target=_safe_init, args=("brain", lambda: JarvisSmartMemory(docs_dir="knowledge_base")), daemon=True),
            threading.Thread(target=_safe_init, args=("scraper", JarvisScraper), daemon=True),
            threading.Thread(target=_safe_init, args=("youtube", JarvisYouTube), daemon=True),
        ]
        for t in init_threads:
            t.start()
        for t in init_threads:
            t.join(timeout=30)

        self.db      = init_results.get("db")      or JarvisDatabase()
        self.brain   = init_results.get("brain")   or JarvisSmartMemory(docs_dir="knowledge_base")
        self.scraper = init_results.get("scraper") or JarvisScraper()
        self.youtube = init_results.get("youtube") or JarvisYouTube()

        self.stop_event = threading.Event()
        keyboard.add_hotkey('esc', self.stop_event.set)

        # Cooperative cancellation flag for long-running skills (e.g. multi-stage
        # agent chain). Set by the "stop_voice" trigger via _request_cancel().
        # Skills SHOULD check ``jarvis.cancel_event.is_set()`` between expensive
        # steps and bail out early. Cleared automatically before each new command.
        self.cancel_event = threading.Event()

        # Bounded executor for command processing. Replaces unbounded
        # ``threading.Thread().start()`` per command — caps concurrency so
        # rapid-fire commands cannot exhaust threads/memory.
        self._brain_pool = ThreadPoolExecutor(
            max_workers=config.BRAIN_MAX_WORKERS,
            thread_name_prefix="BrainWorker",
        )

        self.input_queue  = queue.Queue()
        self.output_queue = queue.Queue()

        # DI: AudioManager routes speak() through enqueue_speech — no monkey-patching
        self.audio = AudioManager(self.stop_event, output_callback=self.enqueue_speech)

        # Working Memory — короткострокова свідомість (тема, активні сутності,
        # остання дія). Підмішується в промпти та доступна скілам.
        from core.working_memory import get_working_memory
        self.working_memory = get_working_memory()

        self.llm         = LLMHandler(self.db, self.brain, self.audio, working_memory=self.working_memory)
        self.sys_actions = SystemActions(self.audio, self.db)

        self.handlers = CommandHandlers(self)

        self.skills_dir = os.path.join(os.getcwd(), "skills")
        os.makedirs(self.skills_dir, exist_ok=True)
        os.makedirs(os.path.join(self.skills_dir, "pending"), exist_ok=True)
        self.dynamic_skills = []
        self.load_skills()

        # Intent Router — LLM-fallback для нечітких команд (опційно).
        self.intent_router = None
        if getattr(config, "INTENT_ROUTER_ENABLED", False):
            try:
                from core.intent_router import IntentRouter
                llm_call = None
                if getattr(config, "INTENT_ROUTER_LLM", False) and self.llm:
                    llm_call = lambda p: self.llm.generate_response(p, is_system_call=True)
                self.intent_router = IntentRouter(
                    triggers=config.TRIGGERS,
                    dynamic_skills=self.dynamic_skills,
                    llm_call=llm_call,
                    cache_db_path=getattr(config, "INTENT_ROUTER_DB", None),
                    fuzzy_match=self.fuzzy_in_command,
                )
                logger.info(f"✅ IntentRouter активовано: {self.intent_router.stats()}")
            except Exception as e:
                logger.error(f"IntentRouter init failed: {e}")
                self.intent_router = None

        # Episodic Memory — довготривалий журнал подій (Шар 5).
        self.episodic_memory = None
        if getattr(config, "EPISODIC_MEMORY_ENABLED", True):
            try:
                from core.episodic_memory import get_episodic_memory
                self.episodic_memory = get_episodic_memory()
                # Embedder підключиться лінь, коли brain прогріє memory_rag.
                if getattr(self, "brain", None) is not None and getattr(self.brain, "embeddings", None):
                    self.episodic_memory.attach_embedder(self.brain.embeddings)
                logger.info(f"✅ EpisodicMemory активовано: {self.episodic_memory.stats()}")
            except Exception as e:
                logger.error(f"EpisodicMemory init failed: {e}")
                self.episodic_memory = None

        self.start_file_monitor()
        self.start_hardware_monitor()
        self.start_library_watcher()

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
                # Спершу перевіряємо команди завершення — інакше будь-яка
                # фраза зі словом "стоп" (напр. "стоп джарвіс") буде
                # перехоплена як stop_voice і ніколи не дійде до system_exit.
                if any(self._trigger_in_cmd(t, cmd)
                       for t in config.TRIGGERS.get("system_exit", [])):
                    self.input_queue.put(cmd)
                    if self.ui_callback:
                        self.ui_callback("STATUS", "Завершення роботи...")
                elif any(w in cmd for w in config.TRIGGERS["stop_voice"]):
                    print(f"{Fore.RED}🛑 Екстрене переривання голосу!")
                    self._request_cancel()
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
    def _request_cancel(self) -> None:
        """Signal cooperative cancellation to all running BrainWorkers.

        Long-running skills check :attr:`cancel_event` between stages and
        terminate early. The flag is auto-cleared by :meth:`_process_command`
        before each new command.
        """
        self.cancel_event.set()
        logger.info("Cancellation requested — running skills should bail out.")

    def _force_shutdown(self) -> None:
        """Hard-terminate the whole process after a STOP command.

        ``JarvisUltimate`` зазвичай живе у daemon-потоці (server.py, ui/hub.py),
        тому вихід з ``start_processing_loop`` сам по собі не закриває
        uvicorn / Tk mainloop. Ця функція дає короткий час на завершення
        поточної озвучки, потім форсує вихід процесу через ``os._exit``.
        """
        self.is_running = False
        logger.info("Force shutdown ініційовано після команди STOP.")

        def _kill():
            # Дочекатись, поки MouthThread доспіває останню репліку.
            try:
                deadline = time.time() + 5.0
                while time.time() < deadline:
                    pending = getattr(self, "output_queue", None)
                    if pending is None or pending.empty():
                        break
                    time.sleep(0.1)
            except Exception:
                pass
            try:
                self._brain_pool.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
            logging.shutdown()
            os._exit(0)

        threading.Thread(target=_kill, daemon=True, name="ShutdownThread").start()

    def start_processing_loop(self) -> None:
        """Main command-processing loop.

        Pulls commands from ``input_queue`` and submits each one to a bounded
        thread pool (:attr:`_brain_pool`). Concurrency is capped by
        ``config.BRAIN_MAX_WORKERS`` to prevent thread/memory exhaustion under
        rapid command bursts. Runs until :attr:`is_running` is False.
        """
        while self.is_running:
            try:
                cmd = self.input_queue.get(timeout=1)
                if cmd:
                    self._brain_pool.submit(self._process_command, cmd)
            except queue.Empty:
                pass
            except KeyboardInterrupt:
                break

        # Drain pool on shutdown (do not wait forever — daemon threads will die
        # with the process if a worker is stuck on a network call).
        try:
            self._brain_pool.shutdown(wait=True, cancel_futures=True)
        except Exception as exc:
            logger.error(f"BrainPool shutdown error: {exc}")

    def run(self) -> None:
        self.start_ears()
        self.start_mouth()

        if not self.audio.mic_available:
            print(f"{Fore.YELLOW}⚠️  Мікрофон не знайдено. Доступний лише текстовий режим.")
            logger.warning("Запуск без мікрофону — голосовий ввід вимкнено.")

        self.audio.play_fx("startup")
        self.enqueue_speech(
            f"Всі системи в нормі. Працюю в режимі мультизадачності. "
            f"Поточний інтелект: {self.llm.active_provider}."
        )

        self.start_processing_loop()
        print(f"\n{Fore.RED}🛑 Зупинка системи.")
        sys.exit(0)

    def _process_command(self, cmd: str) -> None:
        # Reset cancellation flag for each new command so a previous cancel
        # request does not silently abort an unrelated next command.
        self.cancel_event.clear()

        # Watchdog: log a warning if the command exceeds the configured
        # timeout. We cannot safely kill a Python thread, but a visible warning
        # tells the operator that a skill is hung (e.g. LLM provider stalled).
        timeout = config.BRAIN_COMMAND_TIMEOUT
        cmd_preview = cmd if len(cmd) <= 80 else cmd[:77] + "..."

        def _watchdog():
            logger.warning(
                "BrainWorker watchdog: command %r still running after %.0fs. "
                "Possible hang in LLM/skill — consider switching provider or "
                "issuing a stop command.",
                cmd_preview, timeout,
            )

        watchdog = threading.Timer(timeout, _watchdog)
        watchdog.daemon = True
        watchdog.start()

        status = None
        err: Exception | None = None
        try:
            status = self.execute_command(cmd)
            if status == "STOP":
                self.is_running = False
        except Exception as e:
            err = e
            logger.error(f"BrainWorker: необроблена помилка: {e}", exc_info=True)
            self.audio.speak("Виникла системна помилка.")
        finally:
            watchdog.cancel()
            self.input_queue.task_done()
            # Episodic Memory: автологування команди користувача.
            if self.episodic_memory is not None:
                try:
                    if err is None:
                        self.episodic_memory.log(
                            kind="command", text=cmd, actor="user",
                            meta={"status": status or "ok"},
                        )
                    else:
                        self.episodic_memory.log(
                            kind="error", text=cmd, actor="user",
                            tags=["exception"], meta={"error": str(err)[:300]},
                        )
                except Exception as exc:
                    logger.debug(f"episodic log failed: {exc}")

            # ⛔ STOP: гарантоване завершення процесу.
            # У режимі server.py / UI hub.py JarvisUltimate працює всередині
            # daemon-потоку; вихід з start_processing_loop НЕ зупиняє uvicorn
            # чи Tk mainloop. Тому після STOP ми форсуємо вихід процесу,
            # давши короткий час на догравання поточної озвучки.
            if status == "STOP":
                self._force_shutdown()

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
            self._warn_on_skill_trigger_collisions()

    def _warn_on_skill_trigger_collisions(self) -> None:
        """Warn when a dynamic skill trigger collides with a built-in route trigger.

        Dynamic skills are dispatched before built-in routes in
        :meth:`execute_command`, so an overly broad trigger (e.g. "погода")
        in a user-generated plugin can silently shadow core functionality.
        Logging this on load makes the conflict visible.
        """
        builtin_triggers: dict[str, str] = {}
        for route_key, trigger_list in config.TRIGGERS.items():
            for trig in trigger_list:
                builtin_triggers[trig.lower()] = route_key

        for skill in self.dynamic_skills:
            for trig in skill['triggers']:
                shadowed = builtin_triggers.get(trig.lower())
                if shadowed:
                    msg = (
                        f"Skill '{skill['name']}' trigger {trig!r} shadows "
                        f"built-in route '{shadowed}'. Built-in command will not run."
                    )
                    logger.warning(msg)
                    print(f"{Fore.YELLOW}⚠ {msg}")

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

    def start_library_watcher(self) -> None:
        """Авто-індексація нових книг у LITERATURE_DIR.

        Активується через env LIBRARY_WATCH=1 (за замовчуванням вимкнено,
        щоб не сканувати диск без потреби)."""
        if os.getenv("LIBRARY_WATCH", "0") not in ("1", "true", "True"):
            return
        try:
            from core.library_watcher import LibraryWatcher
            self._library_watcher = LibraryWatcher(self)
            self._library_watcher.start()
        except Exception as e:
            logger.error(f"Не вдалося запустити LibraryWatcher: {e}")

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

        # Dynamic skills take priority.
        # For multi-word triggers we also use fuzzy matching so that minor STT
        # transcription errors (e.g. "агентські" → "агентскі") still fire.
        cmd_lower = cmd.lower()
        for skill in self.dynamic_skills:
            matched = False
            for t in skill['triggers']:
                # 1) fast substring check (handles most cases)
                if t.lower() in cmd_lower:
                    matched = True
                    break
                # 2) word-boundary regex (handles trailing punctuation etc.)
                if self._trigger_in_cmd(t, cmd):
                    matched = True
                    break
                # 3) fuzzy fallback for multi-word triggers (handles STT errors)
                if len(t.split()) >= 2 and self.fuzzy_in_command(cmd, t, threshold=82):
                    matched = True
                    break
            if matched:
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
            # ВАЖЛИВО: system_exit має перевірятися РАНІШЕ за stop_voice,
            # бо тригер "стоп" зі stop_voice матчить будь-яку фразу зі словом
            # "стоп" (напр. "нео стоп") і поверне CONTINUE замість завершення.
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
                # Fuzzy fallback для багатослівних тригерів (STT-помилки).
                # Вимагаємо щоб і команда була багатослівною — інакше короткі
                # репліки на кшталт 'стоп' хибно матчать 'стоп система'.
                if (len(trigger.split()) >= 2
                        and len(cmd.split()) >= 2
                        and self.fuzzy_in_command(cmd, trigger, threshold=80)):
                    logger.info(f"execute_command: fuzzy маршрут '{key}' для команди {cmd!r} (тригер='{trigger}')")
                    return handler(cmd)

        # ФОЛБЕК: якщо жоден тригер не зматчив и IntentRouter вмикнено —
        # питаємо LLM, який маршрут обрати. При впевненості → викликаємо відповідний
        # handler в ROUTES; інакше — розмова.
        if self.intent_router is not None:
            try:
                guess = self.intent_router.classify(cmd)
                logger.info(f"execute_command: IntentRouter → {guess}")
                if (guess.name in dict(ROUTES)
                        and guess.confidence >= getattr(config, "INTENT_ROUTER_MIN_CONF", 0.55)):
                    handler = dict(ROUTES)[guess.name]
                    logger.info(f"execute_command: LLM-маршрут '{guess.name}' (conf={guess.confidence:.2f}, src={guess.source})")
                    return handler(cmd)
            except Exception as e:
                logger.warning(f"IntentRouter помилка: {e}")

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
