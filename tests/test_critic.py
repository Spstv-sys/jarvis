"""Юніт-тести core.critic.Critic: rule-based та LLM-вердикти."""
import types

from core.critic import Critic, Verdict


def _sr(n, skill, inp, ok=True, output="", error=None):
    step = types.SimpleNamespace(n=n, skill=skill, input=inp)
    return types.SimpleNamespace(step=step, output=output, ok=ok, error=error, duration_sec=0.0)


def test_empty_plan_is_failure():
    v = Critic().evaluate("якась ціль", [])
    assert v.success is False and v.source == "empty"


def test_all_steps_failed_rule():
    res = [_sr(1, "weather", "x", ok=False, error="timeout"),
           _sr(2, "news", "y", ok=False, error="net")]
    v = Critic().evaluate("ціль", res)
    assert v.success is False and v.source == "rule" and v.confidence >= 0.9


def test_majority_rule_without_llm():
    all_ok = [_sr(1, "a", "x", ok=True), _sr(2, "b", "y", ok=True)]
    assert Critic().evaluate("ціль", all_ok).success is True
    mixed = [_sr(1, "a", "x", ok=True), _sr(2, "b", "y", ok=False, error="e")]
    assert Critic().evaluate("ціль", mixed).success is False


def test_llm_verdict_parsed():
    llm = lambda p: '{"success": true, "confidence": 0.88, "reason": "досягнуто", "suggestion": ""}'
    res = [_sr(1, "a", "x", ok=True), _sr(2, "b", "y", ok=False, error="e")]  # не all-failed → дійде до LLM
    v = Critic(llm_call=llm).evaluate("ціль", res)
    assert v.source == "llm" and v.success is True and abs(v.confidence - 0.88) < 1e-6


def test_llm_single_quote_json_fallback():
    llm = lambda p: "сміття {'success': false, 'confidence': 0.4, 'reason': 'ні'} хвіст"
    res = [_sr(1, "a", "x", ok=True), _sr(2, "b", "y", ok=False, error="e")]
    v = Critic(llm_call=llm).evaluate("ціль", res)
    assert v.source == "llm" and v.success is False


def test_llm_exception_falls_back_to_rule():
    def boom(p): raise RuntimeError("llm down")
    res = [_sr(1, "a", "x", ok=True), _sr(2, "b", "y", ok=False, error="e")]
    v = Critic(llm_call=boom).evaluate("ціль", res)
    assert v.source == "rule"


def test_parse_json_static():
    assert Critic._parse_json('{"success": true}') == {"success": True}
    assert Critic._parse_json("нема json") is None
