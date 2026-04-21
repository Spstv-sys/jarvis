"""
KnowledgeHandlersMixin — навички та векторна база знань.

Відокремлено від CommandHandlers для зменшення розміру God Class.
"""
import ast
import os
import re
import time
import logging

import config
from core.utils import strip_triggers

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Заборонені паттерни в LLM-згенерованому коді (два шари захисту)
# ---------------------------------------------------------------------------
_FORBIDDEN_SKILL_PATTERNS = [
    r'\bos\.system\b',
    r'\bsubprocess\b',
    r'\b__import__\b',
    r'\beval\b',
    r'\bexec\b',
]

# Модулі, імпорт яких блокується під час AST-аналізу
_FORBIDDEN_IMPORT_NAMES = frozenset({
    "subprocess", "ctypes", "importlib", "builtins",
})

# Вбудовані функції, виклик яких блокується
_FORBIDDEN_CALL_NAMES = frozenset({
    "eval", "exec", "__import__", "compile",
})

# Небезпечні атрибути (os.system, os.popen тощо)
_FORBIDDEN_ATTR_CALLS = frozenset({
    "system", "popen", "call", "run", "Popen",
    "check_output", "check_call",
    "spawn", "spawnl", "spawnle", "spawnlp",
    "spawnv", "spawnve", "spawnvp", "spawnvpe",
})


def _check_skill_safety(code: str) -> tuple[bool, str]:
    """Return (True, '') if code passes all safety checks, (False, reason) otherwise.

    Uses two layers:
    1. Regex scan — fast catch of obvious patterns.
    2. AST analysis — catches semantic violations invisible to regex
       (e.g. ``os . system(...)`` or aliased imports).
    """
    # Layer 1: fast regex scan
    for pattern in _FORBIDDEN_SKILL_PATTERNS:
        if re.search(pattern, code):
            return False, pattern

    # Layer 2: AST analysis
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return False, f"SyntaxError: {exc}"

    for node in ast.walk(tree):
        # Block imports of forbidden modules
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in _FORBIDDEN_IMPORT_NAMES:
                    return False, f"import {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top = node.module.split(".")[0]
                if top in _FORBIDDEN_IMPORT_NAMES:
                    return False, f"from {node.module} import ..."
        # Block dangerous built-in calls
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in _FORBIDDEN_CALL_NAMES:
                return False, func.id
            if isinstance(func, ast.Attribute) and func.attr in _FORBIDDEN_ATTR_CALLS:
                return False, f"*.{func.attr}"

    return True, ""


class KnowledgeHandlersMixin:
    """Handlers for knowledge base and skill management."""

    # ==================================================================
    # KNOWLEDGE
    # ==================================================================
    def cmd_update_knowledge(self, cmd: str) -> str:
        logger.info(f"cmd_update_knowledge: {cmd!r}")
        self.audio.speak(
            "Починаю сканування документів та оновлення векторної бази знань. "
            "Це може зайняти деякий час."
        )
        try:
            self.brain.ingest_documents()
            self.audio.speak("Базу знань успішно оновлено.")
        except Exception as e:
            logger.error(f"Помилка оновлення бази знань: {e}")
            self.audio.speak("Виникла помилка під час індексації документів.")
        return "CONTINUE"

    # ==================================================================
    # SKILLS
    # ==================================================================
    def cmd_learn_skill(self, cmd: str) -> str:
        logger.info(f"cmd_learn_skill: {cmd!r}")
        request = strip_triggers(cmd, config.STOP_WORDS_LEARN_SKILL)

        if not request:
            self.audio.speak("Чому саме я маю навчитися?")
            return "CONTINUE"

        self.audio.speak("Пишу код для нової навички. Це займе кілька секунд.")
        prompt = (
            "Ти Senior Python Developer. Напиши плагін для голосового асистента Джарвіс.\n"
            f'Користувач просить: "{request}".\n'
            "Правила:\n"
            "1. ПОВЕРНИ ТІЛЬКИ ЧИСТИЙ PYTHON КОД — без пояснень, без markdown, без коментарів.\n"
            "2. Код ПОВИНЕН мати список `TRIGGERS` — список українських фраз-тригерів.\n"
            "3. Код ПОВИНЕН мати функцію `def execute(cmd, jarvis):`, яка повертає РЯДОК.\n"
            "4. НЕ додавай ```python або ``` — тільки голий код.\n"
            "5. НЕ пиши жодних пояснень до або після коду."
        )
        try:
            clean_code = self.llm.generate_response(prompt, is_system_call=True)
            if not clean_code:
                self.audio.speak("Нейромережа не змогла згенерувати навичку.")
                return "CONTINUE"

            clean_code = clean_code.replace("```python", "").replace("```", "").strip()

            safe, bad_pattern = _check_skill_safety(clean_code)
            if not safe:
                logger.warning(f"cmd_learn_skill: небезпечний патерн {bad_pattern!r} у згенерованому коді")
                self.audio.speak(
                    "Згенерований код містить небезпечні виклики та не може бути збережений."
                )
                return "CONTINUE"

            filename = f"skill_{int(time.time())}.py"
            pending_dir = os.path.join(self.jarvis.skills_dir, "pending")
            os.makedirs(pending_dir, exist_ok=True)
            filepath = os.path.join(pending_dir, filename)

            with open(filepath, "w", encoding="utf-8") as f:
                f.write(clean_code)

            logger.info(f"cmd_learn_skill: навичка збережена у pending: {filename}")
            self.audio.speak(
                f"Нову навичку збережено у файл {filename} у папку перевірки. "
                f"Скажіть 'активуй навичку {filename}', щоб інтегрувати її до системи."
            )
        except Exception as e:
            logger.error(f"Помилка створення навички: {e}")
            self.audio.speak("Виникла помилка під час програмування навички.")
        return "CONTINUE"

    def cmd_activate_skill(self, cmd: str) -> str:
        logger.info(f"cmd_activate_skill: {cmd!r}")
        pending_dir = os.path.join(self.jarvis.skills_dir, "pending")

        name = strip_triggers(cmd, config.TRIGGERS.get("activate_skill", []))
        name = name.strip()

        if not name:
            pending_files = (
                [f for f in os.listdir(pending_dir) if f.endswith(".py")]
                if os.path.exists(pending_dir) else []
            )
            if not pending_files:
                self.audio.speak("Немає навичок, що очікують активації.")
            else:
                self.audio.speak(
                    f"Наступні навички очікують активації: {', '.join(pending_files)}."
                )
            return "CONTINUE"

        if not name.endswith(".py"):
            name += ".py"

        src = os.path.join(pending_dir, name)
        dst = os.path.join(self.jarvis.skills_dir, name)

        if not os.path.exists(src):
            self.audio.speak(f"Навичку {name} не знайдено у папці перевірки.")
            return "CONTINUE"

        try:
            import shutil
            shutil.move(src, dst)
            self.jarvis.load_skills()
            self.audio.speak(f"Навичку {name} активовано та інтегровано до системи.")
            logger.info(f"cmd_activate_skill: навичку активовано: {name}")
        except Exception as e:
            logger.error(f"cmd_activate_skill: помилка активації {name}: {e}")
            self.audio.speak("Виникла помилка під час активації навички.")
        return "CONTINUE"
