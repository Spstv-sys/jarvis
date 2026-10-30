"""Аутентифікація за API-ключем + per-IP rate limiting для REST/WS."""
from __future__ import annotations

import os
import time
import logging
import threading
import secrets
from collections import defaultdict

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Конфігурація
# ---------------------------------------------------------------------------
JARVIS_API_KEY = os.getenv("JARVIS_API_KEY")
RATE_LIMIT = int(os.getenv("JARVIS_RATE_LIMIT", "30"))  # requests per window
RATE_WINDOW = 60                                        # seconds

_rate_counters: dict[str, list[float]] = defaultdict(list)
_rate_lock = threading.Lock()


def check_rate_limit(client_ip: str) -> bool:
    """Return True if the request is within the rate limit, False otherwise.

    Also prunes expired entries for *all* tracked IPs to prevent unbounded
    memory growth when many unique addresses hit the server over time.
    """
    now = time.monotonic()
    with _rate_lock:
        # Prune expired entries for all IPs to prevent memory leak
        stale_ips = [
            ip for ip, ts_list in _rate_counters.items()
            if not any(now - t < RATE_WINDOW for t in ts_list)
        ]
        for ip in stale_ips:
            del _rate_counters[ip]

        recent = [t for t in _rate_counters[client_ip] if now - t < RATE_WINDOW]
        if len(recent) >= RATE_LIMIT:
            _rate_counters[client_ip] = recent
            return False
        recent.append(now)
        _rate_counters[client_ip] = recent
    return True


def enforce_access(request: Request, label: str, *, log: bool = True) -> str:
    """Перевірити IP, API-ключ і rate-limit. Повернути client_ip або кинути HTTPException.

    Інкапсулює логіку, що раніше дублювалася у /command, /upload, /transcribe:

    * відсутній client_ip → 400 Bad Request (захист від обходу rate-limit);
    * невірний X-API-Key (коли ключ налаштовано) → 401 Unauthorized;
    * перевищено ліміт → 429 Too Many Requests.

    ``label`` використовується у попереджувальних логах (напр. "REST /command").
    ``log=False`` вимикає попередження (історична поведінка /transcribe).
    """
    client_ip = request.client.host if request.client else None
    if client_ip is None:
        # Cannot determine client IP — reject to prevent rate-limit bypass
        raise HTTPException(status_code=400, detail="Bad Request")

    if JARVIS_API_KEY:
        api_key = request.headers.get("X-API-Key", "")
        if not secrets.compare_digest(api_key.encode(), JARVIS_API_KEY.encode()):
            if log:
                logger.warning(f"{label}: неправильний API-ключ від {client_ip}")
            raise HTTPException(status_code=401, detail="Unauthorized")

    if not check_rate_limit(client_ip):
        if log:
            logger.warning(f"{label}: ліміт запитів вичерпано для {client_ip}")
        raise HTTPException(status_code=429, detail="Too Many Requests")

    return client_ip


def require_access(request: Request) -> None:
    """Router dependency for private state and actions."""
    enforce_access(request, f"{request.method} {request.url.path}")
