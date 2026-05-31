"""Юніт-тести core.intent_router.IntentRouter: fast-маршрути, кеш, фолбек."""
from core.intent_router import IntentRouter, Intent


def _router(tmp_path=None, **kw):
    triggers = {"weather": ["погода"], "news": ["новини"]}
    cache = str(tmp_path / "intent.sqlite") if tmp_path else None
    return IntentRouter(triggers=triggers, cache_db_path=cache, **kw)


def test_hash_normalizes_whitespace_and_case():
    assert IntentRouter._hash("  Привіт   Світ ") == IntentRouter._hash("привіт світ")


def test_exact_route_match_fast():
    r = _router()
    intent = r.classify("яка сьогодні погода")
    assert intent.name == "weather" and intent.source == "fast" and intent.confidence == 1.0


def test_substring_match_confidence():
    r = IntentRouter(triggers={"weather": ["погод"]})
    intent = r.classify("погодник")  # \bпогод\b не матчить, але підрядок так
    assert intent.name == "weather" and abs(intent.confidence - 0.9) < 1e-6


def test_dynamic_skill_priority():
    r = IntentRouter(triggers={"weather": ["погода"]},
                     dynamic_skills=[{"triggers": ["склади план"], "name": "plan"}])
    intent = r.classify("склади план на день")
    assert intent.name == "skill:plan" and intent.source == "fast"


def test_fallback_to_chat():
    r = _router()
    intent = r.classify("розкажи цікавий факт")
    assert intent.name == "chat" and intent.source == "fallback"


def test_empty_input_is_chat():
    r = _router()
    assert r.classify("   ").name == "chat"


def test_cache_roundtrip_and_clear(tmp_path):
    r = _router(tmp_path)
    r._cache_put("абракадабра приклад", Intent(name="weather", confidence=0.8, source="llm"))
    hit = r.classify("абракадабра приклад")   # не матчить тригер → з кешу
    assert hit.name == "weather" and hit.source == "cache"
    assert r.clear_cache() >= 1
    after = r.classify("абракадабра приклад")
    assert after.source == "fallback"  # кеш порожній


def test_stats_returns_dict():
    assert isinstance(_router().stats(), dict)
