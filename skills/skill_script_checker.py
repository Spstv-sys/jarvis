import os
import py_compile
import tempfile
import logging
import config
from rapidfuzz import process

logger = logging.getLogger(__name__)

TRIGGERS = [
    "перевір скрипт",
    "перевір помилки в скрипті",
    "знайди помилки в скрипті",
    "проаналізуй скрипт",
    "перевір код",
    "знайди помилки в коді",
    "налагодь скрипт",
    "дебаг скрипт",
    "що не так зі скриптом",
    "чому не працює скрипт",
]

def _normalize(s):
    return s.replace("_", "").replace("-", "").replace(" ", "").lower()

def _collect_scripts(base_dir):
    scripts = {}
    for folder in [base_dir, os.path.join(base_dir, "Code")]:
        if os.path.exists(folder):
            for f in os.listdir(folder):
                if f.endswith(".py"):
                    scripts[f] = os.path.join(folder, f)
    return scripts

def _find_script(search_term, scripts_map):
    normalized = {_normalize(k[:-3]): k for k in scripts_map}
    result = process.extractOne(_normalize(search_term), list(normalized.keys()))
    if result and result[1] >= 50:
        return normalized[result[0]]
    return None

def _check_syntax(filepath):
    """Перевірка синтаксису через py_compile."""
    try:
        py_compile.compile(filepath, doraise=True)
        return None  # немає помилок
    except py_compile.PyCompileError as e:
        return str(e)

def _check_imports(source):
    """Витягуємо імпорти і перевіряємо чи встановлені."""
    import importlib
    import re
    issues = []
    imports = re.findall(r'^[ ]*(?:import|from)[ ]+([a-zA-Z_][a-zA-Z0-9_]*)', source, re.MULTILINE)
    stdlib = {
        "os", "sys", "re", "time", "datetime", "json", "math", "random",
        "pathlib", "shutil", "subprocess", "threading", "logging", "io",
        "collections", "itertools", "functools", "typing", "abc", "copy",
        "tempfile", "py_compile", "importlib", "inspect", "traceback",
        "urllib", "http", "socket", "email", "csv", "sqlite3", "hashlib",
        "base64", "struct", "enum", "dataclasses", "contextlib", "warnings",
    }
    checked = set()
    for mod in imports:
        if mod in stdlib or mod in checked:
            continue
        checked.add(mod)
        try:
            importlib.import_module(mod)
        except ImportError:
            issues.append(mod)
    return issues

def execute(cmd, jarvis):
    base_dir = config.SORTING_DRIVE

    if not os.path.exists(base_dir):
        return f"Папка {base_dir} не знайдена. Перевірте підключення диска."

    scripts_map = _collect_scripts(base_dir)

    if not scripts_map:
        return f"Жодного скрипту не знайдено у {base_dir}. Спочатку створіть скрипт."

    # Витягуємо назву скрипту з команди
    search_term = cmd.lower()
    for t in TRIGGERS:
        search_term = search_term.replace(t, "")
    search_term = search_term.strip()

    if not search_term:
        return "Назву якого скрипту перевірити? Наприклад: 'перевір скрипт caloriecalculator'"

    filename = _find_script(search_term, scripts_map)
    if not filename:
        return f"Скрипт схожий на '{search_term}' не знайдено. Скажіть 'які скрипти є' щоб побачити список."

    filepath = scripts_map[filename]

    jarvis.audio.speak(f"Перевіряю скрипт {filename}. Зачекайте.")

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            source = f.read()
    except Exception as e:
        return f"Не вдалося прочитати файл {filename}: {e}"

    results = [f"=== Перевірка скрипту: {filename} ===", ""]

    # 1. Синтаксис
    syntax_error = _check_syntax(filepath)
    if syntax_error:
        results.append(f"❌ СИНТАКСИЧНА ПОМИЛКА:\n   {syntax_error}")
    else:
        results.append("✅ Синтаксис: помилок не знайдено")

    # 2. Імпорти
    missing_imports = _check_imports(source)
    if missing_imports:
        results.append(f"⚠️  Не встановлені бібліотеки: {', '.join(missing_imports)}")
        results.append(f"   Встановіть: pip install {' '.join(missing_imports)}")
    else:
        results.append("✅ Імпорти: всі бібліотеки знайдено")

    results.append("")

    # 3. ШІ-аналіз логічних помилок
    results.append("🤖 ШІ-аналіз коду:")
    prompt = (
        f"Ти Senior Python розробник і code reviewer. Проаналізуй цей скрипт '{filename}'.\n"
        f"Знайди:\n"
        f"1. Логічні помилки та баги\n"
        f"2. Потенційні збої (необроблені виключення, edge cases)\n"
        f"3. Проблеми з безпекою або продуктивністю\n"
        f"4. Конкретні рекомендації по покращенню\n\n"
        f"Якщо код правильний — скажи це прямо.\n"
        f"Відповідай українською мовою, конкретно і по суті.\n\n"
        f"КОД:\n{source[:4000]}"
    )

    ai_review = jarvis.llm.generate_response(prompt, is_system_call=True)
    if ai_review:
        results.append(ai_review)
    else:
        results.append("Не вдалося отримати відповідь від ШІ.")

    return "\n".join(results)