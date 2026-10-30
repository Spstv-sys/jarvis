"""Маршрути керування: POST /command, GET /status."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, Field

from webapp import state
from webapp.security import enforce_access
from webapp import security

logger = logging.getLogger(__name__)
router = APIRouter()


class CommandBody(BaseModel):
    text: str = Field(min_length=1, max_length=10000)


@router.post("/command")
async def send_command(body: CommandBody, request: Request):
    enforce_access(request, "REST /command")

    if not state.jarvis_ready.is_set() or state.jarvis is None:
        logger.warning(f"Команда відхилена (Jarvis не готовий): {body.text!r}")
        raise HTTPException(status_code=503, detail="Jarvis not ready")
    logger.info(f"REST /command: {body.text!r}")
    state.jarvis.input_queue.put(body.text)
    return {"status": "ok"}


@router.get("/status")
async def get_status():
    ready    = state.jarvis_ready.is_set() and state.jarvis is not None
    provider = state.jarvis.llm.active_provider if state.jarvis else "—"
    brain = getattr(state.jarvis, "brain", None)
    return {"ready": ready, "provider": provider, "api_version": 1,
            "state": "ready" if ready else "error" if state.startup_error else "starting",
            "auth_required": bool(security.JARVIS_API_KEY),
            "memory": brain.status() if brain is not None else None}


@router.post("/auth/check")
async def check_auth(request: Request):
    enforce_access(request, "POST /auth/check")
    return {"ok": True}
