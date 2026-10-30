"""Critic — Шар 4 архітектури мислення.

Після виконання плану цей модуль звертається до LLM з питанням:
  • чи досягнута ціль?
  • якщо ні — чому і що поправити в наступному плані?

Повертає `Verdict`. На основі нього `skill_plan` може зробити replan
(до `MAX_REPLANS` разів), передаючи suggestion як підказку планувальнику.

Також є простий **rule-based** критик: якщо всі кроки впали —
автоматично success=False без LLM-запиту.
"""
from __future__ import annotations

import re
import json
import logging
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class Verdict:
    success: bool
    confidence: float        # 0..1
    reason: str              # коротке пояснення
    suggestion: str = ""     # підказка планувальнику для replan
    source: str = "llm"      # llm | rule | empty


class Critic:
    _PROMPT = (
        "Ти — самокритик AI-асистента {name}. Оціни, чи досягнута ціль користувача.\n\n"
        "ЦІЛЬ: {goal!r}\n\n"
        "ПЛАН І РЕЗУЛЬТАТИ КРОКІВ:\n{trace}\n\n"
        "Поверни СТРОГО JSON одним рядком:\n"
        '  {{"success": true|false, "confidence": 0.0..1.0,'
        ' "reason": "коротко чому", "suggestion": "що змінити в наступному плані якщо fail (або порожньо)"}}\n'
        "Не додавай нічого крім JSON."
    )

    def __init__(self, llm_call: Optional[Callable[[str], Optional[str]]] = None):
        self._llm_call = llm_call

    # ------------------------------------------------------------------
    def evaluate(self, goal: str, results: list) -> Verdict:
        """results: List[StepResult] з core.planner."""
        from core.thought_log import think, STAGE_CRITIC
        v = self._evaluate_inner(goal, results)
        try:
            think(STAGE_CRITIC,
                  f"{'✓' if v.success else '✗'} {v.source}/{v.confidence:.2f}: {v.reason[:120]}",
                  success=v.success, suggestion=v.suggestion[:120] if v.suggestion else None)
        except Exception as e:
            logger.debug(f"critic: thought_log emit пропущено: {e}", exc_info=True)
        return v

    def _evaluate_inner(self, goal: str, results: list) -> Verdict:
        if not results:
            return Verdict(False, 1.0, "план порожній", source="empty",
                           suggestion="спробуй переформулювати ціль чіткіше")

        # Швидке правило: усі кроки впали → fail без LLM.
        if all(not r.ok for r in results):
            err = next((r.error for r in results if r.error), "усі кроки невдалі")
            return Verdict(False, 0.95, f"усі кроки впали ({err})",
                           suggestion="використай інші маршрути або розбий ціль інакше",
                           source="rule")

        # Якщо LLM немає — обмежуємось правилом «більшість успішна».
        ok_count = sum(1 for r in results if r.ok)
        if not self._llm_call:
            success = ok_count == len(results)
            return Verdict(success,
                           0.6,
                           f"{ok_count}/{len(results)} кроків успішно",
                           source="rule")

        # LLM-оцінка.
        trace = self._format_trace(results)
        try:
            import config as _cfg
            name = getattr(_cfg, "ASSISTANT_NAME", "Нео")
        except Exception:
            name = "Нео"
        prompt = self._PROMPT.format(name=name, goal=goal, trace=trace)
        try:
            raw = self._llm_call(prompt)
        except Exception as e:
            logger.error(f"[Critic] LLM error: {e}")
            return Verdict(ok_count == len(results), 0.5,
                           f"LLM-критик не відповів ({e})", source="rule")
        if not raw:
            return Verdict(ok_count == len(results), 0.5,
                           "LLM-критик порожня відповідь", source="rule")

        parsed = self._parse_json(raw)
        if not parsed:
            logger.warning(f"[Critic] не вдалося розпарсити: {raw[:200]}")
            return Verdict(ok_count == len(results), 0.5,
                           "не розпарсив JSON критика", source="rule")

        return Verdict(
            success=bool(parsed.get("success", False)),
            confidence=float(parsed.get("confidence", 0.5) or 0.5),
            reason=str(parsed.get("reason", ""))[:300],
            suggestion=str(parsed.get("suggestion", ""))[:300],
            source="llm",
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _format_trace(results: list) -> str:
        lines = []
        for r in results:
            status = "OK" if r.ok else f"FAIL({r.error})"
            out = (r.output or "(без виводу)").replace("\n", " ")[:200]
            lines.append(f"  [{r.step.n}] {r.step.skill} «{r.step.input}» → {status}: {out}")
        return "\n".join(lines)

    @staticmethod
    def _parse_json(raw: str) -> Optional[dict]:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return None
        chunk = m.group(0)
        try:
            return json.loads(chunk)
        except json.JSONDecodeError:
            try:
                return json.loads(chunk.replace("'", '"'))
            except Exception:
                return None
