"""WebSocket-маршрут /ws."""
from __future__ import annotations

import logging
import base64
import binascii
import secrets

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from webapp import state
from webapp import security

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket, token: str = ""):
    """Accept WebSocket connections.

    Browser clients use the ``jarvis-key.<base64url>`` subprotocol. An
    X-API-Key header and legacy token query are also accepted.
    """
    # Browser WebSockets cannot set custom headers. Carry the key in an
    # offered subprotocol so it does not appear in URLs/access logs.
    offered = ws.scope.get("subprotocols", [])
    for protocol in offered:
        if protocol.startswith("jarvis-key."):
            try:
                encoded = protocol.removeprefix("jarvis-key.")
                token = base64.b64decode(encoded + "=" * (-len(encoded) % 4),
                                         altchars=b"-_", validate=True).decode("utf-8")
            except (ValueError, binascii.Error, UnicodeDecodeError):
                await ws.close(code=1008)
                return
    token = ws.headers.get("X-API-Key", token)
    if security.JARVIS_API_KEY and not secrets.compare_digest(
        token.encode(), security.JARVIS_API_KEY.encode()
    ):
        logger.warning(f"WebSocket: відхилено з'єднання від {ws.client} — невірний токен")
        await ws.close(code=1008)  # 1008 = Policy Violation (RFC 6455)
        return

    await state.manager.connect(ws)
    try:
        while True:
            data = await ws.receive_json()
            if not isinstance(data, dict):
                await ws.close(code=1003)
                break
            if data.get("type") == "command":
                text = data.get("text")
                if not isinstance(text, str) or not text.strip() or len(text) > 10000:
                    await ws.send_json({"type": "error", "text": "Invalid command"})
                    continue
                if not security.check_rate_limit(ws.client.host if ws.client else "unknown"):
                    await ws.send_json({"type": "error", "text": "Rate limit exceeded"})
                    continue
                if not state.jarvis_ready.is_set() or state.jarvis is None:
                    await ws.send_json({"type": "error", "text": "Jarvis not ready"})
                    continue
                logger.info(f"WebSocket команда від {ws.client}: {data.get('text')!r}")
                state.jarvis.input_queue.put(data["text"])
    except WebSocketDisconnect:
        pass
    except ValueError:
        await ws.close(code=1003)
    finally:
        await state.manager.disconnect(ws)
