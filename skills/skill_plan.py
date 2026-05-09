"""Запуск планувальника з голосу.

Тригери:
  • "склади план"
  • "зроби покроково"
  • "виконай послідовно"
  • "зроби кілька дій"
  • "сплануй задачу"

Приклади:
  "Сплануй задачу: знайди останні новини про погоду в Сумах і запам'ятай головне"
  "Зроби покроково: відкрий vscode, потім запиши нагадування на завтра"
"""
from __future__ import annotations

import logging

import config
from core.planner import Planner, Executor, build_route_descriptions
from core.critic import Critic
from core.goal_stack import get_goal_stack, STATUS_DONE, STATUS_FAILED

logger = logging.getLogger(__name__)

TRIGGERS = [
    "склади план",
    "зроби покроково",
    "виконай послідовно",
    "зроби кілька дій",
    "сплануй задачу",
    "сплануй і зроби",
]

_MAX_STEPS = 5
_MAX_REPLANS = 1   # скільки разів максимум критик може попросити переплан


def _strip_triggers(cmd: str) -> str:
    text = cmd
    for t in sorted(TRIGGERS, key=len, reverse=True):
        idx = text.lower().find(t.lower())
        if idx >= 0:
            text = (text[:idx] + text[idx + len(t):]).strip(" :,.\u2014-")
            break
    return text.strip()


def _format_summary(goal: str, attempts) -> str:
    if not attempts:
        return f"Не вдалося скласти план для: {goal!r}"
    lines = [f"Ціль: {goal!r}"]
    for i, (results, verdict) in enumerate(attempts, start=1):
        lines.append(f"— Спроба {i} ({len(results)} кроків):")
        for r in results:
            status = "OK" if r.ok else f"FAIL ({r.error})"
            snippet = (r.output or "(без виводу)").splitlines()[0][:120]
            lines.append(f"    [{r.step.n}] {r.step.skill} — {status} — {snippet}")
        lines.append(f"    Критик: success={verdict.success} ({verdict.source}, conf={verdict.confidence:.2f}) — {verdict.reason}")
        if verdict.suggestion and not verdict.success:
            lines.append(f"    Підказка: {verdict.suggestion}")
    return "\n".join(lines)


def execute(cmd, jarvis):
    goal = _strip_triggers(cmd)
    if not goal:
        return "Скажи, яку ціль я маю спланувати. Наприклад: 'склади план: знайди погоду і запам'ятай'."

    if not getattr(jarvis, "llm", None):
        return "Планувальник недоступний — LLM не ініціалізовано."

    routes = build_route_descriptions(config.TRIGGERS, getattr(jarvis, "dynamic_skills", []))
    llm_call = lambda p: jarvis.llm.generate_response(p, is_system_call=True)
    planner = Planner(llm_call=llm_call, route_descriptions=routes, max_steps=_MAX_STEPS)
    critic = Critic(llm_call=llm_call)
    executor = Executor()

    # Working Memory: реєструємо ціль.
    wm = getattr(jarvis, "working_memory", None)
    if wm is not None:
        try:
            wm.add_goal(goal)
            wm.set_topic(f"план: {goal[:60]}")
        except Exception:
            pass

    jarvis.audio.speak("Планую дії, зачекай.")
    attempts = []   # [(results, verdict), ...]
    hint = ""
    final_verdict = None
    gs = get_goal_stack()
    goal_pushed = False

    for attempt in range(_MAX_REPLANS + 1):
        plan = planner.plan(goal, hint=hint)
        if not plan:
            jarvis.audio.speak("Не вдалося скласти план.")
            break

        # GoalStack: push або replan.
        try:
            if not goal_pushed:
                gs.push(goal, plan)
                goal_pushed = True
            else:
                gs.replan(plan)
        except Exception:
            pass

        plan_text = "\n".join(f"  [{s.n}] {s.skill}: {s.input}" for s in plan)
        logger.info(f"[plan/attempt {attempt+1}] {goal!r}\n{plan_text}")
        jarvis.audio.speak(f"Спроба {attempt+1}: {len(plan)} кроків.")

        results = executor.run(plan, jarvis)
        verdict = critic.evaluate(goal, results)
        attempts.append((results, verdict))
        final_verdict = verdict
        logger.info(f"[critic] {verdict}")

        # GoalStack: вердикт.
        try:
            gs.set_verdict(verdict.success, verdict.reason)
        except Exception:
            pass

        if verdict.success or attempt == _MAX_REPLANS:
            break

        # Готуємось до replan: формуємо підказку.
        hint = verdict.suggestion or verdict.reason
        jarvis.audio.speak(f"Не вийшло. Перепланую: {verdict.reason[:80]}")

    # GoalStack: pop.
    if goal_pushed:
        try:
            gs.pop(STATUS_DONE if (final_verdict and final_verdict.success) else STATUS_FAILED)
        except Exception:
            pass

    # Завершення цілі у WM.
    if wm is not None and final_verdict and final_verdict.success:
        try:
            wm.complete_goal(goal)
        except Exception:
            pass

    # Озвучуємо короткий підсумок.
    if final_verdict:
        verdict_phrase = "Ціль досягнута." if final_verdict.success else f"Ціль не досягнута: {final_verdict.reason[:120]}"
        jarvis.audio.speak(verdict_phrase)

    # Episodic Memory: лог завершеного плану.
    em = getattr(jarvis, "episodic_memory", None)
    if em is not None and final_verdict is not None:
        try:
            em.log(
                kind="plan",
                text=goal,
                actor="user",
                tags=["plan", "success" if final_verdict.success else "fail"],
                meta={
                    "attempts": len(attempts),
                    "verdict": {
                        "success": final_verdict.success,
                        "confidence": final_verdict.confidence,
                        "reason": final_verdict.reason,
                        "source": final_verdict.source,
                    },
                    "steps": [s.step.to_dict() for s, _ in [(r, None) for r in attempts[-1][0]]],
                },
            )
        except Exception as exc:
            logger.debug(f"episodic log (plan) failed: {exc}")

    return _format_summary(goal, attempts)
