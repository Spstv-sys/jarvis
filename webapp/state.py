"""Спільний рантайм-стан сервера.

Раніше це були модульні глобали в ``server.py`` (``jarvis``, ``jarvis_ready``,
``event_queue``, ``manager``). Тепер вони зосереджені тут, тож і маршрути, і
фонові таски, і життєвий цикл звертаються до одного джерела правди:
``state.jarvis``, ``state.jarvis_ready`` тощо.

``jarvis`` лишається ``None``, доки фоновий потік (:func:`webapp.lifecycle.start_jarvis`)
не завершить ініціалізацію — тому всюди перед використанням перевіряється
``state.jarvis_ready.is_set()`` / ``state.jarvis is not None``.
"""
from __future__ import annotations

import queue
import threading
from typing import Optional

from main import JarvisUltimate
from webapp.ws_manager import ConnectionManager

# Інстанс Jarvis; присвоюється у start_jarvis() з фонового потоку.
jarvis: Optional[JarvisUltimate] = None

# Сигнал готовності Jarvis (лише після успішної ініціалізації).
jarvis_ready: threading.Event = threading.Event()
startup_error: str | None = None

# Черга подій Jarvis → WebSocket.
event_queue: "queue.Queue" = queue.Queue()

# Менеджер активних WebSocket-зʼєднань.
manager: ConnectionManager = ConnectionManager()
