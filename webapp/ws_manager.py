"""ConnectionManager — облік активних WebSocket-клієнтів і broadcast."""
from __future__ import annotations

import asyncio
import logging

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self):
        self._clients: list[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        protocol = "jarvis" if "jarvis" in ws.scope.get("subprotocols", []) else None
        await ws.accept(subprotocol=protocol)
        async with self._lock:
            self._clients.append(ws)
        logger.info(f"WebSocket клієнт підключився: {ws.client}. Всього клієнтів: {len(self._clients)}")

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients = [c for c in self._clients if c != ws]
        logger.info(f"WebSocket клієнт відключився: {ws.client}. Залишилось клієнтів: {len(self._clients)}")

    async def broadcast(self, data: dict) -> None:
        async with self._lock:
            if not self._clients:
                return
            # Паралельний send + видалення мертвих за один прохід.
            results = await asyncio.gather(
                *(c.send_json(data) for c in self._clients),
                return_exceptions=True,
            )
            self._clients = [
                c for c, r in zip(self._clients, results)
                if not isinstance(r, Exception)
            ]
