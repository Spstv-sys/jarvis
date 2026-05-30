"""Fuzzy-зіставлення розпізнаної команди з тригерами (поверх rapidfuzz).

Whisper на коротких командах часто робить дрібні помилки (1-2 символи):
«погода»→«пагода», «тихіше»→«тихише», «яскравість»→«яскравісь». Базовий
роутер матчить тригери підрядком/межею слова, тож такі промахи повністю
зривають маршрутизацію і команда падає в LLM-чат.

Цей модуль дає токен-орієнтовану оцінку схожості, придатну і для
однослівних тригерів (порівнюємо кожне слово команди з тригером), і для
багатослівних (ковзне partial + token_set). Усе адитивно: модуль лише
*додає* збіги там, де раніше не матчилось нічого.
"""
from __future__ import annotations

import re

from rapidfuzz import fuzz

# Залишаємо літери (вкл. кирилицю/апостроф) і пробіли; решту — у пробіл.
_PUNCT_RE = re.compile(r"[^0-9A-Za-zЀ-ӿʼ'’\s]+")
_WS_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Нижній регістр, прибрати пунктуацію, схлопнути пробіли."""
    if not text:
        return ""
    text = _PUNCT_RE.sub(" ", text.lower())
    return _WS_RE.sub(" ", text).strip()


def trigger_score(cmd: str, trigger: str) -> float:
    """Повернути 0..100 — наскільки команда схожа на тригер.

    * Однослівний тригер: максимум ``fuzz.ratio`` між тригером і кожним
      окремим словом команди (ловить дрібні STT-помилки в потрібному слові,
      не реагуючи на довжину всієї фрази).
    * Багатослівний тригер: максимум із ковзного ``partial_ratio`` та
      ``token_set_ratio`` (стійко до зайвих/переставлених слів).
    """
    cmd_n = normalize(cmd)
    trig_n = normalize(trigger)
    if not cmd_n or not trig_n:
        return 0.0

    trig_words = trig_n.split()
    if len(trig_words) == 1:
        toks = cmd_n.split()
        # Кандидати: окремі слова + склеєні сусідні пари. Друге ловить
        # STT-дроблення одного слова на два ("за пам'ятай" -> "запам'ятай").
        cands = list(toks)
        cands += [toks[i] + toks[i + 1] for i in range(len(toks) - 1)]
        return max(
            (fuzz.ratio(c, trig_n) for c in cands),
            default=0.0,
        )

    return max(
        fuzz.partial_ratio(cmd_n, trig_n),
        fuzz.token_set_ratio(cmd_n, trig_n),
    )


def matches(cmd: str, trigger: str, threshold: float) -> bool:
    """``True``, якщо :func:`trigger_score` досягає порога."""
    return trigger_score(cmd, trigger) >= threshold
