"""Bootstrap — паралельна ініціалізація важких підсистем.

Винесено з main.py у межах рефакторингу (розвантаження god-file).
Запускає незалежні I/O-важкі компоненти (SQLite, ChromaDB+embeddings,
HTTP-scraper, YouTube) у фонових потоках, щоб скоротити час старту на
3-5с проти послідовної ініціалізації. Повертає готові інстанси; якщо
паралельний старт компонента не вдався — створює його синхронно як fallback
(зберігає точну семантику ``init_results.get(...) or Factory()`` з main.py).
"""
from __future__ import annotations

import logging
import threading

from core.memory import JarvisDatabase
from core.memory_rag import JarvisSmartMemory
from jarvis_scraper import JarvisScraper
from jarvis_media import JarvisYouTube

logger = logging.getLogger(__name__)


def init_core_subsystems(timeout: float = 30.0) -> dict:
    """Ініціалізує db / brain / scraper / youtube паралельно.

    Повертає dict із ключами ``db``, ``brain``, ``scraper``, ``youtube``.
    """
    results: dict = {}
    errors: dict = {}

    def _safe_init(name, factory):
        try:
            results[name] = factory()
        except Exception as exc:
            errors[name] = exc
            logger.error(f"Init '{name}' failed: {exc}")

    init_threads = [
        threading.Thread(target=_safe_init, args=("db", JarvisDatabase), daemon=True),
        threading.Thread(target=_safe_init, args=("brain", lambda: JarvisSmartMemory(docs_dir="knowledge_base")), daemon=True),
        threading.Thread(target=_safe_init, args=("scraper", JarvisScraper), daemon=True),
        threading.Thread(target=_safe_init, args=("youtube", JarvisYouTube), daemon=True),
    ]
    for t in init_threads:
        t.start()
    for t in init_threads:
        t.join(timeout=timeout)

    return {
        "db":      results.get("db")      or JarvisDatabase(),
        "brain":   results.get("brain")   or JarvisSmartMemory(docs_dir="knowledge_base"),
        "scraper": results.get("scraper") or JarvisScraper(),
        "youtube": results.get("youtube") or JarvisYouTube(),
    }
