"""Episodic Memory — Шар 5 архітектури мислення.

Довготривале сховище подій (events) із семантичним та темпоральним пошуком.

СХЕМА events:
  id INTEGER PK
  ts INTEGER          -- unix-час у секундах
  kind TEXT           -- 'command' | 'plan' | 'fact' | 'note' | 'error' | ...
  actor TEXT          -- 'user' | 'jarvis' | 'system'
  text TEXT           -- основний контент події (що сталося)
  tags TEXT           -- кома-розділений список тегів
  meta_json TEXT      -- довільний JSON-метаконтекст
  embedding BLOB      -- numpy float32 (або NULL якщо embedder недоступний)

Сховище ОДНЕ на процес (singleton get_episodic_memory()).
Embedder лінується з core.memory_rag (вже завантажений — не дублюємо модель).

API:
  log(kind, text, *, actor='jarvis', tags=None, meta=None) -> int (event_id)
  search(query, *, top_k=5, kinds=None, since=None, until=None) -> List[Event]
  recent(n=10, *, kinds=None, since=None) -> List[Event]
  delete_older_than(days) -> int
  stats() -> dict
"""
from __future__ import annotations

import os
import json
import time
import sqlite3
import logging
import threading
from dataclasses import dataclass, asdict
from typing import Optional, List, Iterable, Any
from datetime import datetime

import numpy as np

import config

logger = logging.getLogger(__name__)


@dataclass
class Event:
    id: int
    ts: int
    kind: str
    actor: str
    text: str
    tags: List[str]
    meta: dict
    score: float = 0.0   # заповнюється при пошуку

    @property
    def when(self) -> str:
        try:
            return datetime.fromtimestamp(self.ts).strftime("%Y-%m-%d %H:%M")
        except Exception:
            return str(self.ts)


# ---------------------------------------------------------------------------
class EpisodicMemory:
    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER NOT NULL,
        kind TEXT NOT NULL,
        actor TEXT NOT NULL DEFAULT 'jarvis',
        text TEXT NOT NULL,
        tags TEXT NOT NULL DEFAULT '',
        meta_json TEXT NOT NULL DEFAULT '{}',
        embedding BLOB
    );
    CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
    CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind);
    """

    def __init__(self, db_path: Optional[str] = None, embedder: Any = None):
        self._db_path = db_path or os.path.join(config.BASE_DIR, "jarvis_episodic.sqlite")
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._lock = threading.RLock()
        self._embedder = embedder           # будь-який об'єкт із .embed_query(str)->List[float]
        self._embed_dim: Optional[int] = None
        self._init_db()

    # ------------------------------------------------------------------
    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self._db_path, timeout=10, check_same_thread=False)
        c.execute("PRAGMA journal_mode=WAL;")
        return c

    def _init_db(self) -> None:
        with self._lock, self._conn() as c:
            c.executescript(self._SCHEMA)

    def attach_embedder(self, embedder: Any) -> None:
        """Прикріпити embedder після того, як memory_rag прогрів модель."""
        self._embedder = embedder

    # ------------------------------------------------------------------
    def _embed(self, text: str) -> Optional[bytes]:
        if not self._embedder or not text:
            return None
        try:
            vec = self._embedder.embed_query(text)
            arr = np.asarray(vec, dtype=np.float32)
            if self._embed_dim is None:
                self._embed_dim = arr.shape[0]
            return arr.tobytes()
        except Exception as e:
            logger.warning(f"[EpisodicMemory] embed failed: {e}")
            return None

    @staticmethod
    def _bytes_to_vec(b: Optional[bytes]) -> Optional[np.ndarray]:
        if not b:
            return None
        return np.frombuffer(b, dtype=np.float32)

    @staticmethod
    def _row_to_event(row: tuple, score: float = 0.0) -> Event:
        eid, ts, kind, actor, text, tags, meta_json, _emb = row
        try:
            meta = json.loads(meta_json) if meta_json else {}
        except Exception:
            meta = {}
        tag_list = [t.strip() for t in (tags or "").split(",") if t.strip()]
        return Event(id=eid, ts=ts, kind=kind, actor=actor, text=text,
                     tags=tag_list, meta=meta, score=score)

    # ------------------------------------------------------------------
    def log(
        self,
        kind: str,
        text: str,
        *,
        actor: str = "jarvis",
        tags: Optional[Iterable[str]] = None,
        meta: Optional[dict] = None,
    ) -> int:
        if not text:
            return -1
        ts = int(time.time())
        tag_str = ",".join(t.strip() for t in (tags or []) if t and t.strip())
        meta_str = json.dumps(meta or {}, ensure_ascii=False)
        emb = self._embed(text)
        with self._lock, self._conn() as c:
            cur = c.execute(
                "INSERT INTO events (ts, kind, actor, text, tags, meta_json, embedding) "
                "VALUES (?,?,?,?,?,?,?)",
                (ts, kind, actor, text[:4000], tag_str, meta_str, emb),
            )
            event_id = int(cur.lastrowid)
        try:
            from core.thought_log import think, STAGE_EPISODIC
            think(STAGE_EPISODIC, f"записано [{kind}] {text[:120]}", id=event_id, actor=actor)
        except Exception:
            pass
        return event_id

    # ------------------------------------------------------------------
    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        kinds: Optional[Iterable[str]] = None,
        since: Optional[int] = None,
        until: Optional[int] = None,
    ) -> List[Event]:
        """Семантичний пошук + темпоральні фільтри. Якщо embedder недоступний —
        fallback до LIKE-пошуку."""
        where = []
        params: list = []
        if kinds:
            kl = list(kinds)
            where.append(f"kind IN ({','.join('?' * len(kl))})")
            params.extend(kl)
        if since is not None:
            where.append("ts >= ?"); params.append(int(since))
        if until is not None:
            where.append("ts <= ?"); params.append(int(until))
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""

        with self._lock, self._conn() as c:
            rows = c.execute(
                f"SELECT id,ts,kind,actor,text,tags,meta_json,embedding FROM events {where_sql}",
                params,
            ).fetchall()

        if not rows:
            return []

        q_vec: Optional[np.ndarray] = None
        if self._embedder and query:
            try:
                q_vec = np.asarray(self._embedder.embed_query(query), dtype=np.float32)
            except Exception as e:
                logger.warning(f"[EpisodicMemory] query embed failed: {e}")

        if q_vec is None:
            # LIKE-fallback: фільтруємо за підрядком, сортуємо за свіжістю.
            q_lower = (query or "").lower()
            scored = [(self._row_to_event(r, score=1.0 if q_lower in (r[4] or "").lower() else 0.0), r[1])
                      for r in rows]
            scored = [(e, ts) for e, ts in scored if e.score > 0 or not q_lower]
            scored.sort(key=lambda x: x[1], reverse=True)
            return [e for e, _ in scored[:top_k]]

        # Cosine similarity.
        q_norm = float(np.linalg.norm(q_vec)) or 1.0
        results: List[Event] = []
        for r in rows:
            v = self._bytes_to_vec(r[7])
            if v is None or v.shape != q_vec.shape:
                # Без embedding — невелика база-оцінка за збігом підрядка.
                base = 0.15 if (query or "").lower() in (r[4] or "").lower() else 0.0
                results.append(self._row_to_event(r, score=base))
                continue
            v_norm = float(np.linalg.norm(v)) or 1.0
            cos = float(np.dot(q_vec, v) / (q_norm * v_norm))
            results.append(self._row_to_event(r, score=cos))

        results.sort(key=lambda e: e.score, reverse=True)
        return results[:top_k]

    # ------------------------------------------------------------------
    def recent(
        self,
        n: int = 10,
        *,
        kinds: Optional[Iterable[str]] = None,
        since: Optional[int] = None,
    ) -> List[Event]:
        where = []
        params: list = []
        if kinds:
            kl = list(kinds)
            where.append(f"kind IN ({','.join('?' * len(kl))})")
            params.extend(kl)
        if since is not None:
            where.append("ts >= ?"); params.append(int(since))
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        with self._lock, self._conn() as c:
            rows = c.execute(
                f"SELECT id,ts,kind,actor,text,tags,meta_json,embedding "
                f"FROM events {where_sql} ORDER BY ts DESC LIMIT ?",
                params + [int(n)],
            ).fetchall()
        return [self._row_to_event(r) for r in rows]

    # ------------------------------------------------------------------
    def delete_older_than(self, days: int) -> int:
        cutoff = int(time.time()) - days * 86400
        with self._lock, self._conn() as c:
            cur = c.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
            return cur.rowcount or 0

    def stats(self) -> dict:
        with self._lock, self._conn() as c:
            total = c.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            with_emb = c.execute("SELECT COUNT(*) FROM events WHERE embedding IS NOT NULL").fetchone()[0]
            kinds = c.execute(
                "SELECT kind, COUNT(*) FROM events GROUP BY kind ORDER BY COUNT(*) DESC"
            ).fetchall()
        return {
            "total": total,
            "with_embedding": with_emb,
            "by_kind": dict(kinds),
            "embedder": bool(self._embedder),
            "db": self._db_path,
        }


# ---------------------------------------------------------------------------
_SINGLETON: Optional[EpisodicMemory] = None
_SINGLETON_LOCK = threading.Lock()


def get_episodic_memory() -> EpisodicMemory:
    global _SINGLETON
    with _SINGLETON_LOCK:
        if _SINGLETON is None:
            db_path = getattr(config, "EPISODIC_DB_PATH", None)
            _SINGLETON = EpisodicMemory(db_path=db_path)
        return _SINGLETON
