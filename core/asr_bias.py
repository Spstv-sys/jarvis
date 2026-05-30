"""Будівник bias-підказки для Whisper (initial_prompt / prompt_ids).

Більшість помилок розпізнавання у командному сценарії — словникові:
модель пише «G-min» замість «джеміні», «Skill Timer» замість «скілл таймер»,
ділить «запам'ятай» на «за пам'ятай». Whisper можна підштовхнути до
очікуваного словника через preceding-context prompt: токени з підказки
стають імовірнішими.

``command_prompt()`` збирає КОМПАКТНИЙ словник-мішок із тригерів
(config.TRIGGERS), форм імені асистента і кількох власних назв, які STT
регулярно перекручує. Найцінніші терміни (власні назви + ім'я) стоять
В КІНЦІ — Whisper зважає переважно на хвіст промпта, тож вони точно
потраплять у вікно навіть у разі обрізки.
"""
from __future__ import annotations

import re

import config

# Власні назви / запозичення, які модель часто пише латиницею або дробить.
_PROPER_NOUNS = ["пайтон", "ютуб", "скілл", "таймер", "джеміні", "оллама",
                 "діпсік", "Нео", "Джарвіс"]

_MAX_CHARS = 400          # з запасом уміщується у вікно промпта Whisper (~224 токени)
_STOP = {"в", "на", "з", "із", "що", "як", "це", "є", "до", "та", "і", "й", "у", "про", "мій"}


def _words_from_triggers() -> list[str]:
    out: list[str] = []
    for phrases in getattr(config, "TRIGGERS", {}).values():
        for ph in phrases:
            for w in re.split(r"\s+", ph.strip().lower()):
                w = w.strip(".,:;!?")
                if len(w) >= 3 and w not in _STOP:
                    out.append(w)
    return out


def command_prompt() -> str:
    """Зібрати компактний bias-рядок із доменного словника команд.

    Повертає рядок-мішок слів; порожній рядок, якщо щось пішло не так
    (тоді біасинг просто не вмикається).
    """
    try:
        seen: set[str] = set()
        words: list[str] = []
        # 1) ключові слова тригерів (на початку — менш критичні).
        for w in _words_from_triggers():
            if w not in seen:
                seen.add(w); words.append(w)
        # 2) власні назви + форми імені — В КІНЦІ (виживають при обрізці хвоста).
        forms = getattr(config, "ASSISTANT_NAME_FORMS", None) or [getattr(config, "ASSISTANT_NAME", "Нео")]
        tail = _PROPER_NOUNS + [f.strip() for f in forms]
        for w in tail:
            k = w.lower()
            if k in seen:
                # підняти у хвіст: прибрати з тіла, додати в кінець
                try:
                    words.remove(w if w in words else k)
                except ValueError:
                    pass
            seen.add(k)
            words.append(w)

        prompt = " ".join(words)
        if len(prompt) > _MAX_CHARS:
            prompt = prompt[-_MAX_CHARS:]
            if " " in prompt:
                prompt = prompt[prompt.index(" ") + 1:]
        return prompt
    except Exception:
        return ""
