"""Маршрути Anticipator (Шар 8): пропозиції, скан, accept/dismiss."""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends

from webapp import state
from webapp.security import require_access

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(require_access)])


@router.get("/anticipator")
async def get_anticipator_snapshot():
    """Поточний стек проактивних пропозицій + статистика."""
    try:
        from core.anticipator import get_anticipator
        return get_anticipator().snapshot()
    except Exception as e:
        return {"error": str(e), "pending": [], "recent": [], "stats": {}}


@router.post("/anticipator/scan")
async def trigger_anticipator_scan():
    """Ручний запуск сканування; повертає нові пропозиції."""
    try:
        from core.anticipator import get_anticipator
        loop = asyncio.get_running_loop()
        new_sugs = await loop.run_in_executor(None, lambda: get_anticipator().scan_now())
        return {"new": [s.to_dict() for s in new_sugs], "count": len(new_sugs)}
    except Exception as e:
        return {"error": str(e), "new": []}


@router.post("/anticipator/{sid}/accept")
async def accept_suggestion(sid: str):
    """Користувач прийняв пропозицію. Якщо є command — виконати через jarvis."""
    try:
        from core.anticipator import get_anticipator
        sug = get_anticipator().accept(sid)
        if not sug:
            return {"ok": False, "reason": "not_found"}
        executed = False
        if sug.command and state.jarvis_ready.is_set() and state.jarvis is not None:
            try:
                state.jarvis.input_queue.put(sug.command)
                executed = True
            except Exception as e:
                logger.warning(f"accept_suggestion: enqueue failed: {e}")
        return {"ok": True, "suggestion": sug.to_dict(), "executed": executed}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/anticipator/{sid}/dismiss")
async def dismiss_suggestion(sid: str):
    try:
        from core.anticipator import get_anticipator
        ok = get_anticipator().dismiss(sid)
        return {"ok": ok}
    except Exception as e:
        return {"ok": False, "error": str(e)}
