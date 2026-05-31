"""Thought Log — Шар 6 архітектури мислення.

Кільцевий буфер «потоку свідомості» Джарвіса. Інші модулі (IntentRouter,
Planner, Executor, Critic, EpisodicMemory) надсилають сюди короткі записи
про те, що зараз відбувається в думках. Будь-який клієнт (UI, server,
лог-паналь) може **підписатись** і отримувати їх в реальному часі.

Опційно — персистенс у SQLite (фоновий writer, не блокує emit).

Singleton, потокобезпечний.
"""
from __future__ import annotations

import os
import json
import time
import queue
import sqlite3
import threading
import logging
from collections import deque
from dataclasses import dataclass
from typing import Callable, List, Optional, Deque

logger = logging.getLogger(__name__)

# Доступні стадії мислення.
STAGE_INTENT   = "intent"
STAGE_PLAN     = "plan"
STAGE_STEP     = "step"
STAGE_CRITIC   = "critic"
STAGE_REPLAN   = "replan"
STAGE_EPISODIC = "episodic"
STAGE_WM       = "wm"
STAGE_AFFECT   = "affect"
STAGE_ERROR    = "error"
STAGE_INFO     = "info"


@dataclass
class Thought:
    ts: float
    stage: str
    text: str
    meta: dict

    def to_dict(self) -> dict:
        return {"ts": self.ts, "stage": self.stage, "text": self.text, "meta": self.meta}


class ThoughtLog:
    def __init__(
        self,
        maxlen: int = 200,
        db_path: Optional[str] = None,
        preload: int = 100,
        retention_days: int = 30,
    ):
        self._buf: Deque[Thought] = deque(maxlen=maxlen)
        self._subs: List[Callable[[Thought], None]] = []
        self._lock = threading.RLock()
        self._db_path = db_path
        self._retention_days = retention_days
        self._write_q: Optional[queue.Queue] = None
        self._writer_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

        if db_path:
            try:
                d = os.path.dirname(os.path.abspath(db_path))
                if d:
                    os.makedirs(d, exist_ok=True)
                self._init_db()
                self._preload(preload)
                self._start_writer()
                logger.info(f"ThoughtLog persistence enabled: {db_path} (preloaded={len(self._buf)})")
            except Exception as e:
                logger.warning(f"ThoughtLog persistence init failed ({e}); running memory-only.")
                self._db_path = None

    # ---------- persistence ----------
    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self._db_path, timeout=5.0)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        return c

    def _init_db(self) -> None:
        with self._conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS thoughts (
                    id    INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts    REAL    NOT NULL,
                    stage TEXT    NOT NULL,
                    text  TEXT    NOT NULL,
                    meta  TEXT
                )
            """)
            c.execute("CREATE INDEX IF NOT EXISTS idx_thoughts_ts    ON thoughts(ts)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_thoughts_stage ON thoughts(stage)")
            if self._retention_days > 0:
                cutoff = time.time() - self._retention_days * 86400
                c.execute("DELETE FROM thoughts WHERE ts < ?", (cutoff,))

    def _preload(self, n: int) -> None:
        if n <= 0:
            return
        with self._conn() as c:
            rows = c.execute(
                "SELECT ts, stage, text, meta FROM thoughts ORDER BY id DESC LIMIT ?",
                (n,),
            ).fetchall()
        for ts, stage, text, meta_json in reversed(rows):
            try:
                meta = json.loads(meta_json) if meta_json else {}
            except Exception:
                meta = {}
            self._buf.append(Thought(ts=ts, stage=stage, text=text, meta=meta))

    def _start_writer(self) -> None:
        self._write_q = queue.Queue()
        self._writer_thread = threading.Thread(
            target=self._writer_loop, name="ThoughtLogWriter", daemon=True,
        )
        self._writer_thread.start()

    def _writer_loop(self) -> None:
        assert self._write_q is not None
        BATCH = 20
        while not self._stop.is_set():
            try:
                first = self._write_q.get(timeout=0.5)
            except queue.Empty:
                continue
            batch = [first]
            try:
                while len(batch) < BATCH:
                    batch.append(self._write_q.get_nowait())
            except queue.Empty:
                pass
            try:
                with self._conn() as c:
                    c.executemany(
                        "INSERT INTO thoughts (ts, stage, text, meta) VALUES (?,?,?,?)",
                        batch,
                    )
            except Exception as e:
                logger.debug(f"ThoughtLog writer batch failed: {e}")

    def shutdown(self) -> None:
        self._stop.set()
        if self._writer_thread and self._writer_thread.is_alive():
            self._writer_thread.join(timeout=2.0)

    # ---------- write ----------
    def log(self, stage: str, text: str, **meta) -> Thought:
        t = Thought(ts=time.time(), stage=stage, text=text[:500], meta=meta or {})
        subs_snapshot: List[Callable[[Thought], None]]
        with self._lock:
            self._buf.append(t)
            subs_snapshot = list(self._subs)
        if self._write_q is not None:
            try:
                self._write_q.put_nowait(
                    (t.ts, t.stage, t.text, json.dumps(t.meta, ensure_ascii=False))
                )
            except Exception:
                pass
        for cb in subs_snapshot:
            try:
                cb(t)
            except Exception as e:
                logger.debug(f"thought subscriber error: {e}")
        return t

    # ---------- read ----------
    def recent(self, n: int = 50, stages: Optional[list] = None) -> List[Thought]:
        with self._lock:
            items = list(self._buf)
        if stages:
            stages_set = set(stages)
            items = [t for t in items if t.stage in stages_set]
        return items[-n:]

    def history(
        self,
        limit: int = 200,
        stages: Optional[list] = None,
        since: Optional[float] = None,
        until: Optional[float] = None,
    ) -> List[Thought]:
        """Запит до SQLite (історія за весь час). Якщо persistence вимкнено — fallback на буфер."""
        if not self._db_path:
            return self.recent(limit, stages)
        sql = "SELECT ts, stage, text, meta FROM thoughts WHERE 1=1"
        args: list = []
        if stages:
            sql += " AND stage IN (" + ",".join("?" * len(stages)) + ")"
            args.extend(stages)
        if since is not None:
            sql += " AND ts >= ?"; args.append(since)
        if until is not None:
            sql += " AND ts <= ?"; args.append(until)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        try:
            with self._conn() as c:
                rows = c.execute(sql, args).fetchall()
        except Exception as e:
            logger.debug(f"ThoughtLog history query failed: {e}")
            return self.recent(limit, stages)
        out: List[Thought] = []
        for ts, stage, text, meta_json in reversed(rows):
            try:
                meta = json.loads(meta_json) if meta_json else {}
            except Exception:
                meta = {}
            out.append(Thought(ts=ts, stage=stage, text=text, meta=meta))
        return out

    # ---------- subscribe ----------
    def subscribe(self, cb: Callable[[Thought], None]) -> Callable[[], None]:
        with self._lock:
            self._subs.append(cb)
        def unsubscribe():
            with self._lock:
                try:
                    self._subs.remove(cb)
                except ValueError:
                    pass
        return unsubscribe

    def stats(self) -> dict:
        with self._lock:
            kinds: dict = {}
            for t in self._buf:
                kinds[t.stage] = kinds.get(t.stage, 0) + 1
            base = {
                "size": len(self._buf),
                "max": self._buf.maxlen,
                "subscribers": len(self._subs),
                "by_stage": kinds,
                "persistent": bool(self._db_path),
            }
        if self._db_path:
            try:
                with self._conn() as c:
                    total = c.execute("SELECT COUNT(*) FROM thoughts").fetchone()[0]
                base["total_persisted"] = total
            except Exception:
                pass
        return base


# ---------------------------------------------------------------------------
def _load_config():
    """Читає конфіг з config.py (толерантно)."""
    try:
        import config as cfg
        persist = getattr(cfg, "THOUGHT_LOG_PERSIST", True)
        return {
            "maxlen":         getattr(cfg, "THOUGHT_LOG_MAXLEN", 200),
            "db_path":        getattr(cfg, "THOUGHT_LOG_DB_PATH", "jarvis_thoughts.sqlite") if persist else None,
            "preload":        getattr(cfg, "THOUGHT_LOG_PRELOAD", 100),
            "retention_days": getattr(cfg, "THOUGHT_LOG_RETENTION_DAYS", 30),
        }
    except Exception:
        return {"maxlen": 200, "db_path": None, "preload": 100, "retention_days": 30}


def get_thought_log() -> ThoughtLog:
    # Зберігання делеговано у core.container (єдине джерело правди);
    # логіка конструювання (_load_config) лишається тут.
    from core.container import get_container
    return get_container().get_or_create(
        "thought_log", lambda: ThoughtLog(**_load_config())
    )


def think(stage: str, text: str, **meta) -> None:
    """Зручний шорткат для emit'у думки з будь-якого модуля."""
    try:
        get_thought_log().log(stage, text, **meta)
    except Exception:
        pass
