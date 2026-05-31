"""Голосовий доступ до епізодичної пам'яті.

Тригери:
  • "що я робив" / "що ти робив" — нещодавні події (за день/тиждень)
  • "коли востаннє" / "пригадай як" — семантичний пошук
  • "забудь старе" — очистка > N днів
  • "статистика памʼяті" / "статистика епізодів"

Часові вирази (швидка евристика):
  • сьогодні, вчора, тиждень, місяць, рік
"""
from __future__ import annotations

import re
import time
import logging
from datetime import datetime

from core.episodic_memory import get_episodic_memory

logger = logging.getLogger(__name__)

TRIGGERS = [
    "що я робив",
    "що ти робив",
    "що ми робили",
    "коли востаннє",
    "коли я востаннє",
    "пригадай як",
    "пригадай коли",
    "знайди в памʼяті",
    "знайди в памяті",
    "статистика памʼяті",
    "статистика памяті",
    "статистика епізодів",
    "забудь старе",
]

# ----------------------------------------------------------
_TIME_WINDOWS = [
    (r"\bсьогодні\b",       1),
    (r"\bвчора\b",          2),
    (r"\bпозавчора\b",      3),
    (r"\bтиждень\b|\bтижня\b", 7),
    (r"\bмісяц(ь|я|і)\b",   30),
    (r"\bрік\b|\bроку\b",   365),
]

def _detect_window_days(text: str) -> int:
    t = text.lower()
    for pat, days in _TIME_WINDOWS:
        if re.search(pat, t):
            return days
    return 0  # без обмеження


def _strip_triggers(cmd: str) -> str:
    text = cmd
    for t in sorted(TRIGGERS, key=len, reverse=True):
        idx = text.lower().find(t.lower())
        if idx >= 0:
            text = (text[:idx] + text[idx + len(t):]).strip(" :,.\u2014-?")
    return text.strip()


def _format_event(e) -> str:
    snippet = (e.text or "").replace("\n", " ")[:140]
    score = f" ({e.score:.2f})" if getattr(e, "score", 0) else ""
    return f"  • {e.when} [{e.kind}] {snippet}{score}"


# ----------------------------------------------------------
def execute(cmd, jarvis):
    em = get_episodic_memory()

    # Прикріпимо embedder з memory_rag, якщо він уже прогрітий.
    brain = getattr(jarvis, "brain", None)
    if brain is not None and getattr(brain, "embeddings", None) is not None:
        em.attach_embedder(brain.embeddings)

    cmd_l = cmd.lower()

    # --- статистика
    if "статистик" in cmd_l:
        s = em.stats()
        return (
            f"Епізодична пам'ять:\n"
            f"  всього подій: {s['total']}\n"
            f"  з ембедингами: {s['with_embedding']}\n"
            f"  embedder: {'так' if s['embedder'] else 'ні'}\n"
            f"  типи: {s['by_kind']}\n"
            f"  файл: {s['db']}"
        )

    # --- забудь старе
    if "забудь старе" in cmd_l:
        m = re.search(r"(\d+)\s*дн", cmd_l)
        days = int(m.group(1)) if m else 90
        n = em.delete_older_than(days)
        return f"Видалено {n} подій старших за {days} дн."

    # --- recent ("що я робив сьогодні/вчора/тижня")
    if any(t in cmd_l for t in ("що я робив", "що ти робив", "що ми робили")):
        days = _detect_window_days(cmd) or 1
        since = int(time.time()) - days * 86400
        events = em.recent(n=15, since=since)
        if not events:
            return f"За останні {days} дн. подій не знайшов."
        head = f"Події за {days} дн ({len(events)}):"
        return head + "\n" + "\n".join(_format_event(e) for e in events)

    # --- семантичний пошук ("коли востаннє ...", "пригадай як ...")
    query = _strip_triggers(cmd)
    if not query:
        return "Що саме шукати в памʼяті? Скажи, наприклад: 'пригадай як я налаштовував Whisper'."
    days = _detect_window_days(cmd)
    since = int(time.time()) - days * 86400 if days else None
    events = em.search(query, top_k=5, since=since)
    if not events:
        return f"У пам'яті немає подій по запиту: {query!r}"
    return f"Знайдено {len(events)} подій по {query!r}:\n" + "\n".join(_format_event(e) for e in events)
