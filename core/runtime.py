"""Runtime — цикл обробки команд і керування життєвим циклом процесу.

Винесено з main.py у межах рефакторингу (розвантаження god-file).
Чисті функції, що приймають інстанс ``jarvis`` першим аргументом і
повністю відтворюють семантику колишніх методів ``JarvisUltimate``:

  • ``request_cancel(jarvis)``     — кооперативне скасування для BrainWorker-ів;
  • ``force_shutdown(jarvis)``     — форсоване завершення процесу після STOP;
  • ``processing_loop(jarvis)``    — головний цикл: черга → пул потоків;
  • ``process_command(jarvis,cmd)``— обробка однієї команди + watchdog + журнал.

main.py лишає тонкі методи-обгортки, що делегують сюди, щоб зовнішні
виклики (server.py, ui/hub.py, ui/app_window.py, тести) працювали без змін.
Цикл і обробник звертаються до ``jarvis._process_command`` / ``jarvis._force_shutdown``
через інстанс — отже monkeypatch у тестах продовжує діяти.
"""
from __future__ import annotations

import os
import time
import queue
import logging
import threading
from typing import TYPE_CHECKING

import config

if TYPE_CHECKING:
    from core.context import JarvisContext

logger = logging.getLogger(__name__)


def request_cancel(jarvis: "JarvisContext") -> None:
    """Сигналізує кооперативне скасування всім активним BrainWorker-ам.

    Довгі скіли перевіряють :attr:`cancel_event` між етапами і завершуються
    раніше. Прапорець авто-очищується в :func:`process_command` перед кожною
    новою командою.
    """
    jarvis.cancel_event.set()
    logger.info("Cancellation requested — running skills should bail out.")


def force_shutdown(jarvis: "JarvisContext") -> None:
    """Жорстко завершує весь процес після команди STOP.

    ``JarvisUltimate`` зазвичай живе у daemon-потоці (server.py, ui/hub.py),
    тому вихід з ``processing_loop`` сам по собі не закриває uvicorn / Tk
    mainloop. Дає короткий час на завершення поточної озвучки, потім форсує
    вихід процесу через ``os._exit``.
    """
    jarvis.is_running = False
    logger.info("Force shutdown ініційовано після команди STOP.")

    def _kill():
        # Дочекатись, поки MouthThread доспіває останню репліку.
        try:
            deadline = time.time() + 5.0
            while time.time() < deadline:
                pending = getattr(jarvis, "output_queue", None)
                if pending is None or pending.empty():
                    break
                time.sleep(0.1)
        except Exception:
            pass
        # Зберегти WorkingMemory snapshot ДО того як БД закриється.
        try:
            jarvis._persist_working_memory()
        except Exception:
            pass
        try:
            jarvis._brain_pool.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        logging.shutdown()
        os._exit(0)

    threading.Thread(target=_kill, daemon=True, name="ShutdownThread").start()


def processing_loop(jarvis: "JarvisContext") -> None:
    """Головний цикл обробки команд.

    Витягує команди з ``input_queue`` і подає кожну в обмежений пул потоків
    (:attr:`_brain_pool`). Паралелізм обмежено ``config.BRAIN_MAX_WORKERS``,
    щоб уникнути вичерпання потоків/пам'яті при бурстах команд. Працює, доки
    :attr:`is_running` істинний.
    """
    while jarvis.is_running:
        try:
            cmd = jarvis.input_queue.get(timeout=1)
            if cmd:
                jarvis._brain_pool.submit(jarvis._process_command, cmd)
        except queue.Empty:
            pass
        except KeyboardInterrupt:
            break

    # Drain pool on shutdown (do not wait forever — daemon threads will die
    # with the process if a worker is stuck on a network call).
    try:
        jarvis._brain_pool.shutdown(wait=True, cancel_futures=True)
    except Exception as exc:
        logger.error(f"BrainPool shutdown error: {exc}")


def process_command(jarvis: "JarvisContext", cmd: str) -> None:
    """Обробляє одну команду: watchdog, виконання, журнал, STOP-завершення."""
    # Reset cancellation flag for each new command so a previous cancel
    # request does not silently abort an unrelated next command.
    jarvis.cancel_event.clear()

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
        status = jarvis.execute_command(cmd)
        if status == "STOP":
            jarvis.is_running = False
    except Exception as e:
        err = e
        logger.error(f"BrainWorker: необроблена помилка: {e}", exc_info=True)
        jarvis.audio.speak("Виникла системна помилка.")
    finally:
        watchdog.cancel()
        jarvis.input_queue.task_done()
        # Episodic Memory: автологування команди користувача.
        if jarvis.episodic_memory is not None:
            try:
                if err is None:
                    jarvis.episodic_memory.log(
                        kind="command", text=cmd, actor="user",
                        meta={"status": status or "ok"},
                    )
                else:
                    jarvis.episodic_memory.log(
                        kind="error", text=cmd, actor="user",
                        tags=["exception"], meta={"error": str(err)[:300]},
                    )
            except Exception as exc:
                logger.debug(f"episodic log failed: {exc}")

        # ⛔ STOP: гарантоване завершення процесу.
        # У режимі server.py / UI hub.py JarvisUltimate працює всередині
        # daemon-потоку; вихід з processing_loop НЕ зупиняє uvicorn
        # чи Tk mainloop. Тому після STOP ми форсуємо вихід процесу,
        # давши короткий час на догравання поточної озвучки.
        if status == "STOP":
            jarvis._force_shutdown()
