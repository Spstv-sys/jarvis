"""Goal Stack — Шар 6+ (візуалізація активного плану).

Тримає стан *активного зараз* плану: яка ціль, які кроки, який зараз
виконується, які завершені. Також веде *стек* — якщо під час виконання
кроку ініціюється новий план (рекурсивне планування у майбутньому).

Singleton, потокобезпечний. Підписники отримують сповіщення про кожну
зміну стану (для WebSocket-broadcast).
"""
from __future__ import annotations

import time
import threading
import logging
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Dict

logger = logging.getLogger(__name__)

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_DONE    = "done"
STATUS_FAILED  = "failed"
STATUS_SKIPPED = "skipped"


@dataclass
class GoalStep:
    n: int
    skill: str
    input: str
    status: str = STATUS_PENDING
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    duration_sec: float = 0.0
    output_preview: str = ""
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "n": self.n, "skill": self.skill, "input": self.input,
            "status": self.status, "started_at": self.started_at,
            "finished_at": self.finished_at, "duration_sec": self.duration_sec,
            "output_preview": self.output_preview, "error": self.error,
        }


@dataclass
class Goal:
    id: str
    text: str
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    status: str = STATUS_RUNNING       # running | done | failed | cancelled
    verdict: Optional[str] = None       # critic verdict text
    success: Optional[bool] = None      # critic success
    steps: List[GoalStep] = field(default_factory=list)
    replan_count: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id, "text": self.text,
            "started_at": self.started_at, "finished_at": self.finished_at,
            "status": self.status, "verdict": self.verdict, "success": self.success,
            "replan_count": self.replan_count,
            "steps": [s.to_dict() for s in self.steps],
            "progress": self._progress(),
        }

    def _progress(self) -> Dict[str, int]:
        out = {"total": len(self.steps), "done": 0, "failed": 0, "running": 0, "pending": 0}
        for s in self.steps:
            if s.status == STATUS_DONE:    out["done"] += 1
            elif s.status == STATUS_FAILED: out["failed"] += 1
            elif s.status == STATUS_RUNNING: out["running"] += 1
            else: out["pending"] += 1
        return out


class GoalStack:
    def __init__(self, history_max: int = 20):
        self._stack: List[Goal] = []
        self._history: List[Goal] = []
        self._history_max = history_max
        self._subs: List[Callable[[dict], None]] = []
        self._lock = threading.RLock()
        self._counter = 0

    # ---------- mutations ----------
    def push(self, goal_text: str, steps: list) -> Goal:
        """Починає новий goal. steps — List[Step] з core.planner."""
        with self._lock:
            self._counter += 1
            gid = f"g{int(time.time() * 1000)}-{self._counter}"
            gs = [GoalStep(n=s.n, skill=s.skill, input=s.input) for s in (steps or [])]
            g = Goal(id=gid, text=goal_text, steps=gs)
            self._stack.append(g)
        self._notify()
        return g

    def replan(self, new_steps: list) -> Optional[Goal]:
        """Замінює кроки активного goal'у новим планом (під час critic→replan)."""
        with self._lock:
            g = self._top_unsafe()
            if g is None:
                return None
            g.replan_count += 1
            # Зберігаємо вже виконані кроки, заміняємо «майбутні».
            done_or_running = [s for s in g.steps if s.status in (STATUS_DONE, STATUS_FAILED)]
            new_gs = [GoalStep(n=s.n, skill=s.skill, input=s.input) for s in new_steps]
            g.steps = done_or_running + new_gs
        self._notify()
        return g

    def step_start(self, n: int) -> None:
        with self._lock:
            g = self._top_unsafe()
            if g is None: return
            for s in g.steps:
                if s.n == n:
                    s.status = STATUS_RUNNING
                    s.started_at = time.time()
                    break
        self._notify()

    def step_finish(self, n: int, ok: bool, output: str = "", error: Optional[str] = None,
                    duration_sec: float = 0.0) -> None:
        with self._lock:
            g = self._top_unsafe()
            if g is None: return
            for s in g.steps:
                if s.n == n:
                    s.status = STATUS_DONE if ok else STATUS_FAILED
                    s.finished_at = time.time()
                    s.duration_sec = duration_sec
                    s.output_preview = (output or "")[:200]
                    s.error = error
                    break
        self._notify()

    def set_verdict(self, success: bool, verdict_text: str) -> None:
        with self._lock:
            g = self._top_unsafe()
            if g is None: return
            g.success = success
            g.verdict = (verdict_text or "")[:300]
        self._notify()

    def pop(self, status: str = STATUS_DONE) -> Optional[Goal]:
        """Знімає верхній goal зі стеку, кладе в історію."""
        with self._lock:
            if not self._stack:
                return None
            g = self._stack.pop()
            g.status = status
            g.finished_at = time.time()
            self._history.append(g)
            if len(self._history) > self._history_max:
                self._history = self._history[-self._history_max:]
        self._notify()
        return g

    # ---------- read ----------
    def _top_unsafe(self) -> Optional[Goal]:
        return self._stack[-1] if self._stack else None

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "active":  [g.to_dict() for g in self._stack],
                "history": [g.to_dict() for g in self._history[-10:]],
            }

    # ---------- pub/sub ----------
    def subscribe(self, cb: Callable[[dict], None]) -> Callable[[], None]:
        with self._lock:
            self._subs.append(cb)
        def unsub():
            with self._lock:
                try: self._subs.remove(cb)
                except ValueError: pass
        return unsub

    def _notify(self) -> None:
        with self._lock:
            snap = self.snapshot()
            subs = list(self._subs)
        for cb in subs:
            try:
                cb(snap)
            except Exception as e:
                logger.debug(f"goal-stack subscriber error: {e}")


# --- singleton ---
_SINGLETON: Optional[GoalStack] = None
_LOCK = threading.Lock()


def get_goal_stack() -> GoalStack:
    global _SINGLETON
    with _LOCK:
        if _SINGLETON is None:
            _SINGLETON = GoalStack()
        return _SINGLETON
