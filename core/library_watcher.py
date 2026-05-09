"""Спостерігач за папкою бібліотеки — авто-індексація нових книг.

Слідкує за ``config.LITERATURE_DIR`` (рекурсивно). Коли з'являється новий
файл підтримуваного формату — після debounce-затримки (щоб дочекатись
завершення копіювання) запускає той самий пайплайн індексації, що й
``skill_learn_library``: прочитати → розбити → зберегти у Chroma з
метаданими.

Запускається з ``main.py`` як daemon-потік. За замовчуванням вимкнений —
вмикається через env ``LIBRARY_WATCH=1``.
"""
from __future__ import annotations

import os
import time
import logging
import threading
from typing import Set, Optional

import config
from core.book_loader import load_book, SUPPORTED_EXTS
from langchain_text_splitters import RecursiveCharacterTextSplitter

try:
    from core.knowledge_graph import get_kg, ingest_chunk_into_kg
    _KG_AVAILABLE = True
except Exception:
    _KG_AVAILABLE = False

logger = logging.getLogger(__name__)


# Інтервал, після якого файл вважається "готовим" (розмір не змінився).
STABLE_INTERVAL_SEC = float(os.getenv("LIBRARY_WATCH_STABLE_SEC", "3.0"))
# Як часто опитуємо стан pending-файлів.
POLL_INTERVAL_SEC   = float(os.getenv("LIBRARY_WATCH_POLL_SEC",   "2.0"))


def _split_for_prose(text: str):
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=getattr(config, "LITERATURE_CHUNK_SIZE", 1500),
        chunk_overlap=getattr(config, "LITERATURE_CHUNK_OVERLAP", 300),
        separators=["\n\n", "\n", ". ", "! ", "? ", " "],
    )
    return [c.strip() for c in splitter.split_text(text) if len(c.strip()) > 80]


def _ingest_book(path: str, jarvis) -> bool:
    """Завантажує одну книгу у пам'ять. Повертає True при успіху."""
    fname = os.path.basename(path)
    try:
        text, meta = load_book(path)
        if not text or len(text) < 200:
            logger.info(f"[watch] {fname}: занадто короткий, пропускаю")
            return False

        chunks = _split_for_prose(text)
        if not chunks:
            return False

        title  = meta.get("title", os.path.splitext(fname)[0])
        author = meta.get("author")
        base_meta = {
            "author":      author,
            "title":       title,
            "format":      meta.get("format"),
            "language":    meta.get("language"),
            "source":      f"book_{fname}",
            "source_path": meta.get("source_path", path),
            "ingested_by": "watcher",
        }
        for i, chunk in enumerate(chunks):
            m = dict(base_meta)
            m["chunk_idx"] = i
            prefix = f"[{title}" + (f" — {author}" if author else "") + f"] {chunk}"
            jarvis.brain.memorize(prefix, source=base_meta["source"], metadata=m)

        # Knowledge graph (опційно).
        if _KG_AVAILABLE and getattr(config, "KG_ENABLED", False):
            try:
                kg = get_kg()
                for i, chunk in enumerate(chunks):
                    ingest_chunk_into_kg(kg, chunk, source_book=fname, chunk_idx=i)
            except Exception as e:
                logger.warning(f"[watch] KG помилка для {fname}: {e}")

        logger.info(f"[watch] '{title}' → {len(chunks)} чанків")
        # М'яке голосове повідомлення (не обов'язково)
        try:
            jarvis.enqueue_speech(f"У бібліотеку додано книгу {title}.")
        except Exception:
            pass
        return True
    except Exception as e:
        logger.error(f"[watch] помилка індексації {fname}: {e}")
        return False


class LibraryWatcher:
    """Поллінг-спостерігач (без зовнішньої watchdog-залежності у hot-path)."""

    def __init__(self, jarvis, folder: Optional[str] = None):
        self.jarvis = jarvis
        self.folder = folder or getattr(
            config, "LITERATURE_DIR", os.path.join(config.BASE_DIR, "literature")
        )
        self._known: Set[str] = set()
        self._pending: dict[str, tuple[int, float]] = {}  # path → (size, last_change_ts)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _scan(self) -> Set[str]:
        found: Set[str] = set()
        if not os.path.isdir(self.folder):
            return found
        for root, _, files in os.walk(self.folder):
            for fn in files:
                if os.path.splitext(fn)[1].lower() in SUPPORTED_EXTS:
                    found.add(os.path.join(root, fn))
        return found

    def _initial_baseline(self) -> None:
        """Перший прохід — фіксуємо існуючі файли як 'вже відомі'."""
        self._known = self._scan()
        logger.info(
            f"[watch] базова лінія: {len(self._known)} книг у {self.folder}"
        )

    def _loop(self) -> None:
        try:
            os.makedirs(self.folder, exist_ok=True)
        except OSError as e:
            logger.error(f"[watch] не можу створити {self.folder}: {e}")
            return

        self._initial_baseline()

        while not self._stop.is_set():
            try:
                current = self._scan()
                new_files = current - self._known

                # Реєструємо нові у pending (для debounce).
                for path in new_files:
                    try:
                        size = os.path.getsize(path)
                    except OSError:
                        continue
                    prev = self._pending.get(path)
                    if prev is None or prev[0] != size:
                        self._pending[path] = (size, time.time())

                # Перевіряємо pending — чи стабілізувались.
                ready = []
                now = time.time()
                for path, (size, ts) in list(self._pending.items()):
                    if not os.path.exists(path):
                        self._pending.pop(path, None)
                        continue
                    try:
                        cur_size = os.path.getsize(path)
                    except OSError:
                        continue
                    if cur_size != size:
                        self._pending[path] = (cur_size, now)
                        continue
                    if now - ts >= STABLE_INTERVAL_SEC:
                        ready.append(path)

                # Індексуємо стабілізовані.
                for path in ready:
                    self._pending.pop(path, None)
                    if _ingest_book(path, self.jarvis):
                        self._known.add(path)
                    else:
                        # все одно додаємо у known, щоб не намагатись індексувати знов
                        self._known.add(path)

                # Прибираємо віддалені файли з known.
                self._known &= current | set(self._pending.keys())

            except Exception as e:
                logger.error(f"[watch] помилка циклу: {e}")

            self._stop.wait(POLL_INTERVAL_SEC)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="LibraryWatcher"
        )
        self._thread.start()
        logger.info(f"[watch] LibraryWatcher запущено для {self.folder}")

    def stop(self) -> None:
        self._stop.set()
