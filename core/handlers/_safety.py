"""Safety — перевірка згенерованого LLM коду навичок (Problem 7).

Винесено з command_handlers.py. ``_check_skill_safety`` делегує у
:func:`core.utils.check_code_safety` (regex + AST). Ре-експортується з
``command_handlers`` заради зворотної сумісності з тестами.
"""
from __future__ import annotations

from core.utils import check_code_safety

# Forbidden patterns for LLM-generated skill code (Problem 7)
_FORBIDDEN_SKILL_PATTERNS = [
    r'\bos\.system\b',
    r'\bsubprocess\b',
    r'\b__import__\b',
    r'\beval\b',
    r'\bexec\b',
]


def _check_skill_safety(code: str) -> tuple[bool, str]:
    """Return (True, '') if code is safe, (False, matched_pattern) otherwise.

    Delegates to :func:`core.utils.check_code_safety` for dual-layer
    (regex + AST) analysis.
    """
    return check_code_safety(code)
