"""
Shared utility functions used across Jarvis modules.
"""
import ast
import re


# ---------------------------------------------------------------------------
# Forbidden identifiers used by check_code_safety
# ---------------------------------------------------------------------------
_FORBIDDEN_PATTERNS: list[str] = [
    r'\bos\.system\b',
    r'\bsubprocess\b',
    r'\b__import__\b',
    r'\beval\b',
    r'\bexec\b',
]

_FORBIDDEN_IMPORTS: frozenset[str] = frozenset({
    "subprocess", "ctypes", "importlib", "builtins",
})
_FORBIDDEN_CALLS: frozenset[str] = frozenset({
    "eval", "exec", "__import__", "compile",
})
_FORBIDDEN_ATTR_CALLS: frozenset[str] = frozenset({
    "system", "popen", "run", "call", "check_output", "check_call",
})


def check_code_safety(code: str) -> tuple[bool, str]:
    """Return (True, '') if *code* is safe, (False, reason) otherwise.

    Applies two layers of analysis:

    1. Quick regex scan for common dangerous patterns.
    2. Full AST walk to catch forbidden imports and function calls.

    Args:
        code: Python source code string to validate.

    Returns:
        A ``(is_safe, reason)`` tuple.  *reason* is an empty string when
        *is_safe* is ``True``.
    """
    # Layer 1: regex
    for pattern in _FORBIDDEN_PATTERNS:
        if re.search(pattern, code):
            return False, pattern

    # Layer 2: AST
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return False, f"SyntaxError: {exc}"

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in _FORBIDDEN_IMPORTS:
                    return False, f"forbidden import: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if not module:
                continue
            top = module.split(".")[0]
            if top in _FORBIDDEN_IMPORTS:
                return False, f"forbidden import: {module}"
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in _FORBIDDEN_CALLS:
                return False, f"forbidden call: {node.func.id}"
            if isinstance(node.func, ast.Attribute) and node.func.attr in _FORBIDDEN_ATTR_CALLS:
                return False, f"forbidden attribute call: {node.func.attr}"

    return True, ""


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
