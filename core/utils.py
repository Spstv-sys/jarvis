"""
Shared utility functions used across Jarvis modules.
"""
import re


def strip_triggers(text: str, triggers: list) -> str:
    """Remove trigger words/phrases from *text*, longest-first to avoid partial matches.

    Args:
        text:     Raw command string.
        triggers: List of trigger phrases to remove (case-insensitive).

    Returns:
        Cleaned string with all triggers removed and excess whitespace collapsed.
    """
    for trigger in sorted(triggers, key=len, reverse=True):
        text = re.sub(rf'\b{re.escape(trigger)}\b', '', text, flags=re.IGNORECASE)
    return re.sub(r'\s+', ' ', text).strip(" .")


def normalize_filename(text: str) -> str:
    """Convert voice-dictated Ukrainian filename tokens to a real filename string.

    Examples::
        "відкрий тест пай"  →  "відкрий test.py"
        "скрипт точка джес" →  "script.js"
    """
    result = (
        text
        .replace(" крапка ", ".")
        .replace(" точка ", ".")
        .replace(" пай", ".py")
        .replace(" джес", ".js")
        .replace(" текст", ".txt")
    )
    for ukr, eng in {
        "тест":   "test",
        "мейн":   "main",
        "скрипт": "script",
        "індекс": "index",
    }.items():
        result = result.replace(ukr, eng)
    return result
