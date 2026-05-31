"""Planner + Executor — Layer 3 of "thinking" architecture.

Розкладає складну ціль на послідовність атомарних кроків (виклики існуючих
скілів/маршрутів) і виконує їх по порядку, передаючи контекст між ними.

Архітектура:
  goal (str)
     │
     ▼
  Planner.plan(goal, available_routes) -> List[Step]   ← LLM-as-planner (JSON)
     │
     ▼
  Executor.run(plan, jarvis)            -> List[StepResult]
     │      │ • перехоплює jarvis.enqueue_speech, щоб зібрати реальний вивід кроку
     │      │ • підставляє {1} {2} ... з попередніх результатів
     │      │ • кооперативне скасування через jarvis.cancel_event
     ▼
  узагальнена відповідь (str) → озвучується

Тригери в окремому скілі `skill_plan.py`.
"""
from __future__ import annotations

import re
import json
import time
import logging
from dataclasses import dataclass
from typing import Callable, List, Optional, Dict

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Дані
# ---------------------------------------------------------------------------
@dataclass
class Step:
    n: int
    skill: str            # ключ маршруту (config.TRIGGERS) або назва dynamic skill
    input: str            # текстова команда для execute_command, з плейсхолдерами {1},{2}...
    reason: str = ""

    def to_dict(self) -> dict:
        return {"n": self.n, "skill": self.skill, "input": self.input, "reason": self.reason}


@dataclass
class StepResult:
    step: Step
    output: str = ""
    ok: bool = True
    error: Optional[str] = None
    duration_sec: float = 0.0


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------
class Planner:
    """Перетворює ціль на список кроків за допомогою LLM."""

    _PROMPT = (
        "Ти — планувальник дій україномовного голосового асистента {assistant_name}.\n"
        "Розклади ціль користувача на послідовність простих кроків (1-{max_steps}).\n"
        "Кожен крок — це виклик ОДНОГО з доступних маршрутів/скілів.\n\n"
        "Доступні маршрути (skill — короткий опис):\n{routes_desc}\n\n"
        "{lessons_block}"
        "Правила:\n"
        "  • Якщо ціль розв'язується одним кроком — поверни ОДИН крок.\n"
        "  • Якщо ціль не зрозуміла або не потребує дій — поверни порожній plan.\n"
        "  • У полі input пиши команду рідною мовою користувача,\n"
        "    можна вживати плейсхолдери {{1}}, {{2}}... — вони будуть замінені\n"
        "    результатом відповідного попереднього кроку перед виконанням.\n"
        "  • Не вигадуй маршрутів, яких немає у списку.\n"
        "  • Якщо є УРОКИ З ДОСВІДУ — врахуй їх (особливо щодо ненадійних скілів).\n\n"
        "ВІДПОВІДЬ — лише JSON одним рядком:\n"
        '  {{"plan": [{{"n":1,"skill":"<key>","input":"<command>","reason":"<short>"}}, ...]}}\n\n'
        "Ціль: {goal!r}"
    )

    def __init__(
        self,
        llm_call: Callable[[str], Optional[str]],
        route_descriptions: Dict[str, str],
        max_steps: int = 5,
    ):
        self._llm_call = llm_call
        self._route_desc = route_descriptions
        self._max_steps = max_steps

    def plan(self, goal: str, hint: str = "") -> List[Step]:
        """`hint` — підказка від критика для replan: що змінити."""
        from core.thought_log import think, STAGE_PLAN, STAGE_REPLAN
        if not goal or not goal.strip():
            return []
        think(STAGE_REPLAN if hint else STAGE_PLAN,
              ("replan: " if hint else "plan: ") + goal[:140],
              hint=hint[:140] if hint else None)
        routes_desc = "\n".join(
            f"  • {k} — {v}" for k, v in self._route_desc.items()
        )
        full_goal = goal if not hint else f"{goal}\n\nПІДКАЗКА (попередня спроба провалилась): {hint}"

        # Шар 7: підтягуємо релевантні уроки з минулого досвіду.
        lessons_block = ""
        try:
            from core.reflector import get_reflector, SCOPE_USER_TRAIT
            ref = get_reflector()
            lessons = ref.recall(goal, top_k=3)
            # Окремо — стійкі риси користувача (важливі завжди, не тільки за темою).
            traits = ref.recall("користувач звичка", top_k=3)
            traits = [t for t in traits if t.get("scope") == SCOPE_USER_TRAIT]
            blocks = []
            if traits:
                tlines = [
                    f"  • [conf={t['confidence']:.2f}] {t['text']}"
                    for t in traits[:3]
                ]
                blocks.append("ХАРАКТЕР КОРИСТУВАЧА:\n" + "\n".join(tlines))
            if lessons:
                # Виключити user_trait, щоб не дублювалися.
                other = [l for l in lessons if l.get("scope") != SCOPE_USER_TRAIT]
                if other:
                    lines = [
                        f"  • [{l['scope']}, conf={l['confidence']:.2f}] {l['text']}"
                        for l in other
                    ]
                    blocks.append("УРОКИ З ДОСВІДУ:\n" + "\n".join(lines))
            if blocks:
                lessons_block = "\n\n".join(blocks) + "\n\n"
                think(STAGE_PLAN,
                      f"уроків підтягнуто: {len(lessons)} + traits: {len(traits)}",
                      lessons=len(lessons), traits=len(traits))
        except Exception as e:
            logger.debug(f"[Planner] reflector recall skipped: {e}")

        try:
            import config as _cfg
            assistant_name = getattr(_cfg, "ASSISTANT_NAME", "Нео")
        except Exception:
            assistant_name = "Нео"
        prompt = self._PROMPT.format(
            assistant_name=assistant_name,
            max_steps=self._max_steps,
            routes_desc=routes_desc,
            lessons_block=lessons_block,
            goal=full_goal,
        )
        try:
            raw = self._llm_call(prompt)
        except Exception as e:
            logger.error(f"[Planner] LLM error: {e}")
            return []
        if not raw:
            return []

        parsed = self._parse_json(raw)
        if not parsed or "plan" not in parsed:
            logger.warning(f"[Planner] не вдалося розпарсити: {raw[:200]}")
            return []

        steps: List[Step] = []
        for i, item in enumerate(parsed.get("plan", [])[: self._max_steps], start=1):
            if not isinstance(item, dict):
                continue
            skill = (item.get("skill") or "").strip()
            inp = (item.get("input") or "").strip()
            if not skill or not inp:
                continue
            if skill not in self._route_desc:
                logger.info(f"[Planner] пропущено невідомий skill='{skill}'")
                continue
            steps.append(Step(
                n=int(item.get("n", i)),
                skill=skill,
                input=inp,
                reason=str(item.get("reason", ""))[:200],
            ))
        return steps

    @staticmethod
    def _parse_json(raw: str) -> Optional[dict]:
        # Шукаємо найбільший JSON-об'єкт у відповіді.
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return None
        chunk = m.group(0)
        try:
            return json.loads(chunk)
        except json.JSONDecodeError:
            patched = chunk.replace("'", '"')
            try:
                return json.loads(patched)
            except Exception:
                return None


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------
class Executor:
    """Виконує план кроків через jarvis.execute_command, перехоплюючи мовний вивід."""

    _PLACEHOLDER_RE = re.compile(r"\{(\d+)\}")

    def __init__(self, max_step_seconds: float = 60.0):
        self._max_step = max_step_seconds

    def run(self, plan: List[Step], jarvis) -> List[StepResult]:
        results: List[StepResult] = []
        if not plan:
            return results

        # Перехоплюємо enqueue_speech, щоб зібрати РЕАЛЬНИЙ вивід кроку.
        # Без цього багато скілів лише озвучують і повертають "CONTINUE".
        original_enqueue = jarvis.enqueue_speech
        captured: List[str] = []

        def capture(text: str) -> None:
            if text and text != "<CLEAR_QUEUE>":
                captured.append(text)
            # Все одно проводимо в TTS — користувач хоче чути проміжки.
            try:
                original_enqueue(text)
            except Exception as e:
                logger.debug(f"planner: TTS passthrough під час захоплення плану не вдався: {e}", exc_info=True)

        for step in plan:
            # Кооперативне скасування.
            cancel = getattr(jarvis, "cancel_event", None)
            if cancel and cancel.is_set():
                results.append(StepResult(step=step, ok=False, error="cancelled"))
                break

            # Підстановка результатів попередніх кроків.
            cmd = self._substitute(step.input, results)

            try:
                from core.thought_log import think, STAGE_STEP
                think(STAGE_STEP, f"виконую [{step.n}] {step.skill}: {cmd[:120]}", n=step.n, skill=step.skill)
            except Exception as e:
                logger.debug(f"planner: thought_log emit (step) пропущено: {e}", exc_info=True)
            try:
                from core.goal_stack import get_goal_stack
                get_goal_stack().step_start(step.n)
            except Exception as e:
                logger.debug(f"planner: goal_stack.step_start пропущено: {e}", exc_info=True)

            captured.clear()
            try:
                jarvis.enqueue_speech = capture  # type: ignore[assignment]
            except Exception as e:
                logger.debug(f"planner: підміна enqueue_speech не вдалася: {e}", exc_info=True)

            t0 = time.time()
            ok = True
            err: Optional[str] = None
            ret_value: Optional[str] = None
            try:
                logger.info(f"[Executor] step {step.n} ({step.skill}): {cmd!r}")
                ret_value = jarvis.execute_command(cmd)
            except Exception as e:
                ok = False
                err = str(e)
                logger.error(f"[Executor] step {step.n} failed: {e}")
            finally:
                jarvis.enqueue_speech = original_enqueue  # type: ignore[assignment]

            elapsed = time.time() - t0
            output = "\n".join(captured).strip()
            # Якщо handler сам повернув текстову відповідь (не "CONTINUE") — теж зберігаємо.
            if ret_value and isinstance(ret_value, str) and ret_value != "CONTINUE":
                output = (output + "\n" + ret_value).strip() if output else ret_value

            results.append(StepResult(
                step=step,
                output=output[:2000],
                ok=ok and elapsed <= self._max_step,
                error=err if not ok else (None if elapsed <= self._max_step else "timeout"),
                duration_sec=round(elapsed, 2),
            ))
            try:
                from core.goal_stack import get_goal_stack
                last = results[-1]
                get_goal_stack().step_finish(
                    step.n, ok=last.ok, output=last.output,
                    error=last.error, duration_sec=last.duration_sec,
                )
            except Exception as e:
                logger.debug(f"planner: goal_stack.step_end пропущено: {e}", exc_info=True)

            # Запис у Working Memory (якщо є).
            wm = getattr(jarvis, "working_memory", None)
            if wm is not None:
                try:
                    wm.add_ai_turn(
                        f"[крок {step.n}/{step.skill}] {output[:300]}",
                        skill=f"plan:{step.skill}",
                    )
                except Exception as e:
                    logger.debug(f"planner: thought_log emit (output) пропущено: {e}", exc_info=True)

        return results

    def _substitute(self, template: str, prev_results: List[StepResult]) -> str:
        if not prev_results:
            return template

        def repl(m: re.Match) -> str:
            idx = int(m.group(1))
            for r in prev_results:
                if r.step.n == idx and r.output:
                    return r.output[:500]
            return m.group(0)

        return self._PLACEHOLDER_RE.sub(repl, template)


# ---------------------------------------------------------------------------
# Описи маршрутів — переиспользуємо те, що в IntentRouter, плюс додаємо динаміку.
# ---------------------------------------------------------------------------
def build_route_descriptions(triggers: dict, dynamic_skills: list) -> Dict[str, str]:
    """Збирає {skill_key: short_description} з вбудованих маршрутів та dyn skills."""
    # Основна частина — ті самі описи, що в IntentRouter.
    try:
        from core.intent_router import IntentRouter
        descs = dict(IntentRouter._ROUTE_DESCRIPTIONS)
    except Exception:
        descs = {}

    # Додаємо тільки наявні в TRIGGERS.
    routes: Dict[str, str] = {k: descs.get(k, "?") for k in triggers.keys()}

    # Динамічні скіли — ім'я + перший тригер як натяк.
    for sk in dynamic_skills or []:
        name = sk.get("name") or sk.get("id")
        if not name:
            continue
        first_trigger = (sk.get("triggers") or [""])[0]
        routes[f"skill:{name}"] = f"динамічна навичка ('{first_trigger}')"

    return routes
