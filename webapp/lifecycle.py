"""Запуск Jarvis у фоновому потоці + FastAPI lifespan.

``start_jarvis`` ініціалізує важкий інстанс :class:`main.JarvisUltimate` поза
event-loop (у власному потоці) і виставляє ``state.jarvis_ready``. ``lifespan``
запускає фонові broadcaster-и та підписує сервер на когнітивні підсистеми.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

import config
from webapp import state
from webapp.events import (
    ui_callback,
    event_broadcaster,
    cognition_broadcaster,
    metrics_broadcaster,
    _on_thought,
    _on_goal_stack,
    _on_reflection,
    _on_anticipator,
)

logger = logging.getLogger(__name__)


def start_jarvis() -> None:
    """Ініціалізувати Jarvis у фоновому потоці та запустити цикли обробки."""
    try:
        state.jarvis = state.JarvisUltimate()
        state.jarvis.ui_callback = ui_callback
        state.jarvis.start_ears()
        state.jarvis.start_mouth()
        state.jarvis.audio.play_fx("startup")
        state.jarvis.enqueue_speech(
            f"Всі системи в нормі. Поточний інтелект: {state.jarvis.llm.active_provider}."
        )
        state.event_queue.put({"sender": "STATUS", "text": "SYSTEM ONLINE"})
        logger.info("SYSTEM ONLINE — Jarvis успішно запущено.")
    except Exception as e:
        state.event_queue.put({"sender": "STATUS", "text": f"INIT ERROR: {e}"})
        logger.error(f"INIT ERROR — Jarvis не вдалося запустити: {e}", exc_info=True)
    finally:
        state.jarvis_ready.set()

    if state.jarvis:
        state.jarvis.start_processing_loop()


@asynccontextmanager
async def lifespan(app: FastAPI):
    threading.Thread(target=start_jarvis, daemon=True).start()
    asyncio.create_task(event_broadcaster())
    asyncio.create_task(metrics_broadcaster())
    asyncio.create_task(cognition_broadcaster())
    # ThoughtLog (Шар 6) — підписуємо сервер на потік думок.
    try:
        from core.thought_log import get_thought_log
        get_thought_log().subscribe(_on_thought)
        logger.info("✅ ThoughtLog subscriber підключений до WebSocket broadcast.")
    except Exception as e:
        logger.warning(f"ThoughtLog subscribe failed: {e}")
    # GoalStack (Шар 6+) — візуалізація активних планів.
    try:
        from core.goal_stack import get_goal_stack
        get_goal_stack().subscribe(_on_goal_stack)
        logger.info("✅ GoalStack subscriber підключений до WebSocket broadcast.")
    except Exception as e:
        logger.warning(f"GoalStack subscribe failed: {e}")
    # Reflector (Шар 7) — метакогнітивні уроки.
    try:
        from core.reflector import get_reflector
        refl = get_reflector()
        refl.subscribe(_on_reflection)
        # Якщо в config задано REFLECTOR_INTERVAL_SEC > 0 — стартуємо фоновий цикл.
        try:
            interval = int(getattr(config, "REFLECTOR_INTERVAL_SEC", 0))
        except Exception:
            interval = 0
        if interval > 0:
            refl.start(interval_sec=interval)
        logger.info("✅ Reflector subscriber підключений до WebSocket broadcast.")
    except Exception as e:
        logger.warning(f"Reflector subscribe failed: {e}")
    # Anticipator (Шар 8) — проактивні пропозиції.
    try:
        from core.anticipator import get_anticipator
        ant = get_anticipator()
        ant.subscribe(_on_anticipator)
        try:
            a_interval = int(getattr(config, "ANTICIPATOR_INTERVAL_SEC", 0))
        except Exception:
            a_interval = 0
        if a_interval > 0:
            ant.start(interval_sec=a_interval)
        logger.info("✅ Anticipator subscriber підключений до WebSocket broadcast.")
    except Exception as e:
        logger.warning(f"Anticipator subscribe failed: {e}")
    yield
