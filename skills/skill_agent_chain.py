import os
import re
import time
import logging
import config

logger = logging.getLogger(__name__)

TRIGGERS = [
    "розробка агента",
    "ланцюг агентів",
    "напиши код панкожком агентів",
    "напиши код ланцюжком агентів",
    "агентна розробка",
    "розроби через агентів",
]

_FORBIDDEN_PATTERNS = [
    r'\bos\.system\b',
    r'\b__import__\b',
    r'\beval\b',
    r'\bexec\b',
]

# Trim context passed between agents to avoid exceeding LLM token limits.
# At ~4 chars/token this maps to roughly 750 tokens — well within all providers.
_MAX_CONTEXT = 3000


def _is_safe_code(code: str) -> tuple[bool, str]:
    """Return (True, '') if code is safe, (False, pattern) otherwise."""
    for pattern in _FORBIDDEN_PATTERNS:
        if re.search(pattern, code):
            return False, pattern
    return True, ""


def _trim(text: str, max_chars: int = _MAX_CONTEXT) -> str:
    """Trim text to at most max_chars characters, keeping the tail (most recent output)."""
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def execute(cmd: str, jarvis) -> str:
    """Run a 4-stage agent chain (Architect → Developer → Reviewer → Refactorer).

    Args:
        cmd: Raw user command string.
        jarvis: JarvisUltimate instance providing .llm and .audio.

    Returns:
        Human-readable result string.
    """
    request = cmd
    for t in TRIGGERS:
        request = request.replace(t, "")
    request = request.strip()

    if not request:
        return "Що саме розробити за допомогою ланцюжка агентів?"

    # ── Stage 1: Architect ────────────────────────────────────────────────────
    jarvis.audio.speak("Архітектор аналізує задачу та проєктує структуру рішення.")

    arch_prompt = (
        f"Ти — Software Architect. Задача: '{request}'.\n"
        f"Спроєктуй чітку архітектуру рішення:\n"
        f"- Список модулів / класів / функцій з коротким описом.\n"
        f"- Вхідні та вихідні дані кожного компонента.\n"
        f"- Ключові технічні рішення та їхні обґрунтування.\n"
        f"Відповідай виключно технічним текстом, без коду."
    )
    arch_output = jarvis.llm.generate_response(arch_prompt, is_system_call=True)
    if not arch_output:
        return "Помилка на етапі Архітектора."

    logger.debug("Architect output length: %d", len(arch_output))

    # ── Stage 2: Developer ────────────────────────────────────────────────────
    jarvis.audio.speak("Розробник пише код за планом архітектора.")

    dev_context = _trim(arch_output)
    dev_prompt = (
        f"Ти — Senior Python Developer. Задача: '{request}'.\n"
        f"Архітектурний план (скорочено до {_MAX_CONTEXT} символів):\n"
        f"{dev_context}\n\n"
        f"Напиши повну реалізацію на Python. Вимоги:\n"
        f"- Type annotations (PEP 484) на всіх функціях.\n"
        f"- Google-style docstrings для публічних функцій.\n"
        f"- Конкретні типи виключень у except (не bare except).\n"
        f"- Логування через logging, не print.\n"
        f"- Поверни відповідь СТРОГО у форматі:\n"
        f"FILENAME: назва_файлу.py\n"
        f"CODE:\n"
        f"<тільки чистий Python код без markdown-огорожі>"
    )
    dev_output = jarvis.llm.generate_response(dev_prompt, is_system_call=True)
    if not dev_output:
        return "Помилка на етапі Розробника."

    logger.debug("Developer output length: %d", len(dev_output))

    # ── Stage 3: Reviewer ─────────────────────────────────────────────────────
    jarvis.audio.speak("Рев'ювер перевіряє код на помилки та безпеку.")

    rev_context = _trim(dev_output)
    rev_prompt = (
        f"Ти — Senior Code Reviewer. Задача: '{request}'.\n"
        f"Перевір наступний код (скорочено до {_MAX_CONTEXT} символів):\n"
        f"{rev_context}\n\n"
        f"Знайди та виправ:\n"
        f"1. Логічні помилки та помилки часу виконання.\n"
        f"2. Порушення PEP 8 та PEP 484.\n"
        f"3. Потенційні вразливості безпеки.\n"
        f"4. Відсутню обробку виключень.\n"
        f"Поверни ТІЛЬКИ виправлений код у форматі:\n"
        f"FILENAME: назва_файлу.py\n"
        f"CODE:\n"
        f"<тільки чистий Python код без markdown-огорожі>"
    )
    rev_output = jarvis.llm.generate_response(rev_prompt, is_system_call=True)
    if not rev_output:
        return "Помилка на етапі Рев'юера."

    logger.debug("Reviewer output length: %d", len(rev_output))

    # ── Stage 4: Refactorer ───────────────────────────────────────────────────
    jarvis.audio.speak("Рефакторер оптимізує та фіналізує код.")

    ref_context = _trim(rev_output)
    ref_prompt = (
        f"Ти — Refactoring Expert. Задача: '{request}'.\n"
        f"Рефактор та оптимізуй наступний код (скорочено до {_MAX_CONTEXT} символів):\n"
        f"{ref_context}\n\n"
        f"Вимоги до рефакторингу:\n"
        f"1. Дотримання SOLID, DRY та KISS.\n"
        f"2. Видалення дублювання та мертвого коду.\n"
        f"3. Покращення читабельності (змінні, функції, структура).\n"
        f"4. Оптимізація продуктивності там, де це доцільно.\n"
        f"Поверни ТІЛЬКИ фінальний код у форматі:\n"
        f"FILENAME: назва_файлу.py\n"
        f"CODE:\n"
        f"<тільки чистий Python код без markdown-огорожі>"
    )
    ref_output = jarvis.llm.generate_response(ref_prompt, is_system_call=True)
    if not ref_output:
        return "Помилка на етапі Рефакторера."

    logger.debug("Refactorer output length: %d", len(ref_output))

    # ── Parse final output ────────────────────────────────────────────────────
    try:
        filename_match = re.search(r"FILENAME:\s*([^/\\\s]+\.py)", ref_output)
        filename = filename_match.group(1).strip() if filename_match else f"agent_chain_{int(time.time())}.py"
        filename = os.path.basename(filename)
    except AttributeError:
        return "Не вдалося визначити ім'я файлу з відповіді агентного ланцюжка."

    parts = ref_output.split("CODE:")
    if len(parts) < 2:
        return "Відповідь агентного ланцюжка не містить секцію CODE:."
    clean_code = parts[1].strip().replace("```python", "").replace("```", "").strip()

    # ── Safety check ──────────────────────────────────────────────────────────
    safe, bad_pattern = _is_safe_code(clean_code)
    if not safe:
        return f"Згенерований код містить небезпечний виклик ({bad_pattern}) та не збережено."

    # ── Save to SORTING_DRIVE ─────────────────────────────────────────────────
    target_dir = os.path.realpath(config.SORTING_DRIVE)
    os.makedirs(target_dir, exist_ok=True)
    filepath = os.path.join(target_dir, filename)
    if not os.path.realpath(filepath).startswith(target_dir + os.sep):
        return "Назва файлу від агентів є некоректною — збереження скасовано."

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(clean_code)

    jarvis.audio.speak(f"Готово! Ланцюжок агентів завершив роботу. Файл {filename} збережено.")
    return f"Агентний ланцюжок завершено. Скрипт '{filename}' збережено у {target_dir}."
