"""WebSocket-маршрут /ws."""
from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from webapp import state
from webapp.security import JARVIS_API_KEY

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket, token: str = ""):
    """Accept WebSocket connections.

    When ``JARVIS_API_KEY`` is set, the client must pass a matching
    ``?token=<key>`` query parameter.  This is consistent with the
    authentication model used on POST /command.
    """
    if JARVIS_API_KEY and token != JARVIS_API_KEY:
        logger.warning(f"WebSocket: відхилено з'єднання від {ws.client} — невірний токен")
        await ws.close(code=1008)  # 1008 = Policy Violation (RFC 6455)
        return

    await state.manager.connect(ws)
    try:
        while True:
            data = await ws.receive_json()
            if data.get("type") == "command" and state.jarvis_ready.is_set():
                logger.info(f"WebSocket команда від {ws.client}: {data.get('text')!r}")
                state.jarvis.input_queue.put(data["text"])
    except WebSocketDisconnect:
        await state.manager.disconnect(ws)
