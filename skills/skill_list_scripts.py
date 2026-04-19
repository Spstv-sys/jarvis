import os
import logging
from rapidfuzz import process

logger = logging.getLogger(__name__)

TRIGGERS = [
    "які скрипти є",
    "які скрипти у тебе є",
    "покажи скрипти",
    "список скриптів",
    "які є скрипти",
    "що є в скриптах",
    "що робить скрипт",
    "для чого скрипт",
    "навіщо скрипт",
    "поясни скрипт",
    "опиши скрипт",
    "розкажи про скрипт",
]

def _normalize(s):
    return s.replace("_", "").replace("-", "").replace(" ", "").lower()

def _find_script(search_term, scripts):
    scripts_map = {_normalize(f[:-3]): f for f in scripts}
    result = process.extractOne(_normalize(search_term), list(scripts_map.keys()))
    if result and result[1] >= 55:
        return scripts_map[result[0]]
    return None

def execute(cmd, jarvis):
    code_dir = os.getenv("JARVIS_DRIVE", "H:\\Jarvis_Sorted")

    if not os.path.exists(code_dir):
        return f"Папка {code_dir} не знайдена. Перевірте підключення диска."

    scripts = [f for f in os.listdir(code_dir) if f.endswith(".py")]

    if not scripts:
        return "У мене поки що немає жодного збереженого скрипту."

    cmd_lower = cmd.lower()
    is_describe = any(t in cmd_lower for t in [
        "що робить скрипт", "для чого скрипт", "навіщо скрипт",
        "поясни скрипт", "опиши скрипт", "розкажи про скрипт"
    ])

    if is_describe:
        search_term = cmd_lower
        for t in TRIGGERS:
            search_term = search_term.replace(t, "")
        search_term = search_term.strip()

        if not search_term:
            return "Назву якого скрипту пояснити?"

        filename = _find_script(search_term, scripts)
        if not filename:
            return f"Скрипт схожий на '{search_term}' не знайдено. Скажіть 'які скрипти є' щоб побачити список."

        filepath = os.path.join(code_dir, filename)
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                source = f.read()
        except Exception as e:
            return f"Не вдалося прочитати файл {filename}: {e}"

        jarvis.audio.speak(f"Аналізую скрипт {filename}...")

        prompt = (
            f"Ти Python-розробник. Проаналізуй цей скрипт '{filename}' і поясни:\n"
            f"1. Що він робить (1-2 речення)\n"
            f"2. Як використовувати (коротко)\n"
            f"3. Які бібліотеки використовує\n\n"
            f"Відповідай українською мовою, лаконічно, без зайвого.\n\n"
            f"КОД:\n{source[:3000]}"
        )

        result = jarvis.llm.generate_response(prompt, is_system_call=True)
        return result if result else f"Не вдалося проаналізувати скрипт {filename}."

    else:
        jarvis.audio.speak("Ось мої доступні скрипти.")

        lines = [f"Скрипти у {code_dir} ({len(scripts)} шт.):", "-" * 40]
        for i, name in enumerate(sorted(scripts), 1):
            filepath = os.path.join(code_dir, name)
            size_kb = os.path.getsize(filepath) / 1024
            lines.append(f"{i:2}. {name:<35} ({size_kb:.1f} KB)")
        lines.append("-" * 40)
        lines.append("Скажіть 'що робить скрипт [назва]' для детального опису.")

        return "\n".join(lines)