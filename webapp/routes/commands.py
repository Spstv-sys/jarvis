"""Маршрути керування: POST /command, GET /status."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel

from webapp import state
from webapp.security import enforce_access

logger = logging.getLogger(__name__)
router = APIRouter()


class CommandBody(BaseModel):
    text: str


@router.post("/command")
async def send_command(body: CommandBody, request: Request):
    enforce_access(request, "REST /command")

    if not state.jarvis_ready.is_set():
        logger.warning(f"Команда відхилена (Jarvis не готовий): {body.text!r}")
        return {"status": "not_ready"}
    logger.info(f"REST /command: {body.text!r}")
    state.jarvis.input_queue.put(body.text)
    return {"status": "ok"}


@router.get("/status")
async def get_status():
    ready    = state.jarvis_ready.is_set()
    provider = state.jarvis.llm.active_provider if state.jarvis else "—"
    return {"ready": ready, "provider": provider}
