"""Anticipator — Шар 8 архітектури мислення (проактивність).

Без явного запиту користувача формує *пропозиції* (Suggestion) на основі:
  • routine-уроків з Reflector ("користувач часто каже X")
  • незавершених/провалених цілей з GoalStack history (follow-up)
  • часових патернів з Episodic ("щодня близько 19:00 → погода")
  • системних метрик (наприклад, низький заряд батареї)

Кожна пропозиція має пріоритет, текст для TTS, і опціонально команду
для виконання після підтвердження користувача.

Сам не озвучує — лише ставить у чергу. Озвучення/підтвердження інтегрується
окремо (через UI Suggestion-баннер або скіл-обробник). Anticipator
НЕ блокує основний цикл і працює у власному потоці.

API:
    a = get_anticipator()
    a.scan_now()                    # один прохід генерації
    a.start(interval_sec=600)       # фоновий цикл
    a.snapshot()                    # {pending: [...], stats: {...}, recent: [...]}
    a.subscribe(cb)                 # cb(snapshot)
    a.accept(suggestion_id)         # забрати з черги (підтвердив)
    a.dismiss(suggestion_id)        # відкинути
    a.peek()                        # верхня пропозиція без вилучення
"""
from __future__ import annotations

import re
import time
import logging
import threading
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict, field
from datetime import datetime
from typing import Callable, List, Optional, Dict, Any

logger = logging.getLogger(__name__)

# Джерела пропозицій.
SOURCE_ROUTINE      = "routine"
SOURCE_FOLLOWUP     = "followup"
SOURCE_TIME_PATTERN = "time_pattern"
SOURCE_SYSTEM       = "system"

# Пріоритети.
PRIORITY_LOW    = 1
PRIORITY_NORMAL = 2
PRIORITY_HIGH   = 3
PRIORITY_URGENT = 4


@dataclass
class Suggestion:
    id: str
    ts: int
    source: str                 # routine | followup | time_pattern | system
    priority: int
    text: str                   # що сказати користувачу (питання / пропозиція)
    command: Optional[str]      # команда, яку запустити після згоди
    confidence: float           # 0..1
    expires_at: Optional[int]   # unix-час, після якого пропозиція автовідкинеться
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def is_expired(self, now: Optional[int] = None) -> bool:
        if not self.expires_at:
            return False
        return (now or int(time.time())) > self.expires_at


# ---------------------------------------------------------------------------
def _load_config() -> Dict[str, Any]:
    try:
        import config
    except Exception:
        config = None  # type: ignore
    g = lambda k, d: getattr(config, k, d) if config else d
    return {
        "enabled":          bool(g("ANTICIPATOR_ENABLED", True)),
        "interval_sec":     int(g("ANTICIPATOR_INTERVAL_SEC", 0)),     # 0 = manual
        "max_pending":      int(g("ANTICIPATOR_MAX_PENDING", 5)),
        "expire_after_sec": int(g("ANTICIPATOR_EXPIRE_SEC", 1800)),    # 30 хв
        "cooldown_sec":     int(g("ANTICIPATOR_COOLDOWN_SEC", 600)),   # не повторювати ту саму ідею < 10 хв
        "battery_low_pct":  int(g("ANTICIPATOR_BATTERY_LOW_PCT", 20)),
        "time_window_min":  int(g("ANTICIPATOR_TIME_WINDOW_MIN", 30)), # збіг ±30 хв із минулим патерном
        "time_min_repeats": int(g("ANTICIPATOR_TIME_MIN_REPEATS", 3)),
    }


# ---------------------------------------------------------------------------
class Anticipator:
    """Генератор проактивних пропозицій."""

    def __init__(self):
        cfg = _load_config()
        self._enabled          = cfg["enabled"]
        self._interval         = cfg["interval_sec"]
        self._max_pending      = cfg["max_pending"]
        self._expire_after_sec = cfg["expire_after_sec"]
        self._cooldown_sec     = cfg["cooldown_sec"]
        self._battery_low_pct  = cfg["battery_low_pct"]
        self._time_window_min  = cfg["time_window_min"]
        self._time_min_repeats = cfg["time_min_repeats"]

        self._lock      = threading.RLock()
        self._pending: List[Suggestion] = []
        self._recent:  List[Suggestion] = []   # останні 10 (accepted/dismissed/expired)
        self._cooldown: Dict[str, int] = {}    # key -> ts останньої появи
        self._counter   = 0
        self._stats     = {"runs": 0, "generated": 0, "accepted": 0,
                           "dismissed": 0, "expired": 0}
        self._subs:     List[Callable[[dict], None]] = []
        self._stop_evt  = threading.Event()
        self._thread:   Optional[threading.Thread] = None
        self._jarvis_ref = None

    # ------------------------------------------------------------------
    def attach_jarvis(self, jarvis) -> None:
        """Опційно: для системних сенсорів (батарея, тощо)."""
        with self._lock:
            self._jarvis_ref = jarvis

    def subscribe(self, cb: Callable[[dict], None]) -> Callable[[], None]:
        with self._lock:
            self._subs.append(cb)
        def _unsub():
            with self._lock:
                try: self._subs.remove(cb)
                except ValueError: pass
        return _unsub

    def snapshot(self) -> dict:
        with self._lock:
            self._purge_expired_locked()
            return {
                "enabled":      self._enabled,
                "interval_sec": self._interval,
                "stats":        dict(self._stats),
                "pending":      [s.to_dict() for s in
                                 sorted(self._pending,
                                        key=lambda x: (-x.priority, -x.confidence, x.ts))],
                "recent":       [s.to_dict() for s in self._recent[-10:]],
            }

    def peek(self) -> Optional[Suggestion]:
        with self._lock:
            self._purge_expired_locked()
            if not self._pending:
                return None
            return sorted(self._pending,
                          key=lambda x: (-x.priority, -x.confidence, x.ts))[0]

    # ------------------------------------------------------------------
    def start(self, interval_sec: Optional[int] = None) -> None:
        if interval_sec is not None:
            self._interval = int(interval_sec)
        if self._interval <= 0:
            logger.info("[Anticipator] фоновий режим вимкнено (interval=0)")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._loop, name="AnticipatorLoop", daemon=True)
        self._thread.start()
        logger.info(f"[Anticipator] стартував із інтервалом {self._interval}s")

    def stop(self) -> None:
        self._stop_evt.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    # ------------------------------------------------------------------
    # Пропозиції — accept/dismiss
    # ------------------------------------------------------------------
    def accept(self, suggestion_id: str) -> Optional[Suggestion]:
        """Користувач погодився. Повертає пропозицію (для виконання command)."""
        with self._lock:
            sug = self._pop_locked(suggestion_id)
            if sug:
                self._recent.append(sug)
                self._stats["accepted"] += 1
        if sug:
            try:
                from core.thought_log import think
                think("info", f"proactive accepted: {sug.text[:80]}",
                      sug_id=sug.id, source=sug.source)
            except Exception:
                pass
            self._notify()
        return sug

    def dismiss(self, suggestion_id: str) -> bool:
        with self._lock:
            sug = self._pop_locked(suggestion_id)
            if sug:
                self._recent.append(sug)
                self._stats["dismissed"] += 1
                # Подовжимо cooldown, щоб не повторювати щойно відкинуте.
                key = self._cooldown_key(sug)
                self._cooldown[key] = int(time.time()) + max(self._cooldown_sec * 3, 1800)
        if sug:
            try:
                from core.thought_log import think
                think("info", f"proactive dismissed: {sug.text[:80]}",
                      sug_id=sug.id, source=sug.source)
            except Exception:
                pass
            self._notify()
        return sug is not None

    # ------------------------------------------------------------------
    def scan_now(self) -> List[Suggestion]:
        """Один прохід генерації пропозицій. Повертає НОВІ пропозиції."""
        if not self._enabled:
            return []
        new_sugs: List[Suggestion] = []
        try:
            new_sugs.extend(self._from_routines())
        except Exception as e:
            logger.warning(f"[Anticipator] routines: {e}")
        try:
            new_sugs.extend(self._from_followups())
        except Exception as e:
            logger.warning(f"[Anticipator] followups: {e}")
        try:
            new_sugs.extend(self._from_time_patterns())
        except Exception as e:
            logger.warning(f"[Anticipator] time_patterns: {e}")
        try:
            new_sugs.extend(self._from_system())
        except Exception as e:
            logger.warning(f"[Anticipator] system: {e}")

        # Cooldown + dedup + max_pending.
        accepted_new: List[Suggestion] = []
        with self._lock:
            self._purge_expired_locked()
            now = int(time.time())
            existing_keys = {self._cooldown_key(s) for s in self._pending}
            for s in new_sugs:
                key = self._cooldown_key(s)
                if key in existing_keys:
                    continue
                cd_until = self._cooldown.get(key, 0)
                if now < cd_until:
                    continue
                self._pending.append(s)
                self._cooldown[key] = now + self._cooldown_sec
                existing_keys.add(key)
                accepted_new.append(s)
                self._stats["generated"] += 1
            # Тримаємо стек у межах max_pending (відкидаємо з найнижчим пріоритетом).
            if len(self._pending) > self._max_pending:
                self._pending.sort(key=lambda x: (-x.priority, -x.confidence))
                dropped = self._pending[self._max_pending:]
                self._pending = self._pending[: self._max_pending]
                for d in dropped:
                    d_dict = d
                    self._recent.append(d_dict)
                    self._stats["expired"] += 1
            self._stats["runs"] += 1

        if accepted_new:
            try:
                from core.thought_log import think
                think("info", f"proactive: {len(accepted_new)} нових пропозицій",
                      sources=[s.source for s in accepted_new])
            except Exception:
                pass
            self._notify()
        return accepted_new

    # ------------------------------------------------------------------
    # Внутрішнє
    # ------------------------------------------------------------------
    def _loop(self) -> None:
        if self._stop_evt.wait(min(60, self._interval)):
            return
        while not self._stop_evt.is_set():
            try:
                self.scan_now()
            except Exception as e:
                logger.error(f"[Anticipator] loop error: {e}")
            if self._stop_evt.wait(self._interval):
                return

    def _purge_expired_locked(self) -> None:
        if not self._pending:
            return
        now = int(time.time())
        kept: List[Suggestion] = []
        for s in self._pending:
            if s.is_expired(now):
                self._recent.append(s)
                self._stats["expired"] += 1
            else:
                kept.append(s)
        self._pending = kept
        # Тримаємо recent ≤ 30.
        if len(self._recent) > 30:
            self._recent = self._recent[-30:]

    def _pop_locked(self, sid: str) -> Optional[Suggestion]:
        for i, s in enumerate(self._pending):
            if s.id == sid:
                return self._pending.pop(i)
        return None

    def _next_id(self) -> str:
        with self._lock:
            self._counter += 1
            return f"a{int(time.time()*1000)}-{self._counter}"

    def _make(self, *, source: str, priority: int, text: str,
              command: Optional[str] = None, confidence: float = 0.5,
              meta: Optional[dict] = None) -> Suggestion:
        return Suggestion(
            id=self._next_id(),
            ts=int(time.time()),
            source=source,
            priority=int(priority),
            text=text,
            command=command,
            confidence=float(confidence),
            expires_at=int(time.time()) + self._expire_after_sec,
            meta=dict(meta or {}),
        )

    def _cooldown_key(self, s: Suggestion) -> str:
        # Ключ дедуплікації: source + meta.dedup_key OR text first 80 chars.
        dk = (s.meta or {}).get("dedup_key")
        if dk:
            return f"{s.source}:{dk}"
        return f"{s.source}:{(s.command or s.text)[:80].lower()}"

    def _notify(self) -> None:
        snap = self.snapshot()
        with self._lock:
            subs = list(self._subs)
        for cb in subs:
            try:
                cb(snap)
            except Exception as e:
                logger.warning(f"[Anticipator] subscriber failed: {e}")

    # --- генератори ---------------------------------------------------
    def _from_routines(self) -> List[Suggestion]:
        """З уроків Reflector з scope='routine' робимо пропозиції."""
        try:
            from core.episodic_memory import get_episodic_memory
            em = get_episodic_memory()
            lessons = em.recent(n=20, kinds=["lesson"])
        except Exception as e:
            logger.debug(f"[Anticipator] no episodic for routines: {e}")
            return []
        out: List[Suggestion] = []
        seen_cmds: set = set()
        for ev in lessons:
            scope = (ev.meta or {}).get("scope")
            if scope != "routine":
                continue
            cmd = (ev.meta or {}).get("command")
            count = int((ev.meta or {}).get("count", 0))
            if not cmd or count < 3 or cmd in seen_cmds:
                continue
            seen_cmds.add(cmd)
            conf = float((ev.meta or {}).get("confidence", 0.5))
            out.append(self._make(
                source=SOURCE_ROUTINE,
                priority=PRIORITY_NORMAL,
                text=f"Хочеш, щоб я зараз виконав «{cmd}»? Ти це робиш регулярно.",
                command=cmd,
                confidence=conf,
                meta={"dedup_key": f"routine:{cmd}", "occurrences": count},
            ))
        return out

    def _from_followups(self) -> List[Suggestion]:
        """Провалені цілі з GoalStack history → пропозиція повторити."""
        try:
            from core.goal_stack import get_goal_stack
            history = get_goal_stack().snapshot().get("history", [])
        except Exception:
            return []
        out: List[Suggestion] = []
        # Беремо останні 5 завершених, шукаємо provals.
        for g in history[-5:]:
            if g.get("success") is True:
                continue
            text = (g.get("text") or "").strip()
            if not text:
                continue
            gid = g.get("id") or text[:40]
            out.append(self._make(
                source=SOURCE_FOLLOWUP,
                priority=PRIORITY_NORMAL,
                text=f"Минула спроба «{text[:80]}» провалилась. Спробуємо ще раз?",
                command=f"склади план: {text}",
                confidence=0.6,
                meta={"dedup_key": f"followup:{gid}",
                      "goal_id": g.get("id"),
                      "verdict": g.get("verdict")},
            ))
        return out

    def _from_time_patterns(self) -> List[Suggestion]:
        """Команди, що повторюються в одному часовому вікні (±X хв) у різні дні."""
        try:
            from core.episodic_memory import get_episodic_memory
            em = get_episodic_memory()
            since = int(time.time()) - 14 * 86400  # 2 тижні
            events = em.recent(n=2000, kinds=["command"], since=since)
        except Exception:
            return []
        # Групуємо за командою + годиною.
        bucket: Dict[tuple, List[int]] = defaultdict(list)
        for ev in events:
            if ev.actor != "user":
                continue
            text = (ev.text or "").strip().lower()
            if not text or len(text) > 80:
                continue
            try:
                hh = datetime.fromtimestamp(ev.ts).hour
            except Exception:
                continue
            bucket[(text, hh)].append(ev.ts)
        out: List[Suggestion] = []
        now = datetime.now()
        for (text, hh), times in bucket.items():
            # Унікальні дні.
            unique_days = {datetime.fromtimestamp(t).date() for t in times}
            if len(unique_days) < self._time_min_repeats:
                continue
            # Чи зараз потрапляємо в це вікно?
            cur_min = now.hour * 60 + now.minute
            tgt_min = hh * 60 + 0
            diff = abs(cur_min - tgt_min)
            diff = min(diff, 1440 - diff)
            if diff > self._time_window_min:
                continue
            out.append(self._make(
                source=SOURCE_TIME_PATTERN,
                priority=PRIORITY_NORMAL,
                text=f"Зараз приблизно той час, коли ти зазвичай кажеш «{text}» "
                     f"({len(unique_days)} днів поспіль о {hh:02d}:00). Виконати?",
                command=text,
                confidence=min(0.4 + 0.1 * len(unique_days), 0.9),
                meta={"dedup_key": f"time:{text}:{hh}",
                      "hour": hh, "unique_days": len(unique_days)},
            ))
        return out

    def _from_system(self) -> List[Suggestion]:
        """Батарея, тощо."""
        out: List[Suggestion] = []
        try:
            import psutil
            bat = psutil.sensors_battery()
            if bat and bat.percent <= self._battery_low_pct and not bat.power_plugged:
                out.append(self._make(
                    source=SOURCE_SYSTEM,
                    priority=PRIORITY_HIGH,
                    text=f"Заряд батареї {int(bat.percent)}%. Підключи зарядку.",
                    command=None,
                    confidence=1.0,
                    meta={"dedup_key": "system:battery_low",
                          "battery_pct": int(bat.percent)},
                ))
        except Exception:
            pass
        return out


# ---------------------------------------------------------------------------
_SINGLETON: Optional[Anticipator] = None
_SINGLETON_LOCK = threading.Lock()


def get_anticipator() -> Anticipator:
    global _SINGLETON
    with _SINGLETON_LOCK:
        if _SINGLETON is None:
            _SINGLETON = Anticipator()
        return _SINGLETON
