"""Reserve the API port before lifespan starts models and worker threads."""
from __future__ import annotations

import logging
import socket

import uvicorn

logger = logging.getLogger(__name__)


def run_api(app, host: str, port: int) -> None:
    """Fail immediately on an occupied port, without starting a second Jarvis."""
    with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET,
                       socket.SOCK_STREAM) as listener:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            listener.bind((host, port))
        except OSError:
            logger.error("Не вдалося відкрити %s:%s. Перевірте, чи вже запущений сервер Нео.",
                         host, port)
            raise
        listener.listen(2048)
        config = uvicorn.Config(app, host=host, port=port, reload=False)
        uvicorn.Server(config).run(sockets=[listener])
