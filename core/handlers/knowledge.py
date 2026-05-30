"""KnowledgeMixin — оновлення бази знань і керування навичками."""
from __future__ import annotations

import os
import time
import shutil
import logging

import config
from core.utils import strip_triggers
from core.handlers._safety import _check_skill_safety

logger = logging.getLogger(__name__)


class KnowledgeMixin:
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
        assistant_name = getattr(config, "ASSISTANT_NAME", "Нео")
        prompt = (
            "Ти Senior Python Engineer з 10+ роками досвіду.\n"
            f"Напиши плагін для голосового асистента {assistant_name}.\n"
            f'Задача: "{request}".\n\n'
            "ОБОВ'ЯЗКОВІ ВИМОГИ (рівень Senior):\n"
            "1. ПОВЕРНИ ТІЛЬКИ ЧИСТИЙ PYTHON КОД — без markdown, без ``` блоків, без пояснень.\n"
            "2. Перша змінна — `TRIGGERS: list[str]` — список українських фраз-тригерів.\n"
            "3. Функція `def execute(cmd: str, jarvis) -> str:` — завжди повертає рядок.\n"
            "4. Type annotations на всіх функціях та нетривіальних змінних.\n"
            "5. Google-style docstring для кожної публічної функції.\n"
            "6. Конкретні типи виключень у except: НЕ `except:` та НЕ `except Exception` — використовуй ValueError, OSError тощо.\n"
            "7. Логування через `import logging; logger = logging.getLogger(__name__)` — НЕ print.\n"
            "8. Валідація вхідних аргументів на початку `execute`: перевір, що `cmd` не порожній після strip.\n"
            "9. Лише константи у верхньому регістрі як глобальні змінні (TRIGGERS, MAX_RETRIES тощо).\n"
            "10. НЕ пиши жодних пояснень або коментарів поза кодом."
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
            pending_files = [
                f for f in os.listdir(pending_dir) if f.endswith(".py")
            ] if os.path.exists(pending_dir) else []
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
            shutil.move(src, dst)
            self.jarvis.load_skills()
            self.audio.speak(f"Навичку {name} активовано та інтегровано до системи.")
            logger.info(f"cmd_activate_skill: навичку активовано: {name}")
        except Exception as e:
            logger.error(f"cmd_activate_skill: помилка активації {name}: {e}")
            self.audio.speak("Виникла помилка під час активації навички.")
        return "CONTINUE"
