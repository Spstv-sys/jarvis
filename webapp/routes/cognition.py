"""Маршрути когнітивного стану: /cognition, /goal_stack, /reflection."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter

from webapp import state

router = APIRouter()


@router.get("/cognition")
async def get_cognition():
    """Снепшот когнітивного стану: WM + останні думки + епізоди."""
    snapshot: dict = {"ready": state.jarvis_ready.is_set()}
    try:
        from core.thought_log import get_thought_log
        snapshot["thoughts"] = [t.to_dict() for t in get_thought_log().recent(50)]
    except Exception as e:
        snapshot["thoughts_error"] = str(e)

    if state.jarvis is not None:
        wm = getattr(state.jarvis, "working_memory", None)
        if wm is not None:
            try:
                snapshot["working_memory"] = {
                    "context": wm.context_block(),
                    "entities": [e.__dict__ for e in getattr(wm, "entities", lambda: [])()][:10]
                                  if hasattr(wm, "entities") else [],
                }
            except Exception as e:
                snapshot["wm_error"] = str(e)
        em = getattr(state.jarvis, "episodic_memory", None)
        if em is not None:
            try:
                snapshot["episodic_recent"] = [
                    {"when": e.when, "kind": e.kind, "text": e.text}
                    for e in em.recent(15)
                ]
                snapshot["episodic_stats"] = em.stats()
            except Exception as e:
                snapshot["em_error"] = str(e)
        ir = getattr(state.jarvis, "intent_router", None)
        if ir is not None:
            try:
                snapshot["intent_router"] = ir.stats()
            except Exception:
                pass
    return snapshot


@router.get("/cognition/history")
async def get_cognition_history(
    limit: int = 200,
    stages: str = "",
    since: float | None = None,
    until: float | None = None,
):
    """Історія думок з SQLite (за весь час)."""
    try:
        from core.thought_log import get_thought_log
        stage_list = [s.strip() for s in stages.split(",") if s.strip()] or None
        items = get_thought_log().history(limit=limit, stages=stage_list, since=since, until=until)
        return {"thoughts": [t.to_dict() for t in items], "count": len(items)}
    except Exception as e:
        return {"error": str(e), "thoughts": []}


@router.get("/goal_stack")
async def get_goal_stack_snapshot():
    """Поточний стек активних планів + остання історія."""
    try:
        from core.goal_stack import get_goal_stack
        return get_goal_stack().snapshot()
    except Exception as e:
        return {"error": str(e), "active": [], "history": []}


@router.get("/reflection")
async def get_reflection_snapshot():
    """Останні згенеровані уроки + статистика рефлексії."""
    try:
        from core.reflector import get_reflector
        return get_reflector().snapshot()
    except Exception as e:
        return {"error": str(e), "recent": [], "stats": {}}


@router.post("/reflection/run")
async def trigger_reflection(window_hours: int | None = None):
    """Ручний запуск рефлексії; повертає створені уроки."""
    try:
        from core.reflector import get_reflector
        loop = asyncio.get_running_loop()
        lessons = await loop.run_in_executor(
            None, lambda: get_reflector().reflect_now(window_hours=window_hours)
        )
        return {"lessons": [l.to_dict() for l in lessons], "count": len(lessons)}
    except Exception as e:
        return {"error": str(e), "lessons": []}
