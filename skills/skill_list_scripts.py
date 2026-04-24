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

def _collect_scripts(base_dir):
    """Збираємо скрипти з кореневої папки І з підпапки Code."""
    scripts = {}  # filename -> full_path
    for folder in [base_dir, os.path.join(base_dir, "Code")]:
        if os.path.exists(folder):
            for f in os.listdir(folder):
                if f.endswith(".py"):
                    scripts[f] = os.path.join(folder, f)
    return scripts

def _find_script(search_term, scripts_map):
    """Fuzzy пошук скрипту за назвою."""
    normalized = {_normalize(k[:-3]): k for k in scripts_map}
    result = process.extractOne(_normalize(search_term), list(normalized.keys()))
    if result and result[1] >= 55:
        return normalized[result[0]]
    return None

def execute(cmd, jarvis):
    base_dir = os.getenv("JARVIS_DRIVE", "D:\\Jarvis_Sorted")

    if not os.path.exists(base_dir):
        return f"Папка {base_dir} не знайдена. Перевірте підключення диска."

    # Збираємо скрипти з кореня і з \Code
    scripts_map = _collect_scripts(base_dir)

    if not scripts_map:
        return f"У мене поки що немає жодного збереженого скрипту. (шукав у {base_dir} та {base_dir}\\Code)"

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

        filename = _find_script(search_term, scripts_map)
        if not filename:
            return f"Скрипт схожий на '{search_term}' не знайдено. Скажіть 'які скрипти є' щоб побачити список."

        filepath = scripts_map[filename]
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

        lines = [f"Скрипти ({len(scripts_map)} шт.):", "-" * 40]
        for i, (name, path) in enumerate(sorted(scripts_map.items()), 1):
            folder_label = "\\Code" if "Code" in path else "     "
            size_kb = os.path.getsize(path) / 1024
            lines.append(f"{i:2}. [{folder_label}] {name:<30} ({size_kb:.1f} KB)")
        lines.append("-" * 40)
        lines.append("Скажіть 'що робить скрипт [назва]' для детального опису.")

        return "\n".join(lines)