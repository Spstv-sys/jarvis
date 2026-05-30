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
from core.file_watcher import DownloadHandler

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


class JarvisUltimate:

    def __init__(self):
        print(f"{Fore.CYAN}⚙️  Ініціалізація мультизадачних систем...")
        logger.info("=== ЗАПУСК JARVIS ULTIMATE ===")

        self.fuzzy_threshold = config.FUZZY_THRESHOLD
        self.is_running      = True
        self.ui_callback     = None

        # Паралельна ініціалізація важких підсистем — делеговано у core/bootstrap.
        from core.bootstrap import init_core_subsystems
        _subsystems = init_core_subsystems(timeout=30)
        self.db      = _subsystems["db"]
        self.brain   = _subsystems["brain"]
        self.scraper = _subsystems["scraper"]
        self.youtube = _subsystems["youtube"]

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

        # User Profile — стабільне самопредставлення «господаря» (ім'я,
        # форма звертання, мова, інтереси). Підмішується у системний промпт.
        try:
            from core.user_profile import get_user_profile
            self.user_profile = get_user_profile(self.db)
            logger.info(f"✅ UserProfile завантажено: {self.user_profile.all()}")
        except Exception as e:
            logger.error(f"UserProfile init failed: {e}")
            self.user_profile = None

        # Affect NLP scorer — підмінити лексикон на справжню sentiment-модель.
        # Завантажується у фоні; до готовності працює лексикон як fallback.
        # Вимикається через AFFECT_NLP=0.
        try:
            from core.affect_nlp import enable_nlp_scorer
            if enable_nlp_scorer():
                logger.info("✅ Affect NLP scorer активовано (фоновe завантаження).")
        except Exception as e:
            logger.debug(f"Affect NLP init skipped: {e}")

        # Відновлюємо знімок WorkingMemory з settings — щоб після рестарту
        # Нео пам'ятав топ-сутності й активну тему попередньої сесії.
        try:
            snap_raw = self.db.get_setting("working_memory_snapshot_v1")
            if isinstance(snap_raw, str) and snap_raw:
                if self.working_memory.from_json(snap_raw):
                    logger.info("✅ WorkingMemory snapshot відновлено")
        except Exception as e:
            logger.warning(f"WM snapshot restore failed: {e}")

        self.llm         = LLMHandler(self.db, self.brain, self.audio, working_memory=self.working_memory)
        # Передаємо UserProfile у LLM, якщо handler його підтримує.
        if self.user_profile is not None:
            try:
                self.llm.user_profile = self.user_profile
            except Exception:
                pass
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

        # Гарантовано зберігаємо знімок WorkingMemory перед завершенням,
        # навіть якщо процес впаде через _force_shutdown / SIGTERM.
        try:
            import atexit
            atexit.register(self._persist_working_memory)
        except Exception:
            pass

    def _persist_working_memory(self) -> None:
        """Скидає поточний стан WorkingMemory у settings-таблицю.

        Викликається з atexit та з _force_shutdown. Тихо ковтає помилки,
        бо це best-effort на shutdown — БД може вже закриватися.
        """
        try:
            wm = getattr(self, "working_memory", None)
            db = getattr(self, "db", None)
            if wm is None or db is None:
                return
            snap = wm.to_json()
            db.set_setting("working_memory_snapshot_v1", snap)
            logger.info("[WM] snapshot збережено")
        except Exception as e:
            logger.debug(f"[WM] persist на shutdown пропущено: {e}")

    # ------------------------------------------------------------------
    # SPEECH QUEUE
    # ------------------------------------------------------------------
    def enqueue_speech(self, text: str) -> None:
        if not text:
            return
        if self.ui_callback:
            self.ui_callback(getattr(config, "ASSISTANT_NAME", "Нео"), text)
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
        # Делеговано у core/runtime (розвантаження god-file).
        from core.runtime import request_cancel
        return request_cancel(self)

    def _force_shutdown(self) -> None:
        # Делеговано у core/runtime (розвантаження god-file).
        from core.runtime import force_shutdown
        return force_shutdown(self)

    def start_processing_loop(self) -> None:
        # Делеговано у core/runtime (розвантаження god-file).
        from core.runtime import processing_loop
        return processing_loop(self)

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
        # Делеговано у core/runtime (розвантаження god-file).
        from core.runtime import process_command
        return process_command(self, cmd)

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
        # Делеговано у core/skill_loader (розвантаження god-file).
        from core.skill_loader import load_skills as _load_skills
        self.dynamic_skills = _load_skills(
            self.skills_dir,
            getattr(config, "TRIGGERS", {}),
        )

        # Інвалідуємо кеш IntentRouter — інакше нові тригери / перейменовані
        # скіли не врахуються до закінчення 30-денного TTL запису.
        router = getattr(self, "intent_router", None)
        if router is not None and hasattr(router, "clear_cache"):
            try:
                router.clear_cache()
            except Exception as e:
                logger.warning(f"IntentRouter.clear_cache() не виконано: {e}")

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
        # Делеговано у core/command_router (розвантаження god-file).
        from core.command_router import dispatch
        return dispatch(self, cmd)

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
