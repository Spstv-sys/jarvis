import os
import re
import time
import config

TRIGGERS = [
    "напиши код",
    "напиши скрипт",
    "запрограмуй",
    "створи скрипт",
    "створи код",
    "зроби скрипт",
    "зроби програму",
    "напиши програму",
    "генеруй код",
    "напиши функцію",
    "створи функцію",
]

# Patterns blocked in LLM-generated code (mirrors _FORBIDDEN_SKILL_PATTERNS)
_FORBIDDEN_PATTERNS = [
    r'\bos\.system\b',
    r'\b__import__\b',
    r'\beval\b',
    r'\bexec\b',
]


def _is_safe_code(code: str) -> tuple[bool, str]:
    """Return (True, '') if code is safe, (False, pattern) otherwise."""
    for pattern in _FORBIDDEN_PATTERNS:
        if re.search(pattern, code):
            return False, pattern
    return True, ""


def execute(cmd, jarvis):
    request = cmd
    for t in TRIGGERS:
        request = request.replace(t, "")
    request = request.strip()

    if not request:
        return "Що саме мені потрібно запрограмувати?"

    jarvis.audio.speak("Аналізую задачу та формую структуру файлу.")

    prompt = (
        f"Ти Senior Python Engineer з 10+ роками досвіду. Задача: '{request}'.\n\n"
        f"Поверни відповідь СТРОГО у форматі:\n"
        f"FILENAME: назва_файлу.py\n"
        f"CODE:\n"
        f"<код тут>\n\n"
        f"ВИМОГИ ДО КОДУ (рівень Senior):\n"
        f"- Type annotations на всіх функціях та нетривіальних змінних (PEP 484).\n"
        f"- Google-style docstrings для всіх публічних функцій.\n"
        f"- Конкретні типи виключень у except (НЕ bare `except Exception`).\n"
        f"- Логування через `import logging; logger = logging.getLogger(__name__)` замість print.\n"
        f"- Валідація вхідних аргументів.\n"
        f"- Назва файлу: коротка, латиниця, через підкреслення.\n"
        f"- ТІЛЬКИ чистий Python код, без markdown, без пояснень поза кодом."
    )

    response = jarvis.llm.generate_response(prompt, is_system_call=True)
    if not response:
        return "Помилка зв'язку з ШІ."

    try:
        filename_match = re.search(r"FILENAME:\s*([^/\\\s]+\.py)", response)
        filename = filename_match.group(1).strip() if filename_match else f"script_{int(time.time())}.py"
        # Prevent path traversal in LLM-supplied filename
        filename = os.path.basename(filename)

        code_part = response.split("CODE:")[1].strip()
        clean_code = code_part.replace("```python", "").replace("```", "").strip()
    except Exception:
        return "Не вдалося розібрати структуру коду від ШІ."

    safe, bad_pattern = _is_safe_code(clean_code)
    if not safe:
        return f"Згенерований код містить небезпечний виклик ({bad_pattern}) та не збережено."

    target_dir = os.path.realpath(config.SORTING_DRIVE)
    os.makedirs(target_dir, exist_ok=True)
    filepath = os.path.join(target_dir, filename)
    # Ensure the resolved path stays within target_dir
    if not os.path.realpath(filepath).startswith(target_dir + os.sep):
        return "Назва файлу від ШІ є некоректною — збереження скасовано."

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(clean_code)

    return f"Скрипт '{filename}' успішно створено та збережено у {target_dir}."