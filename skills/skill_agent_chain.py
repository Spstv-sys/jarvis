import os
import re
import time
import logging
import config
from core.utils import check_code_safety

logger = logging.getLogger(__name__)

TRIGGERS = [
    "розробка агента",
    "ланцюг агентів",
    "напиши код ланцюжком агентів",
    "агентна розробка",
    "розроби через агентів",
]

# Trim context passed between agents to avoid exceeding LLM token limits.
# At ~4 chars/token 8000 chars ≈ 2000 tokens — fits comfortably in all providers
# while giving agents enough context to process larger generated files.
_MAX_CONTEXT = 8000

# Number of tail chars to preserve when the text exceeds _MAX_CONTEXT.
# Keeping both the beginning (structure/imports) and the end (final functions)
# reduces the chance of an agent receiving incomplete code.
_TAIL_CHARS = 2000


def _call_with_retry(fn, retries: int = 2, delay: float = 1.5):
    """Call *fn()* up to ``retries + 1`` times, sleeping *delay* seconds between attempts.

    Args:
        fn:      A zero-argument callable to invoke.
        retries: Number of additional attempts after the first failure.
        delay:   Seconds to wait between attempts.

    Returns:
        The first truthy return value of *fn*, or the last return value when
        all attempts are exhausted.
    """
    result = None
    for attempt in range(retries + 1):
        result = fn()
        if result:
            return result
        if attempt < retries:
            time.sleep(delay)
    return result


def _trim(text: str, max_chars: int = _MAX_CONTEXT) -> str:
    """Trim *text* to at most *max_chars* characters.

    When truncation is needed the function preserves the first
    ``max_chars - _TAIL_CHARS`` characters (structure/imports) plus the last
    ``_TAIL_CHARS`` characters (final functions/classes), separated by a short
    ellipsis marker.  This reduces the chance of passing incomplete code to the
    next agent.
    """
    if len(text) <= max_chars:
        return text
    head_chars = max_chars - _TAIL_CHARS
    if head_chars <= 0:
        # _TAIL_CHARS >= max_chars: preserve the tail only, but warn in case of
        # misconfiguration so the developer can notice and fix the constants.
        logger.warning(
            "_trim: _TAIL_CHARS (%d) >= max_chars (%d); returning tail only.",
            _TAIL_CHARS, max_chars,
        )
        return text[-max_chars:]
    return text[:head_chars] + "\n# ... (скорочено) ...\n" + text[-_TAIL_CHARS:]


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
    arch_output = _call_with_retry(lambda: jarvis.llm.generate_response(arch_prompt, is_system_call=True))
    if not arch_output:
        return "Помилка на етапі Архітектора."

    logger.debug("Architect output length: %d", len(arch_output))

    # ── Stage 2: Developer ────────────────────────────────────────────────────
    jarvis.audio.speak("Розробник пише код за планом архітектора.")

    dev_context = _trim(arch_output)
    dev_label = f"скорочено до {_MAX_CONTEXT} символів" if len(arch_output) > _MAX_CONTEXT else "повний текст"
    dev_prompt = (
        f"Ти — Senior Python Developer. Задача: '{request}'.\n"
        f"Архітектурний план ({dev_label}):\n"
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
    dev_output = _call_with_retry(lambda: jarvis.llm.generate_response(dev_prompt, is_system_call=True))
    if not dev_output:
        return "Помилка на етапі Розробника."

    logger.debug("Developer output length: %d", len(dev_output))

    # ── Stage 3: Reviewer ─────────────────────────────────────────────────────
    jarvis.audio.speak("Рев'ювер перевіряє код на помилки та безпеку.")

    rev_context = _trim(dev_output)
    rev_label = f"скорочено до {_MAX_CONTEXT} символів" if len(dev_output) > _MAX_CONTEXT else "повний текст"
    rev_prompt = (
        f"Ти — Senior Code Reviewer. Задача: '{request}'.\n"
        f"Перевір наступний код ({rev_label}):\n"
        f"{rev_context}\n\n"
        f"Знайди та виправ:\n"
        f"1. Логічні помилки та помилки часу виконання.\n"
        f"2. Порушення PEP 8 та PEP 484.\n"
        f"3. Потенційні вразливості безпеки.\n"
        f"4. Відсутню обробку виключень.\n"
        f"ОБОВ'ЯЗКОВО поверни відповідь СТРОГО у форматі нижче — жодного тексту поза ним:\n"
        f"FILENAME: назва_файлу.py\n"
        f"CODE:\n"
        f"<тільки чистий Python код, без markdown-огорожі, тобто без символів ```>"
    )
    rev_output = _call_with_retry(lambda: jarvis.llm.generate_response(rev_prompt, is_system_call=True))
    if not rev_output:
        return "Помилка на етапі Рев'юера."

    logger.debug("Reviewer output length: %d", len(rev_output))

    # ── Stage 4: Refactorer ───────────────────────────────────────────────────
    jarvis.audio.speak("Рефакторер оптимізує та фіналізує код.")

    ref_context = _trim(rev_output)
    ref_label = f"скорочено до {_MAX_CONTEXT} символів" if len(rev_output) > _MAX_CONTEXT else "повний текст"
    ref_prompt = (
        f"Ти — Refactoring Expert. Задача: '{request}'.\n"
        f"Рефактор та оптимізуй наступний код ({ref_label}):\n"
        f"{ref_context}\n\n"
        f"Вимоги до рефакторингу:\n"
        f"1. Дотримання SOLID, DRY та KISS.\n"
        f"2. Видалення дублювання та мертвого коду.\n"
        f"3. Покращення читабельності (змінні, функції, структура).\n"
        f"4. Оптимізація продуктивності там, де це доцільно.\n"
        f"ОБОВ'ЯЗКОВО поверни відповідь СТРОГО у форматі нижче — жодного тексту поза ним:\n"
        f"FILENAME: назва_файлу.py\n"
        f"CODE:\n"
        f"<тільки чистий Python код, без markdown-огорожі, тобто без символів ```>"
    )
    ref_output = _call_with_retry(lambda: jarvis.llm.generate_response(ref_prompt, is_system_call=True))
    if not ref_output:
        return "Помилка на етапі Рефакторера."

    logger.debug("Refactorer output length: %d", len(ref_output))

    # ── Parse final output ────────────────────────────────────────────────────
    filename_match = re.search(r"FILENAME:\s*([^/\\\s]+\.py)", ref_output)
    filename = filename_match.group(1).strip() if filename_match else f"agent_chain_{int(time.time())}.py"
    filename = os.path.basename(filename)

    if "CODE:" in ref_output:
        clean_code = ref_output.split("CODE:", 1)[1].strip()
    else:
        # Fallback: the LLM returned text without the required header.
        # Strip any markdown fences and use whatever code-like content remains.
        logger.warning(
            "Refactorer response missing CODE: section — attempting fallback extraction."
        )
        clean_code = ref_output.strip()

    clean_code = clean_code.replace("```python", "").replace("```", "").strip()

    if not clean_code:
        return "Відповідь агентного ланцюжка не містить секцію CODE:."

    # ── Safety check ──────────────────────────────────────────────────────────
    safe, bad_pattern = check_code_safety(clean_code)
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
