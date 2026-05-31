"""Reflector — Шар 7 архітектури мислення (метакогніція).

Періодично (або по команді) аналізує:
  • історію GoalStack (успіхи/провали/replan-важкі цілі)
  • Episodic Memory (повторювані помилки/успіхи скілів)
  • статистику стадій з ThoughtLog
і генерує короткі "уроки" (Lesson) — інсайти про власну поведінку,
які потім підтягуються Planner-ом у промпт перед плануванням.

Lesson зберігається як подія в EpisodicMemory з kind='lesson' (отже —
автоматично пресистентна, шукається семантично, чиститься retention-ом).

API:
    reflector = get_reflector()
    lessons   = reflector.reflect_now(window_hours=24)
    relevant  = reflector.recall(query, top_k=3)
    reflector.start(interval_sec=1800)   # фоновий скан
    reflector.subscribe(cb)              # cb(snapshot_dict)
    reflector.snapshot()                 # {"recent": [Lesson...], "stats": {...}}
"""
from __future__ import annotations

import re
import time
import json
import logging
import threading
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from typing import Callable, List, Optional, Dict, Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
SCOPE_SKILL      = "skill"      # урок про конкретний скіл
SCOPE_GOAL       = "goal"       # урок про тип цілі
SCOPE_ROUTINE    = "routine"    # повторюваний патерн користувача
SCOPE_GLOBAL     = "global"     # загальне спостереження
SCOPE_LLM        = "llm"        # урок, згенерований LLM
SCOPE_USER_TRAIT = "user_trait" # стійка риса користувача (час, тон, теми)


@dataclass
class Lesson:
    id: int
    ts: int
    text: str
    scope: str
    confidence: float          # 0..1
    evidence: List[int]        # event_ids у Episodic
    meta: Dict[str, Any]

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


# ---------------------------------------------------------------------------
def _load_config() -> Dict[str, Any]:
    try:
        import config
    except Exception:
        config = None  # type: ignore
    g = lambda k, d: getattr(config, k, d) if config else d
    return {
        "enabled":         bool(g("REFLECTOR_ENABLED", True)),
        "interval_sec":    int(g("REFLECTOR_INTERVAL_SEC", 0)),
        "window_hours":    int(g("REFLECTOR_WINDOW_HOURS", 24)),
        "min_events":      int(g("REFLECTOR_MIN_EVENTS", 5)),
        "use_llm":         bool(g("REFLECTOR_USE_LLM", False)),
        "max_lessons":     int(g("REFLECTOR_MAX_LESSONS", 5)),
        "lesson_ttl_days": int(g("REFLECTOR_LESSON_TTL_DAYS", 60)),
    }


# ---------------------------------------------------------------------------
class Reflector:
    """Метакогнітивний модуль: спостерігає за собою і робить висновки."""

    def __init__(self, llm_call: Optional[Callable[[str], Optional[str]]] = None):
        cfg = _load_config()
        self._enabled         = cfg["enabled"]
        self._interval        = cfg["interval_sec"]
        self._window_hours    = cfg["window_hours"]
        self._min_events      = cfg["min_events"]
        self._use_llm         = cfg["use_llm"]
        self._max_lessons     = cfg["max_lessons"]
        self._lesson_ttl_days = cfg["lesson_ttl_days"]

        self._llm_call        = llm_call
        self._lock            = threading.RLock()
        self._subs: List[Callable[[dict], None]] = []
        self._stop_evt        = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_run_ts: Optional[int] = None
        self._last_lessons: List[Lesson] = []
        self._stats           = {"runs": 0, "lessons_total": 0, "errors": 0}

    # ------------------------------------------------------------------
    # Публічне API
    # ------------------------------------------------------------------
    def attach_llm(self, llm_call: Callable[[str], Optional[str]]) -> None:
        with self._lock:
            self._llm_call = llm_call

    def subscribe(self, cb: Callable[[dict], None]) -> Callable[[], None]:
        with self._lock:
            self._subs.append(cb)

        def _unsub() -> None:
            with self._lock:
                try:
                    self._subs.remove(cb)
                except ValueError:
                    pass
        return _unsub

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "enabled":      self._enabled,
                "interval_sec": self._interval,
                "last_run_ts":  self._last_run_ts,
                "stats":        dict(self._stats),
                "recent":       [l.to_dict() for l in self._last_lessons],
            }

    # ------------------------------------------------------------------
    def start(self, interval_sec: Optional[int] = None) -> None:
        """Запустити фоновий цикл рефлексії. interval_sec=0 — лише ручний режим."""
        if interval_sec is not None:
            self._interval = int(interval_sec)
        if self._interval <= 0:
            logger.info("[Reflector] фоновий режим вимкнено (interval=0)")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._loop, name="ReflectorLoop", daemon=True
        )
        self._thread.start()
        logger.info(f"[Reflector] стартував із інтервалом {self._interval}s")

    def stop(self) -> None:
        self._stop_evt.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    # ------------------------------------------------------------------
    def reflect_now(self, window_hours: Optional[int] = None) -> List[Lesson]:
        """Один прохід рефлексії. Повертає створені уроки."""
        if not self._enabled:
            return []
        win = window_hours or self._window_hours
        try:
            episodes = self._gather_episodes(win)
            goals    = self._gather_goal_history()
            stages   = self._gather_thought_stats()
        except Exception as e:
            logger.error(f"[Reflector] збір даних провалився: {e}")
            with self._lock:
                self._stats["errors"] += 1
            return []

        if len(episodes) + len(goals) < self._min_events:
            logger.info(
                f"[Reflector] замало даних "
                f"(events={len(episodes)} + goals={len(goals)} < {self._min_events})"
            )
            return []

        lessons: List[Lesson] = []
        # 1) Rule-based детектори.
        try:
            lessons.extend(self._detect_skill_failures(episodes))
        except Exception as e:
            logger.warning(f"[Reflector] skill_failures: {e}")
        try:
            lessons.extend(self._detect_skill_reliable(episodes))
        except Exception as e:
            logger.warning(f"[Reflector] skill_reliable: {e}")
        try:
            lessons.extend(self._detect_goal_replans(goals))
        except Exception as e:
            logger.warning(f"[Reflector] goal_replans: {e}")
        try:
            lessons.extend(self._detect_routines(episodes))
        except Exception as e:
            logger.warning(f"[Reflector] routines: {e}")
        try:
            lessons.extend(self._detect_user_traits(episodes))
        except Exception as e:
            logger.warning(f"[Reflector] user_traits: {e}")

        # 2) Опційно — LLM-аналіз для більш креативних інсайтів.
        if self._use_llm and self._llm_call:
            try:
                lessons.extend(self._llm_reflect(episodes, goals, stages))
            except Exception as e:
                logger.warning(f"[Reflector] LLM reflect: {e}")

        # Обрізка та запис у пам'ять.
        lessons = self._dedup_and_trim(lessons)
        saved   = self._persist(lessons)

        with self._lock:
            self._last_run_ts  = int(time.time())
            self._last_lessons = saved
            self._stats["runs"]          += 1
            self._stats["lessons_total"] += len(saved)

        # Сповіщаємо.
        try:
            from core.thought_log import think
            think("info", f"reflect: {len(saved)} нових уроків", scope="reflector")
        except Exception as e:
            logger.debug(f"reflector: thought_log emit пропущено: {e}", exc_info=True)
        self._notify()
        return saved

    # ------------------------------------------------------------------
    def recall(self, query: str, top_k: int = 3) -> List[dict]:
        """Знайти релевантні уроки для injection у Planner-промпт."""
        try:
            from core.episodic_memory import get_episodic_memory
            em = get_episodic_memory()
            results = em.search(query, top_k=top_k, kinds=["lesson"])
        except Exception as e:
            logger.warning(f"[Reflector] recall failed: {e}")
            return []
        out: List[dict] = []
        for ev in results or []:
            out.append({
                "text":       ev.text,
                "scope":      ev.meta.get("scope", SCOPE_GLOBAL),
                "confidence": float(ev.meta.get("confidence", 0.5)),
                "ts":         ev.ts,
                "score":      float(getattr(ev, "score", 0.0)),
            })
        return out

    # ------------------------------------------------------------------
    # Внутрішнє
    # ------------------------------------------------------------------
    def _loop(self) -> None:
        # Мінімальна затримка перед першим стартом, щоб не бити по щойно піднятій системі.
        first_delay = min(60, self._interval)
        if self._stop_evt.wait(first_delay):
            return
        while not self._stop_evt.is_set():
            try:
                self.reflect_now()
            except Exception as e:
                logger.error(f"[Reflector] loop error: {e}")
            if self._stop_evt.wait(self._interval):
                return

    # --- джерела даних --------------------------------------------------
    def _gather_episodes(self, window_hours: int) -> list:
        try:
            from core.episodic_memory import get_episodic_memory
            em = get_episodic_memory()
            since = int(time.time()) - window_hours * 3600
            # Не беремо самі lesson-події, інакше станеться рекурсія.
            return [e for e in em.recent(n=500, since=since) if e.kind != "lesson"]
        except Exception as e:
            logger.warning(f"[Reflector] episodic недоступна: {e}")
            return []

    def _gather_goal_history(self) -> list:
        try:
            from core.goal_stack import get_goal_stack
            return get_goal_stack().snapshot().get("history", [])
        except Exception:
            return []

    def _gather_thought_stats(self) -> dict:
        try:
            from core.thought_log import get_thought_log
            return get_thought_log().stats()
        except Exception:
            return {}

    # --- детектори ------------------------------------------------------
    def _detect_skill_failures(self, episodes) -> List[Lesson]:
        """Скіл, що часто фейлить (>=3 фейли і fail_rate>=50%)."""
        per_skill_total = Counter()
        per_skill_fail  = Counter()
        evidence: Dict[str, List[int]] = defaultdict(list)
        for ev in episodes:
            skill = ev.meta.get("skill") if isinstance(ev.meta, dict) else None
            if not skill:
                continue
            per_skill_total[skill] += 1
            if ev.kind == "error" or ev.meta.get("ok") is False:
                per_skill_fail[skill] += 1
                evidence[skill].append(ev.id)
        out: List[Lesson] = []
        for skill, fails in per_skill_fail.items():
            total = per_skill_total[skill]
            if fails < 3 or total == 0:
                continue
            rate = fails / total
            if rate < 0.5:
                continue
            out.append(self._make(
                text=f"Скіл «{skill}» часто фейлить ({fails}/{total}, {int(rate*100)}%) — "
                     f"варто перевірити вхідні дані або обрати альтернативний шлях.",
                scope=SCOPE_SKILL,
                confidence=min(0.5 + rate * 0.5, 0.95),
                evidence=evidence[skill][:5],
                meta={"skill": skill, "fail_rate": rate, "fails": fails, "total": total},
            ))
        return out

    def _detect_skill_reliable(self, episodes) -> List[Lesson]:
        """Скіл, що завжди успішний (>=5 викликів, fail_rate==0)."""
        per_skill_total = Counter()
        per_skill_fail  = Counter()
        for ev in episodes:
            skill = ev.meta.get("skill") if isinstance(ev.meta, dict) else None
            if not skill:
                continue
            per_skill_total[skill] += 1
            if ev.kind == "error" or ev.meta.get("ok") is False:
                per_skill_fail[skill] += 1
        out: List[Lesson] = []
        for skill, total in per_skill_total.items():
            if total < 5 or per_skill_fail[skill] != 0:
                continue
            out.append(self._make(
                text=f"Скіл «{skill}» стабільно успішний ({total} запусків поспіль) — "
                     f"можна довіряти йому без додаткової перевірки.",
                scope=SCOPE_SKILL,
                confidence=0.7,
                evidence=[],
                meta={"skill": skill, "successes": total},
            ))
        return out

    def _detect_goal_replans(self, goals) -> List[Lesson]:
        """Цілі, що потребували replan >= 1 рази — патерн поганого першого плану."""
        out: List[Lesson] = []
        if not goals:
            return out
        heavy = [g for g in goals if (g.get("replan_count") or 0) >= 1]
        if len(heavy) < 2:
            return out
        # Згрупуємо за першими словами цілі (грубий ключ теми).
        topic_counter: Counter = Counter()
        examples: Dict[str, str] = {}
        for g in heavy:
            text = (g.get("text") or "").strip().lower()
            key = " ".join(re.findall(r"\w+", text)[:2]) or "?"
            topic_counter[key] += 1
            examples.setdefault(key, g.get("text") or "")
        for key, n in topic_counter.most_common(3):
            if n < 2:
                continue
            out.append(self._make(
                text=f"Цілі типу «{examples[key]}» потребують переплану в {n} випадках — "
                     f"початковий план зазвичай неповний; розкладай детальніше з самого початку.",
                scope=SCOPE_GOAL,
                confidence=min(0.4 + 0.15 * n, 0.9),
                evidence=[],
                meta={"topic_key": key, "occurrences": n},
            ))
        return out

    def _detect_routines(self, episodes) -> List[Lesson]:
        """Користувацькі команди, що повторюються 3+ рази у вікні."""
        cmd_counter: Counter = Counter()
        for ev in episodes:
            if ev.kind != "command" or ev.actor != "user":
                continue
            text = (ev.text or "").strip().lower()
            if not text or len(text) > 80:
                continue
            cmd_counter[text] += 1
        out: List[Lesson] = []
        for cmd, n in cmd_counter.most_common(3):
            if n < 3:
                continue
            out.append(self._make(
                text=f"Користувач часто каже «{cmd}» ({n} разів за період) — "
                     f"це його рутина; можна пропонувати її проактивно.",
                scope=SCOPE_ROUTINE,
                confidence=min(0.4 + 0.1 * n, 0.85),
                evidence=[],
                meta={"command": cmd, "count": n},
            ))
        return out

    def _detect_user_traits(self, episodes) -> List[Lesson]:
        """Уроки ПРО КОРИСТУВАЧА: час доби, довжина команд, теми, тон.

        Не про власну поведінку Jarvis, а про стійкі риси людини.
        Допомагає підлаштовувати стиль відповіді (короткий/розгорнутий,
        нічний/денний режим, очікувана тематика).
        """
        out: List[Lesson] = []
        # Беремо лише команди користувача.
        user_cmds = [
            e for e in episodes
            if e.kind == "command" and e.actor == "user" and (e.text or "").strip()
        ]
        if len(user_cmds) < 5:
            return out

        # ---- 1) Час доби активності --------------------------------
        hour_buckets = Counter()
        for e in user_cmds:
            try:
                hour = time.localtime(e.ts).tm_hour
            except Exception:
                continue
            if   5 <= hour < 12: hour_buckets["ранок"]   += 1
            elif 12 <= hour < 18: hour_buckets["день"]   += 1
            elif 18 <= hour < 23: hour_buckets["вечір"]  += 1
            else:                 hour_buckets["ніч"]    += 1
        if hour_buckets:
            top_period, top_n = hour_buckets.most_common(1)[0]
            share = top_n / len(user_cmds)
            if share >= 0.55 and top_n >= 4:
                out.append(self._make(
                    text=f"Користувач переважно активний у {top_period} "
                         f"({int(share*100)}% запитів) — у цей час доречні "
                         f"проактивні підказки, в інші — тиша.",
                    scope=SCOPE_USER_TRAIT,
                    confidence=min(0.5 + share * 0.4, 0.9),
                    evidence=[],
                    meta={"trait": "active_period", "period": top_period,
                          "share": round(share, 2)},
                ))

        # ---- 2) Довжина команд --------------------------------------
        lens = [len((e.text or "").split()) for e in user_cmds]
        if lens:
            avg = sum(lens) / len(lens)
            if avg <= 3.0:
                out.append(self._make(
                    text=f"Користувач формулює дуже коротко (~{avg:.1f} слів) — "
                         f"відповідай так само лаконічно, без розлогих пояснень.",
                    scope=SCOPE_USER_TRAIT,
                    confidence=0.75,
                    evidence=[],
                    meta={"trait": "terse", "avg_words": round(avg, 1)},
                ))
            elif avg >= 12.0:
                out.append(self._make(
                    text=f"Користувач любить розгорнуті формулювання (~{avg:.1f} слів) — "
                         f"можеш давати докладніші відповіді з контекстом.",
                    scope=SCOPE_USER_TRAIT,
                    confidence=0.7,
                    evidence=[],
                    meta={"trait": "verbose", "avg_words": round(avg, 1)},
                ))

        # ---- 3) Повторювані тематичні слова -------------------------
        STOP = {
            "я","ти","мені","тобі","це","той","та","так","ні","щоб","як",
            "що","де","коли","для","і","й","а","але","чи","в","у","на",
            "з","до","від","про","за","по","the","a","an","and","or","to",
            "of","in","on","is","it","be","you","i","me","my","can","do",
            "jarvis","джарвіс","нео","please","будь","ласка",
        }
        word_counter: Counter = Counter()
        for e in user_cmds:
            for w in re.findall(r"[\w']+", (e.text or "").lower()):
                if len(w) >= 3 and w not in STOP and not w.isdigit():
                    word_counter[w] += 1
        themes = [(w, n) for w, n in word_counter.most_common(5) if n >= 3]
        if len(themes) >= 2:
            top = ", ".join(f"«{w}»({n})" for w, n in themes[:3])
            out.append(self._make(
                text=f"Часті теми у запитах користувача: {top}. "
                     f"Тримай контекст готовим — ймовірно, скоро буде продовження.",
                scope=SCOPE_USER_TRAIT,
                confidence=0.65,
                evidence=[],
                meta={"trait": "themes", "top": [w for w, _ in themes[:5]]},
            ))

        # ---- 4) Поточний емоційний фон (з Affect) -------------------
        try:
            from core.affect import get_affect
            st = get_affect().state()
            if st.samples >= 8:
                if st.stress >= 0.5:
                    out.append(self._make(
                        text=f"Останнім часом користувач під стресом "
                             f"(stress={st.stress:.2f}) — уникай нав'язливих "
                             f"пропозицій, говори м'якше і коротше.",
                        scope=SCOPE_USER_TRAIT,
                        confidence=min(0.5 + st.stress * 0.4, 0.9),
                        evidence=[],
                        meta={"trait": "baseline_stress", "stress": round(st.stress, 2)},
                    ))
                elif st.valence >= 0.4:
                    out.append(self._make(
                        text=f"Користувач у позитивному настрої "
                             f"(valence=+{st.valence:.2f}) — добрий момент для "
                             f"навчання нових скілів чи планування великих цілей.",
                        scope=SCOPE_USER_TRAIT,
                        confidence=0.7,
                        evidence=[],
                        meta={"trait": "baseline_positive",
                              "valence": round(st.valence, 2)},
                    ))
                elif st.valence <= -0.3:
                    out.append(self._make(
                        text=f"Користувач помітно втомлений/незадоволений "
                             f"(valence={st.valence:+.2f}) — пропонуй маленькі кроки, "
                             f"не великі плани; підтверджуй частіше.",
                        scope=SCOPE_USER_TRAIT,
                        confidence=0.7,
                        evidence=[],
                        meta={"trait": "baseline_low",
                              "valence": round(st.valence, 2)},
                    ))
        except Exception as e:
            logger.debug(f"[Reflector] affect baseline skip: {e}")

        return out

    def _llm_reflect(self, episodes, goals, stages) -> List[Lesson]:
        """Опційний шар: попросити LLM знайти 1-3 інсайти у вільній формі."""
        if not self._llm_call:
            return []
        # Готуємо стислий контекст.
        ep_lines = [f"  • [{e.kind}] {e.text[:140]}" for e in episodes[:30]]
        goal_lines = []
        for g in goals[:10]:
            ok = g.get("success")
            mark = "✓" if ok else ("✗" if ok is False else "?")
            goal_lines.append(
                f"  • {mark} {g.get('text','')[:120]} "
                f"(replan={g.get('replan_count', 0)})"
            )
        prompt = (
            "Ти — рефлексивний модуль AI-асистента. Проаналізуй останній досвід "
            "і знайди 1-3 КОРОТКІ уроки про власну поведінку (укр. мова).\n\n"
            "Останні події:\n" + "\n".join(ep_lines or ["  (немає)"]) + "\n\n"
            "Останні цілі (✓ успіх / ✗ провал):\n" + "\n".join(goal_lines or ["  (немає)"]) + "\n\n"
            'ВІДПОВІДЬ — лише JSON: {"lessons":[{"text":"...","confidence":0.0-1.0}]}\n'
            "Жодного зайвого тексту."
        )
        try:
            raw = self._llm_call(prompt)
        except Exception as e:
            logger.warning(f"[Reflector] LLM call failed: {e}")
            return []
        if not raw:
            return []
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return []
        try:
            data = json.loads(m.group(0))
        except Exception:
            try:
                data = json.loads(m.group(0).replace("'", '"'))
            except Exception:
                return []
        out: List[Lesson] = []
        for item in (data.get("lessons") or [])[:3]:
            text = (item.get("text") or "").strip()
            if not text or len(text) < 10:
                continue
            conf = float(item.get("confidence", 0.5))
            conf = max(0.0, min(conf, 1.0))
            out.append(self._make(
                text=text[:300], scope=SCOPE_LLM, confidence=conf,
                evidence=[], meta={"source": "llm"},
            ))
        return out

    # --- утиліти --------------------------------------------------------
    def _make(self, *, text: str, scope: str, confidence: float,
              evidence: List[int], meta: Dict[str, Any]) -> Lesson:
        return Lesson(
            id=0, ts=int(time.time()), text=text, scope=scope,
            confidence=float(confidence), evidence=list(evidence or []),
            meta=dict(meta or {}),
        )

    def _dedup_and_trim(self, lessons: List[Lesson]) -> List[Lesson]:
        """Прибираємо дублікати за text і обрізаємо до max_lessons за confidence."""
        seen: Dict[str, Lesson] = {}
        for l in lessons:
            key = l.text.strip().lower()
            if key not in seen or seen[key].confidence < l.confidence:
                seen[key] = l
        ranked = sorted(seen.values(), key=lambda x: x.confidence, reverse=True)
        return ranked[: self._max_lessons]

    def _persist(self, lessons: List[Lesson]) -> List[Lesson]:
        """Записати уроки в EpisodicMemory; повернути з реальними id."""
        if not lessons:
            return []
        try:
            from core.episodic_memory import get_episodic_memory
            em = get_episodic_memory()
        except Exception as e:
            logger.warning(f"[Reflector] persist: episodic недоступна: {e}")
            return lessons
        saved: List[Lesson] = []
        for l in lessons:
            try:
                meta = {**l.meta, "scope": l.scope, "confidence": l.confidence,
                        "evidence": l.evidence}
                eid = em.log("lesson", l.text, actor="jarvis",
                             tags=["lesson", l.scope], meta=meta)
                if eid > 0:
                    l.id = eid
                saved.append(l)
            except Exception as e:
                logger.warning(f"[Reflector] persist lesson failed: {e}")
        return saved

    def _notify(self) -> None:
        snap = self.snapshot()
        with self._lock:
            subs = list(self._subs)
        for cb in subs:
            try:
                cb(snap)
            except Exception as e:
                logger.warning(f"[Reflector] subscriber failed: {e}")


# ---------------------------------------------------------------------------
def get_reflector() -> Reflector:
    # Зберігання делеговано у core.container (єдине джерело правди).
    from core.container import get_container
    return get_container().get_or_create("reflector", Reflector)
