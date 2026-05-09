import ast
import os
import re
import shutil
import subprocess
import sys
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

# Triggers that activate AMEND mode (edit an existing file).
# The command after trigger stripping must contain the target filename and a
# description of what to change, e.g.:
#   "виправ агентами calc.py  додай обробку ділення на нуль"
AMEND_TRIGGERS = [
    "виправ агентами",
    "доопрацюй агентами",
    "покращ агентами",
    "відредагуй агентами",
    "агентна корекція",
    "агентна правка",
]

# Trim context passed between agents to avoid exceeding LLM token limits.
# At ~4 chars/token 24000 chars ≈ 6000 tokens — fits comfortably in modern
# providers (8k+ context) and is large enough that typical generated files
# (under ~600 LOC) survive intact between Reviewer and Refactorer stages
# without losing their middle section.
_MAX_CONTEXT = 24000

# Number of tail chars to preserve when the text exceeds _MAX_CONTEXT.
# Keeping both the beginning (structure/imports) and the end (final functions)
# reduces the chance of an agent receiving incomplete code.
_TAIL_CHARS = 6000

# Maximum Developer↔Reviewer self-correction iterations. The loop exits early
# when the Reviewer signals "STATUS: APPROVED" — typical happy path is 1-2
# iterations. The cap prevents pathological loops on intractable bugs.
_MAX_REVIEW_ITERATIONS = 3

# Maximum syntax-fix retries asked from the Refactorer when ast.parse() fails
# on its output. Two attempts suffice for almost all transient mistakes
# (missing colon, unmatched bracket) without burning excessive tokens.
_MAX_SYNTAX_RETRIES = 2


def _parse_filename_and_code(response: str, default_prefix: str = "agent_chain") -> tuple[str, str]:
    """Extract ``(filename, code)`` from a stage response.

    Honours the ``FILENAME:``/``CODE:`` contract; falls back to a fenced
    code block if the contract is broken; returns ``("", "")`` if no code can
    be recovered. ``filename`` is always passed through :func:`os.path.basename`
    to neutralise path-traversal attempts.
    """
    filename_match = re.search(r"FILENAME:\s*([^/\\\s]+\.py)", response)
    filename = filename_match.group(1).strip() if filename_match else f"{default_prefix}_{int(time.time())}.py"
    filename = os.path.basename(filename)

    if "CODE:" in response:
        code = response.split("CODE:", 1)[1].strip()
    else:
        logger.warning("Response missing CODE: section — attempting fenced-block extraction.")
        fenced = re.search(r"```(?:python)?\s*\n(.*?)```", response, re.DOTALL)
        code = fenced.group(1).strip() if fenced else ""

    code = code.replace("```python", "").replace("```", "").strip()
    return filename, code


def _validate_syntax(code: str) -> tuple[bool, str]:
    """Return ``(True, "")`` if *code* parses as Python, ``(False, error)`` otherwise."""
    try:
        ast.parse(code)
        return True, ""
    except SyntaxError as exc:
        return False, f"line {exc.lineno}: {exc.msg}"


def _autoformat(code: str) -> str:
    """Best-effort apply ``ruff --fix`` then ``black`` to *code*.

    Both tools are optional. Failures are logged at DEBUG and the original
    code is returned — formatting is a quality-of-life improvement, not a
    correctness requirement.
    """
    formatted = code

    # ruff --fix (lint autofixes: imports order, unused vars, simple style)
    if shutil.which("ruff"):
        try:
            proc = subprocess.run(
                ["ruff", "check", "--fix", "--exit-zero", "--stdin-filename", "snippet.py", "-"],
                input=formatted, capture_output=True, text=True, timeout=15,
                encoding="utf-8", errors="replace",
            )
            # ruff prints diagnostics to stderr; fixed code goes to stdout when
            # --fix is combined with stdin. If ruff lacks stdin output support
            # in the installed version, stdout will be empty — keep original.
            if proc.returncode in (0, 1) and proc.stdout.strip():
                formatted = proc.stdout
        except (subprocess.TimeoutExpired, OSError) as exc:
            logger.debug("ruff autoformat skipped: %s", exc)

    # black (formatting)
    if shutil.which("black"):
        try:
            proc = subprocess.run(
                ["black", "--quiet", "-"],
                input=formatted, capture_output=True, text=True, timeout=15,
                encoding="utf-8", errors="replace",
            )
            if proc.returncode == 0 and proc.stdout:
                formatted = proc.stdout
        except (subprocess.TimeoutExpired, OSError) as exc:
            logger.debug("black autoformat skipped: %s", exc)

    return formatted


def _ruff_diagnostics(code: str) -> str:
    """Run ``ruff check`` (no fix) on *code* and return concise diagnostics.

    Returns an empty string if ruff is unavailable, the run fails, or there
    are no findings. The output is intended to be embedded into the Reviewer
    prompt so the LLM critiques real issues instead of hallucinated ones.
    """
    if not shutil.which("ruff"):
        return ""
    try:
        proc = subprocess.run(
            [
                "ruff", "check",
                "--no-fix",
                "--output-format=concise",
                "--exit-zero",
                "--stdin-filename", "snippet.py",
                "-",
            ],
            input=code, capture_output=True, text=True, timeout=15,
            encoding="utf-8", errors="replace",
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.debug("ruff diagnostics skipped: %s", exc)
        return ""
    out = (proc.stdout or "").strip()
    if not out:
        return ""
    # Cap to ~40 lines to avoid blowing up the prompt on noisy code.
    lines = out.splitlines()
    if len(lines) > 40:
        lines = lines[:40] + [f"... ({len(out.splitlines()) - 40} more)"]
    return "\n".join(lines)


def _unique_filepath(target_dir: str, filename: str) -> str:
    """Return a path inside *target_dir* that does not yet exist.

    If ``target_dir/filename`` already exists, append ``_2``, ``_3``, ... before
    the extension until a free slot is found. Prevents silently overwriting
    previously generated artifacts when the LLM picks a recurring filename.
    """
    candidate = os.path.join(target_dir, filename)
    if not os.path.exists(candidate):
        return candidate
    stem, ext = os.path.splitext(filename)
    for i in range(2, 1000):
        candidate = os.path.join(target_dir, f"{stem}_{i}{ext}")
        if not os.path.exists(candidate):
            return candidate
    # Extremely unlikely; fall back to timestamp suffix.
    return os.path.join(target_dir, f"{stem}_{int(time.time())}{ext}")


def _atomic_write(filepath: str, content: str) -> None:
    """Write *content* to *filepath* atomically (temp file + os.replace).

    Eliminates the half-written-file failure mode where a crash mid-`write`
    would leave a corrupt artifact. The temp file is created in the same
    directory so ``os.replace`` stays on a single filesystem.
    """
    target_dir = os.path.dirname(filepath) or "."
    tmp_path = f"{filepath}.tmp.{os.getpid()}.{int(time.time() * 1000)}"
    try:
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                # fsync unsupported on some FSes (e.g. some Windows shares); ignore.
                pass
        os.replace(tmp_path, filepath)
    except Exception:
        # Clean up temp file on any failure so we don't litter the directory.
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise
    # silence unused-name warning
    _ = target_dir


def _backup_file(filepath: str) -> str:
    """Create a timestamped backup of *filepath* next to the original.

    Returns the backup path so callers can report it to the user.
    Raises :exc:`OSError` if the source does not exist.
    """
    ts = time.strftime("%Y%m%d_%H%M%S")
    stem, ext = os.path.splitext(filepath)
    backup_path = f"{stem}.bak_{ts}{ext}"
    import shutil as _shutil
    _shutil.copy2(filepath, backup_path)
    return backup_path


def _find_project_file(name_hint: str, search_dir: str) -> str | None:
    """Locate a Python file inside *search_dir* whose name matches *name_hint*.

    Matching priority:
    1. Exact basename match (``calc.py`` → ``<dir>/calc.py``).
    2. Case-insensitive exact match.
    3. First file whose stem is a substring of *name_hint* or vice-versa.

    Returns the full path on success, ``None`` if nothing found.
    The search is intentionally limited to the top level of *search_dir* — the
    agent chain only generates files there, so deeper traversal is unnecessary.
    """
    if not os.path.isdir(search_dir):
        return None
    hint = os.path.basename(name_hint).strip()
    if not hint.endswith(".py"):
        hint += ".py"
    # 1. exact
    candidate = os.path.join(search_dir, hint)
    if os.path.isfile(candidate):
        return candidate
    # 2. case-insensitive
    hint_lower = hint.lower()
    for entry in os.scandir(search_dir):
        if entry.is_file() and entry.name.lower() == hint_lower:
            return entry.path
    # 3. substring
    hint_stem = os.path.splitext(hint_lower)[0]
    for entry in os.scandir(search_dir):
        if not entry.is_file() or not entry.name.endswith(".py"):
            continue
        stem = os.path.splitext(entry.name.lower())[0]
        if hint_stem in stem or stem in hint_stem:
            return entry.path
    return None


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
    logger.warning(
        "_trim: input length %d exceeds max_chars %d — middle section will be dropped.",
        len(text), max_chars,
    )
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


# ---------------------------------------------------------------------------
# Domain classification + playbooks.
# ---------------------------------------------------------------------------
# Хардкодних словників більше немає — все читається з /playbooks/*.yaml через
# core.playbook_loader. Це дозволяє редагувати чек-листи та архітектурні
# підказки без правок коду (hot-reload по mtime).
from core.playbook_loader import classify_domain as _classify_domain  # noqa: E402
from core.playbook_loader import get_playbook as _get_playbook  # noqa: E402


def _domain_checklist(domain: str) -> str:
    return _get_playbook(domain).reviewer_checklist or _get_playbook("generic").reviewer_checklist


def _domain_architect_hints(domain: str) -> str:
    return _get_playbook(domain).architect_hints


def _recall_memory(request: str) -> str:
    """Підтягує уроки рефлектора + схожі епізоди. Graceful degradation якщо модулі не доступні."""
    parts: list[str] = []
    try:
        from core.reflector import get_reflector
        lessons = get_reflector().recall(request, top_k=5) or []
        for l in lessons:
            scope = (getattr(l, "scope", None) or "").strip()
            text = (getattr(l, "text", None) or "").strip()
            conf = float(getattr(l, "confidence", 0.0) or 0.0)
            if text:
                parts.append(f"  • [урок/{scope}, conf={conf:.2f}] {text[:200]}")
    except Exception as exc:
        logger.debug("_recall_memory: reflector unavailable: %s", exc)
    try:
        from core.episodic_memory import get_episodic_memory
        episodes = get_episodic_memory().search(
            f"код {request}", top_k=3, kinds=["command", "skill_run"]
        ) or []
        for ev in episodes:
            text = (getattr(ev, "text", None) or "").strip()
            if text:
                parts.append(f"  • [епізод] {text[:200]}")
    except Exception as exc:
        logger.debug("_recall_memory: episodic unavailable: %s", exc)
    if not parts:
        return ""
    return (
        "\nКОНТЕКСТ З ДОСВІДУ (врахуй ці уроки при проєктуванні):\n"
        + "\n".join(parts[:8])
        + "\n"
    )


def execute(cmd: str, jarvis) -> str:
    """Run a 4-stage agent chain (Architect → Developer → Reviewer → Refactorer).

    Supports two modes:

    * **CREATE** (default) – generate a new Python file from scratch.
      Triggered by any phrase in :data:`TRIGGERS`.

    * **AMEND** – load an existing file generated by a prior run and apply
      targeted changes described by the user.
      Triggered by any phrase in :data:`AMEND_TRIGGERS`.  The remaining text
      must start with the target filename, e.g.::

          виправ агентами calc.py  додай логування

    Args:
        cmd: Raw user command string.
        jarvis: JarvisUltimate instance providing .llm and .audio.

    Returns:
        Human-readable result string.
    """
    # ── Detect mode & strip trigger ───────────────────────────────────────────
    amend_mode = False
    text = cmd

    for t in AMEND_TRIGGERS:
        if t in text:
            amend_mode = True
            text = text.replace(t, "")
            break

    if not amend_mode:
        for t in TRIGGERS:
            text = text.replace(t, "")

    text = text.strip()

    if not text:
        if amend_mode:
            return (
                "Вкажи назву файлу та що змінити, наприклад: "
                "'виправ агентами calc.py  додай обробку помилок'"
            )
        return "Що саме розробити за допомогою ланцюжка агентів?"

    # ── AMEND: resolve filename + load existing code ──────────────────────────
    existing_code: str = ""
    target_filepath: str = ""
    amend_instruction: str = text

    if amend_mode:
        target_dir = os.path.realpath(config.SORTING_DRIVE)
        # First token is treated as the filename; the rest is the instruction.
        parts = text.split(None, 1)
        file_hint = parts[0]
        amend_instruction = parts[1].strip() if len(parts) > 1 else ""

        if not amend_instruction:
            return (
                f"Вкажи, що саме потрібно змінити у файлі '{file_hint}', наприклад: "
                f"'виправ агентами {file_hint}  додай логування'"
            )

        found = _find_project_file(file_hint, target_dir)
        if not found:
            return (
                f"Файл '{file_hint}' не знайдено у {target_dir}. "
                f"Перевір назву або спочатку створи проект ланцюжком агентів."
            )

        target_filepath = found
        try:
            with open(target_filepath, "r", encoding="utf-8") as fh:
                existing_code = fh.read()
        except OSError as exc:
            return f"Не вдалося прочитати файл '{target_filepath}': {exc}"

        jarvis.audio.speak(
            f"Починаю корекцію файлу {os.path.basename(target_filepath)} "
            f"за допомогою ланцюжка агентів."
        )
        logger.info(
            "AMEND mode: file=%s, instruction=%r, size=%d chars",
            target_filepath, amend_instruction, len(existing_code),
        )

    request = amend_instruction if amend_mode else text

    # ── Domain + memory grounding (shared by Architect & Reviewer) ───────────
    domain = _classify_domain(request)
    architect_hints = _domain_architect_hints(domain)
    domain_checklist = _domain_checklist(domain)
    memory_block = _recall_memory(request)
    logger.info(
        "agent_chain: domain=%s, hints=%dch, checklist=%dch, memory=%dch",
        domain, len(architect_hints), len(domain_checklist), len(memory_block),
    )

    # Cooperative cancellation helper.
    cancel_event = getattr(jarvis, "cancel_event", None)

    def _cancelled() -> bool:
        return bool(cancel_event and cancel_event.is_set())

    # ── Stage 1: Architect ────────────────────────────────────────────────────
    jarvis.audio.speak("Архітектор аналізує задачу та проєктує структуру рішення.")

    hints_block = (
        f"\nАРХІТЕКТУРНІ ОРІЄНТИРИ ДЛЯ ДОМЕНУ '{domain}':\n{architect_hints}\n"
        if architect_hints else ""
    )

    if amend_mode:
        existing_ctx = _trim(existing_code)
        existing_label = (
            f"скорочено до {_MAX_CONTEXT} символів"
            if len(existing_code) > _MAX_CONTEXT else "повний текст"
        )
        arch_prompt = (
            f"Ти — Software Architect. Домен: {domain}. Задача корекції: '{request}'.\n"
            f"{memory_block}"
            f"{hints_block}"
            f"Існуючий код ({existing_label}):\n"
            f"{existing_ctx}\n\n"
            f"Підготуй точний план змін:\n"
            f"- Які функції / класи / рядки потрібно додати, змінити або видалити.\n"
            f"- Які залежності або імпорти зміняться.\n"
            f"- Можливі ризики регресії та як їх уникнути.\n"
            f"Відповідай виключно технічним текстом, без коду."
        )
    else:
        arch_prompt = (
            f"Ти — Software Architect. Домен: {domain}. Задача: '{request}'.\n"
            f"{memory_block}"
            f"{hints_block}"
            f"Спроєктуй чітку архітектуру рішення:\n"
            f"- Список модулів / класів / функцій з коротким описом.\n"
            f"- Вхідні та вихідні дані кожного компонента.\n"
            f"- Ключові технічні рішення та їхні обґрунтування.\n"
            f"- Специфічні ризики для домену '{domain}' та як їх мітигувати.\n"
            f"Відповідай виключно технічним текстом, без коду."
        )
    arch_output = _call_with_retry(lambda: jarvis.llm.generate_response(arch_prompt, is_system_call=True))
    if not arch_output:
        return "Помилка на етапі Архітектора."

    logger.debug("Architect output length: %d", len(arch_output))

    if _cancelled():
        return "Ланцюжок агентів зупинено за запитом користувача."

    # ── Stage 2-3: Developer ↔ Reviewer self-correction loop ─────────────────
    # The Reviewer either APPROVES or returns concrete REQUIRED_CHANGES that
    # the Developer addresses on the next iteration. Loop exits early when
    # APPROVED or after _MAX_REVIEW_ITERATIONS (whichever first).
    dev_output = None
    review_feedback = ""
    approved = False

    for iteration in range(1, _MAX_REVIEW_ITERATIONS + 1):
        # ---- Developer turn ----
        if iteration == 1:
            if amend_mode:
                jarvis.audio.speak("Розробник застосовує зміни до існуючого коду.")
                existing_ctx = _trim(existing_code)
                existing_label = (
                    f"скорочено до {_MAX_CONTEXT} символів"
                    if len(existing_code) > _MAX_CONTEXT else "повний текст"
                )
                arch_ctx = _trim(arch_output)
                dev_prompt = (
                    f"Ти — Senior Python Developer. Задача корекції: '{request}'.\n"
                    f"Існуючий код файлу ({existing_label}):\n"
                    f"{existing_ctx}\n\n"
                    f"План змін від архітектора:\n"
                    f"{arch_ctx}\n\n"
                    f"Поверни ПОВНУ оновлену версію файлу з усіма змінами. Вимоги:\n"
                    f"- Зберігай весь незмінений код без скорочень.\n"
                    f"- Type annotations (PEP 484) на всіх функціях.\n"
                    f"- Google-style docstrings для публічних функцій.\n"
                    f"- Конкретні типи виключень у except (не bare except).\n"
                    f"- Логування через logging, не print.\n"
                    f"- Поверни відповідь СТРОГО у форматі:\n"
                    f"FILENAME: {os.path.basename(target_filepath)}\n"
                    f"CODE:\n"
                    f"<тільки чистий Python код без markdown-огорожі>"
                )
            else:
                jarvis.audio.speak("Розробник пише код за планом архітектора.")
                dev_context = _trim(arch_output)
                dev_label = (
                    f"скорочено до {_MAX_CONTEXT} символів"
                    if len(arch_output) > _MAX_CONTEXT else "повний текст"
                )
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
        else:
            jarvis.audio.speak(f"Розробник вносить виправлення, ітерація {iteration}.")
            dev_context = _trim(dev_output or "")
            dev_prompt = (
                f"Ти — Senior Python Developer. Задача: '{request}'.\n"
                f"Попередня версія коду:\n{dev_context}\n\n"
                f"Зауваження рев'ювера, які ОБОВ'ЯЗКОВО виправити:\n"
                f"{_trim(review_feedback)}\n\n"
                f"Поверни виправлену повну версію СТРОГО у форматі:\n"
                f"FILENAME: назва_файлу.py\n"
                f"CODE:\n"
                f"<тільки чистий Python код без markdown-огорожі>"
            )

        dev_output = _call_with_retry(
            lambda: jarvis.llm.generate_response(dev_prompt, is_system_call=True)
        )
        if not dev_output:
            return "Помилка на етапі Розробника."

        logger.debug("Developer iteration %d output length: %d", iteration, len(dev_output))

        if _cancelled():
            return "Ланцюжок агентів зупинено за запитом користувача."

        # ---- Reviewer turn ----
        jarvis.audio.speak("Рев'ювер перевіряє код на помилки та безпеку.")
        rev_context = _trim(dev_output)
        rev_label = (
            f"скорочено до {_MAX_CONTEXT} символів"
            if len(dev_output) > _MAX_CONTEXT else "повний текст"
        )
        # Pre-enrich the Reviewer prompt with REAL static-analysis findings.
        # When ruff is installed we extract the actual code (not the FILENAME:
        # header) and pass its diagnostics — this anchors the LLM in concrete
        # issues instead of speculative ones, dramatically improving review
        # quality. When ruff is absent the section is omitted gracefully.
        _, dev_clean_code = _parse_filename_and_code(dev_output)
        ruff_section = ""
        if dev_clean_code:
            diagnostics = _ruff_diagnostics(dev_clean_code)
            if diagnostics:
                ruff_section = (
                    "\nРеальні зауваження статичного аналізатора (ruff) — "
                    "враховуй їх першочергово:\n"
                    f"{diagnostics}\n"
                )
        rev_prompt = (
            f"Ти — Senior Code Reviewer. Домен: {domain}. Задача: '{request}'.\n"
            f"Перевір наступний код ({rev_label}):\n"
            f"{rev_context}\n"
            f"{ruff_section}\n"
            f"Загальні категорії:\n"
            f"1. Логічні помилки та помилки часу виконання.\n"
            f"2. Порушення PEP 8 та PEP 484.\n"
            f"3. Потенційні вразливості безпеки.\n"
            f"4. Відсутню обробку виключень.\n\n"
            f"{domain_checklist}\n"
            f"ПРАВИЛА ВЕРДИКТУ:\n"
            f"  • Знайди МІНІМУМ 2 конкретні проблеми. Якщо їх дійсно немає —\n"
            f"    явно пройди КОЖНИЙ пункт доменного чек-листу в COMMENT (≥2 речення на кожний).\n"
            f"  • Синтаксична валідність / PEP-8 / type hints НЕ є підставою для APPROVED без перевірки семантики.\n\n"
            f"Поверни відповідь СТРОГО у форматі (одна з двох форм):\n"
            f"  STATUS: APPROVED\n"
            f"  COMMENT: <явний прохід по кожному пункту чек-листу>\n"
            f"АБО:\n"
            f"  STATUS: NEEDS_CHANGES\n"
            f"  REQUIRED_CHANGES:\n"
            f"  - <конкретна зміна 1>\n"
            f"  - <конкретна зміна 2>\n"
            f"  ...\n"
            f"Жодного коду в цій відповіді — тільки вердикт і зміни."
        )
        rev_output = _call_with_retry(
            lambda: jarvis.llm.generate_response(rev_prompt, is_system_call=True)
        )
        if not rev_output:
            return "Помилка на етапі Рев'юера."

        logger.debug("Reviewer iteration %d output length: %d", iteration, len(rev_output))

        if _cancelled():
            return "Ланцюжок агентів зупинено за запитом користувача."

        # APPROVED детекція жорстка: "STATUS: APPROVED" НА ОКРЕМОМУ РЯДКУ.
        # Раніше permissive regex ловив фрази на кшталт
        # "це НЕ STATUS: APPROVED, бо..." — і лооп виходив хибно.
        if re.search(r"^\s*STATUS:\s*APPROVED\s*$", rev_output, re.IGNORECASE | re.MULTILINE):
            approved = True
            logger.info("Reviewer APPROVED at iteration %d", iteration)
            break

        review_feedback = rev_output
        logger.info(
            "Reviewer requested changes at iteration %d/%d",
            iteration, _MAX_REVIEW_ITERATIONS,
        )

    if not approved:
        logger.warning(
            "Review loop exhausted %d iterations without APPROVED — proceeding "
            "with last Developer output to Refactorer.",
            _MAX_REVIEW_ITERATIONS,
        )

    if _cancelled():
        return "Ланцюжок агентів зупинено за запитом користувача."

    # ── Stage 4: Refactorer (with ast.parse validation & retry) ──────────────
    jarvis.audio.speak("Рефакторер оптимізує та фіналізує код.")

    base_ref_context = _trim(dev_output)
    base_ref_label = (
        f"скорочено до {_MAX_CONTEXT} символів"
        if len(dev_output) > _MAX_CONTEXT else "повний текст"
    )

    def _build_refactor_prompt(extra_instruction: str = "") -> str:
        return (
            f"Ти — Refactoring Expert. Задача: '{request}'.\n"
            f"Рефактор та оптимізуй наступний код ({base_ref_label}):\n"
            f"{base_ref_context}\n\n"
            f"Вимоги до рефакторингу:\n"
            f"1. Дотримання SOLID, DRY та KISS.\n"
            f"2. Видалення дублювання та мертвого коду.\n"
            f"3. Покращення читабельності (змінні, функції, структура).\n"
            f"4. Оптимізація продуктивності там, де це доцільно.\n"
            f"{extra_instruction}"
            f"ОБОВ'ЯЗКОВО поверни відповідь СТРОГО у форматі нижче — жодного тексту поза ним:\n"
            f"FILENAME: назва_файлу.py\n"
            f"CODE:\n"
            f"<тільки чистий Python код, без markdown-огорожі, тобто без символів ```>"
        )

    ref_output = _call_with_retry(
        lambda: jarvis.llm.generate_response(_build_refactor_prompt(), is_system_call=True)
    )
    if not ref_output:
        return "Помилка на етапі Рефакторера."

    logger.debug("Refactorer output length: %d", len(ref_output))

    # ── Parse + syntax validation with retry ─────────────────────────────────
    filename, clean_code = _parse_filename_and_code(ref_output)

    if not clean_code:
        return (
            "Відповідь агентного ланцюжка не містить секцію CODE: "
            "та не вдалося знайти блок коду — збереження скасовано."
        )

    syntax_ok, syntax_err = _validate_syntax(clean_code)
    syntax_attempt = 0
    while not syntax_ok and syntax_attempt < _MAX_SYNTAX_RETRIES:
        syntax_attempt += 1
        if _cancelled():
            return "Ланцюжок агентів зупинено за запитом користувача."

        logger.warning(
            "Refactorer output failed ast.parse (%s) — retry %d/%d",
            syntax_err, syntax_attempt, _MAX_SYNTAX_RETRIES,
        )
        jarvis.audio.speak(f"Виявлено синтаксичну помилку, спроба виправлення {syntax_attempt}.")

        fix_instruction = (
            f"5. ВАЖЛИВО: попередня версія мала синтаксичну помилку: '{syntax_err}'. "
            f"Виправ її та переконайся, що код парситься Python інтерпретатором.\n"
        )
        ref_output = _call_with_retry(
            lambda: jarvis.llm.generate_response(
                _build_refactor_prompt(fix_instruction), is_system_call=True
            )
        )
        if not ref_output:
            return "Помилка на етапі Рефакторера під час виправлення синтаксису."

        filename, clean_code = _parse_filename_and_code(ref_output)
        if not clean_code:
            return "Виправлена відповідь Рефакторера не містить коду."
        syntax_ok, syntax_err = _validate_syntax(clean_code)

    if not syntax_ok:
        return (
            f"Згенерований код не пройшов синтаксичну перевірку після "
            f"{_MAX_SYNTAX_RETRIES} спроб виправлення: {syntax_err}"
        )

    # ── Auto-format (best-effort: ruff + black if available) ─────────────────
    formatted_code = _autoformat(clean_code)
    # Re-validate after formatting in case a formatter mangled something
    # (shouldn't happen for valid code, but better safe than sorry).
    post_ok, _ = _validate_syntax(formatted_code)
    if post_ok:
        clean_code = formatted_code
    else:
        logger.warning("Auto-format produced invalid syntax — keeping unformatted code.")

    # ── Safety check ──────────────────────────────────────────────────────────
    safe, bad_pattern = check_code_safety(clean_code)
    if not safe:
        return f"Згенерований код містить небезпечний виклик ({bad_pattern}) та не збережено."

    # ── Save to SORTING_DRIVE ─────────────────────────────────────────────────
    target_dir = os.path.realpath(config.SORTING_DRIVE)
    os.makedirs(target_dir, exist_ok=True)

    if amend_mode:
        # Amend: overwrite the original file, but keep a timestamped backup first.
        filepath = target_filepath
        if not os.path.realpath(filepath).startswith(target_dir + os.sep):
            return "Шлях до файлу виходить за межі SORTING_DRIVE — збереження скасовано."
        try:
            backup_path = _backup_file(filepath)
            logger.info("Backup created: %s", backup_path)
        except OSError as exc:
            return f"Не вдалося створити резервну копію перед перезаписом: {exc}"
        _atomic_write(filepath, clean_code)
        final_filename = os.path.basename(filepath)
        backup_name = os.path.basename(backup_path)
        jarvis.audio.speak(
            f"Готово! Файл {final_filename} успішно оновлено. "
            f"Резервну копію збережено як {backup_name}."
        )
        return (
            f"Агентна корекція завершена. Файл '{final_filename}' оновлено у {target_dir}. "
            f"Резервна копія: '{backup_name}'."
        )
    else:
        # Create: pick a non-colliding filename.
        filepath_candidate = os.path.join(target_dir, filename)
        if not os.path.realpath(filepath_candidate).startswith(target_dir + os.sep):
            return "Назва файлу від агентів є некоректною — збереження скасовано."
        filepath = _unique_filepath(target_dir, filename)
        final_filename = os.path.basename(filepath)
        _atomic_write(filepath, clean_code)
        jarvis.audio.speak(
            f"Готово! Ланцюжок агентів завершив роботу. Файл {final_filename} збережено."
        )
        return f"Агентний ланцюжок завершено. Скрипт '{final_filename}' збережено у {target_dir}."
