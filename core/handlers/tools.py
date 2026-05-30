"""ToolsMixin — vision (аналіз екрана), code-assistant, диктування."""
from __future__ import annotations

import os
import re
import logging

import config
from core.utils import strip_triggers

logger = logging.getLogger(__name__)

try:
    from PIL import Image as PILImage
except ImportError:
    PILImage = None

try:
    import pyautogui
except ImportError:
    pyautogui = None


class ToolsMixin:
    # ==================================================================
    # VISION
    # ==================================================================
    def cmd_vision(self, cmd: str) -> str:
        logger.info(f"cmd_vision: {cmd!r}")
        self.audio.speak("Аналізую екран...")
        shot_path = "temp_screen.png"
        try:
            if not self.llm.gemini_client:
                self.audio.speak("Vision API недоступне. Активуйте Gemini.")
                return "CONTINUE"
            if pyautogui is None:
                self.audio.speak("Бібліотека pyautogui не встановлена.")
                return "CONTINUE"
            pyautogui.screenshot(shot_path)
            if PILImage is None:
                self.audio.speak("Бібліотека PIL не встановлена.")
                return "CONTINUE"
            img   = PILImage.open(shot_path)
            query = (
                strip_triggers(cmd, config.TRIGGERS["vision"])
                or "Що на екрані? Відповідай коротко."
            )
            res = self.llm.gemini_client.models.generate_content(
                model='gemini-2.5-flash', contents=[img, query]
            )
            self.audio.speak(res.text)
        except Exception as e:
            logger.error(f"Помилка Vision API: {e}")
            self.audio.speak("Не вдалося проаналізувати зображення.")
        finally:
            if os.path.exists(shot_path):
                os.remove(shot_path)
        return "CONTINUE"

    # ==================================================================
    # CODE ASSISTANT
    # ==================================================================
    def cmd_code_assistant(self, cmd: str) -> str:
        logger.info(f"cmd_code_assistant: {cmd!r}")
        from core.utils import normalize_filename
        n_cmd = normalize_filename(cmd)
        match = re.search(r'([a-zA-Z0-9_\-\.]+\.[a-zA-Z0-9]{2,5})', n_cmd)
        if not match:
            self.audio.speak("Будь ласка, скажіть точну назву файлу з розширенням.")
            return "CONTINUE"
        # Use basename to prevent path traversal (e.g. "../../etc/passwd.py")
        filename = os.path.basename(match.group(1))
        if not filename:
            self.audio.speak("Назва файлу некоректна.")
            return "CONTINUE"
        search_dirs = [
            os.getcwd(),
            os.path.join(config.SORTING_DRIVE, "Code"),
        ]
        file_path = None
        for d in search_dirs:
            real_d = os.path.realpath(d)
            candidate = os.path.realpath(os.path.join(d, filename))
            if candidate.startswith(real_d + os.sep) and os.path.exists(candidate):
                file_path = candidate
                break
        if not file_path:
            self.audio.speak(f"Не можу знайти файл {filename}.")
            return "CONTINUE"
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()[:8000]
            if "виправ" in cmd:
                self.audio.speak("Виправляю архітектуру...")
                fixed = self.llm.generate_response(
                    prompt=(
                        "Ти Senior Python Engineer. Рефактор цього коду до рівня Senior:\n"
                        "- Додай type annotations (PEP 484) на всі функції.\n"
                        "- Замін bare `except Exception` на конкретні типи виключень.\n"
                        "- Замін print() на logging.getLogger(__name__).\n"
                        "- Додай Google-style docstrings де їх немає.\n"
                        "- Валідуй вхідні аргументи функцій.\n"
                        "- Дотримуйся SOLID та DRY принципів.\n"
                        "ПОВЕРНИ ТІЛЬКИ КОД без markdown та пояснень:\n"
                        + content
                    ),
                    is_system_call=True,
                )
                if not fixed:
                    self.audio.speak("Нейромережа не змогла згенерувати виправлення.")
                    return "CONTINUE"
                base, ext = os.path.splitext(file_path)
                new_path  = f"{base}_fixed{ext}"
                with open(new_path, "w", encoding="utf-8") as f:
                    f.write(fixed.replace("```python", "").replace("```", "").strip())
                self.audio.speak(f"Код збережено у файл {os.path.basename(new_path)}.")
            else:
                self.audio.speak("Аналізую логіку...")
                prompt = (
                    f"Ти Senior Python Engineer. "
                    f"Проведи короткий code-review файлу {filename}. "
                    f"Вкажи: (1) що робить код, (2) порушення Senior-рівня "
                    f"(відсутні type hints, bare except, print замість logging, відсутні docstrings), "
                    f"(3) одну конкретну пропозицію покращення. "
                    f"Відповідь — 3-4 речення.\n\nКод:\n{content}"
                )
                answer = self.llm.generate_response(prompt, is_system_call=True)
                if answer:
                    self.audio.speak(answer)
        except Exception as e:
            logger.error(f"Помилка при читанні файлу {filename}: {e}")
            self.audio.speak("Виникла системна помилка доступу до файлу.")
        return "CONTINUE"

    # ==================================================================
    # DICTATION
    # ==================================================================
    def cmd_dictation(self, cmd: str) -> str:
        logger.info(f"cmd_dictation: {cmd!r}")
        self.audio.speak("Режим диктування. Слухаю.")
        text = self.audio.listen_for_command(dictation_mode=True)
        if not text:
            self.audio.speak("Диктування скасовано.")
            return "CONTINUE"
        self.audio.speak("Обробляю...")
        try:
            summary = self.llm.generate_response(
                prompt=f"Зроби конспект цього тексту: {text}",
                is_system_call=True,
            )
            if summary:
                self.audio.speak("Конспект готовий:")
                self.audio.speak(summary)
        except Exception as e:
            logger.error(f"Помилка ШІ при диктуванні: {e}")
            self.audio.speak("Помилка обробки тексту.")
        return "CONTINUE"
