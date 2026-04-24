import os
import subprocess
import sys
import config
from rapidfuzz import process

TRIGGERS = ["запусти скрипт", "виконай програму", "відкрий інструмент"]

def execute(cmd, jarvis):
    # Витягуємо назву, яку шукає користувач
    search_term = cmd
    for t in TRIGGERS:
        search_term = search_term.replace(t, "")
    search_term = search_term.strip().lower()

    code_dir = os.path.join(config.SORTING_DRIVE, "Code")
    
    if not os.path.exists(code_dir):
        return "Папка зі скриптами ще не створена."

    # Список усіх файлів .py
    files = [f for f in os.listdir(code_dir) if f.endswith(".py")]
    if not files:
        return "У моїй базі ще немає жодного скрипта."

    if not search_term:
        return f"Які саме скрипти вас цікавлять? У мене є: {', '.join(files[:5])}"

    # Нечіткий пошук назви файлу
    match, score, _ = process.extractOne(search_term, files)

    if score < 50:
        return f"Я не впевнений, що знайшов '{search_term}'. Найближчий варіант — {match}, але збіг замалий."

    jarvis.audio.speak(f"Знайшов {match}. Запускаю.")

    try:
        filepath = os.path.join(code_dir, match)
        result = subprocess.run([sys.executable, filepath], capture_output=True, text=True, timeout=15)
        
        if result.stderr:
            return f"Помилка у скрипті: {result.stderr[-150:]}"
        
        output = result.stdout.strip()
        return f"Виконано. Результат: {output if output else 'Скрипт відпрацював без виводу.'}"

    except Exception as e:
        return f"Не вдалося виконати: {str(e)}"