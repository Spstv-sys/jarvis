"""Черга подій Jarvis → WebSocket і фонові broadcaster-и.

Підписники когнітивних підсистем (ThoughtLog, GoalStack, Reflector,
Anticipator) кладуть події у :data:`webapp.state.event_queue`, а
:func:`event_broadcaster` розсилає їх усім WS-клієнтам. Окремі таски
розсилають живий когнітивний снепшот і метрики системи.
"""
from __future__ import annotations

import asyncio
import logging
import queue

import psutil

from webapp import state

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Колбеки / підписники
# ---------------------------------------------------------------------------
def ui_callback(sender: str, text: str) -> None:
    """Викликається з потоків Jarvis — кладе подію в чергу."""
    state.event_queue.put({"sender": sender, "text": text})


def _thought_to_event(t) -> dict:
    """Серіалізує Thought в dict для WS-broadcast'у."""
    return {
        "_kind": "thought",
        "ts":    t.ts,
        "stage": t.stage,
        "text":  t.text,
        "meta":  t.meta,
    }


def _on_thought(t) -> None:
    """Підписник ThoughtLog — публікує думку в чергу подій."""
    try:
        state.event_queue.put(_thought_to_event(t))
    except Exception:
        pass


def _on_goal_stack(snap: dict) -> None:
    """Підписник GoalStack — публікує snapshot стеку."""
    try:
        state.event_queue.put({"_kind": "goal_stack", **snap})
    except Exception:
        pass


def _on_reflection(snap: dict) -> None:
    """Підписник Reflector — публікує оновлені уроки."""
    try:
        state.event_queue.put({"_kind": "reflection", **snap})
    except Exception:
        pass


def _on_anticipator(snap: dict) -> None:
    """Підписник Anticipator — публікує проактивні пропозиції."""
    try:
        state.event_queue.put({"_kind": "anticipator", **snap})
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Broadcaster-и
# ---------------------------------------------------------------------------
async def event_broadcaster() -> None:
    """Фоновий таск — розсилає події всім клієнтам."""
    loop = asyncio.get_running_loop()
    while True:
        try:
            msg = await loop.run_in_executor(
                None,
                lambda: state.event_queue.get(timeout=0.05)
            )
            if msg.get("_kind") == "thought":
                msg.pop("_kind", None)
                await state.manager.broadcast({"type": "thought", **msg})
            elif msg.get("_kind") == "goal_stack":
                msg.pop("_kind", None)
                await state.manager.broadcast({"type": "goal_stack", **msg})
            elif msg.get("_kind") == "reflection":
                msg.pop("_kind", None)
                await state.manager.broadcast({"type": "reflection", **msg})
            elif msg.get("_kind") == "anticipator":
                msg.pop("_kind", None)
                await state.manager.broadcast({"type": "anticipator", **msg})
            else:
                await state.manager.broadcast({"type": "message", **msg})
        except queue.Empty:
            pass
        except Exception:
            await asyncio.sleep(0.1)


async def cognition_broadcaster() -> None:
    """Live snapshot WM + intent stats — раз на 2 с (тільки якщо є зміни)."""
    last_sig = None
    while True:
        await asyncio.sleep(2.0)
        if not state.jarvis_ready.is_set() or state.jarvis is None:
            continue
        try:
            wm = getattr(state.jarvis, "working_memory", None)
            ctx = wm.context_block() if wm else ""
            ents = []
            if wm is not None:
                try:
                    ents = wm.entity_snapshot(limit=8)
                except Exception:
                    ents = []
            ir = getattr(state.jarvis, "intent_router", None)
            ir_stats = ir.stats() if ir else {}
            sig = (ctx, len(ents), tuple(sorted(ir_stats.items())) if ir_stats else ())
            if sig == last_sig:
                continue
            last_sig = sig
            await state.manager.broadcast({
                "type": "cognition",
                "wm_context": ctx,
                "wm_entities": ents,
                "intent_stats": ir_stats,
            })
        except Exception:
            pass


async def metrics_broadcaster() -> None:
    """Метрики системи — раз на секунду."""
    # Кеш cpu_percent: перший виклик interval=None повертає 0.0,
    # подальші — середнє від останнього виклику. Оскільки ми створюємо
    # броадкаст раз на секунду, це OK. Просто виносимо виклики всіх сенсорів
    # у thread pool, щоб не блокувати event loop.
    loop = asyncio.get_running_loop()

    def _gather_metrics():
        battery = psutil.sensors_battery()
        return {
            "cpu":     psutil.cpu_percent(interval=None),
            "ram":     psutil.virtual_memory().percent,
            "battery": battery.percent if battery else None,
            "plugged": battery.power_plugged if battery else None,
        }

    while True:
        if not state.jarvis_ready.is_set():
            await asyncio.sleep(1.0)
            continue
        metrics = await loop.run_in_executor(None, _gather_metrics)
        vol = getattr(state.jarvis.audio, "current_volume", 0) if state.jarvis else 0
        await state.manager.broadcast({
            "type":    "metrics",
            **metrics,
            "volume":  float(vol),
        })
        await asyncio.sleep(1.0)
