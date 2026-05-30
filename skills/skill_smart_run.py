import os
import subprocess
import sys
import config
from core.sandbox import run_python

# Робимо тригери максимально природними
TRIGGERS = [
    "використай скрипт для", 
    "знайди інструмент щоб", 
    "запусти програму яка", 
    "зроби парсинг через скрипт",
    "застосуй скрипт"
]

def execute(cmd, jarvis):
    # Відрізаємо слова-тригери, залишаємо саму суть задачі
    task_description = cmd
    for t in TRIGGERS:
        task_description = task_description.replace(t, "")
    task_description = task_description.strip()

    if not task_description:
        return "Для чого саме мені потрібно використати скрипт?"

    # 1. Знаходимо всі доступні скрипти
    code_dir = os.path.realpath(
        os.path.join(config.SORTING_DRIVE, "Code")
    )
    if not os.path.exists(code_dir):
        return "Папка зі скриптами ще не створена."

    available_files = [f for f in os.listdir(code_dir) if f.endswith(".py")]
    
    if not available_files:
        return "У мене ще немає жодного збереженого скрипта."

    jarvis.audio.speak("Підбираю найкращий інструмент для цієї задачі...")

    # 2. Семантичний пошук через нейромережу
    prompt = (
        f"Ти AI-маршрутизатор. Твоя задача — вибрати правильний скрипт для виконання запиту користувача.\n"
        f"Запит користувача: '{task_description}'\n\n"
        f"Список доступних файлів:\n{', '.join(available_files)}\n\n"
        f"ПРАВИЛО: Виведи ТІЛЬКИ точну назву файлу (наприклад, script.py). "
        f"Якщо жоден файл не підходить за змістом, виведи слово NONE. Ніяких пояснень."
    )

    # Тихий виклик ШІ для прийняття рішення
    ai_decision = jarvis.llm.generate_response(prompt, is_system_call=True)

    if not ai_decision:
        return "Помилка нейромережі під час вибору скрипта."

    # Очищаємо відповідь від можливого сміття
    selected_file = os.path.basename(ai_decision.strip())

    # 3. Аналіз рішення: файл має бути у відомому списку та в межах code_dir
    if selected_file == "NONE" or selected_file not in available_files:
        return f"Я переглянув свої файли, але не знайшов скрипта, який підходить для задачі: {task_description}."

    filepath = os.path.realpath(os.path.join(code_dir, selected_file))
    if not filepath.startswith(code_dir + os.sep):
        return "Шлях до скрипта є некоректним — виконання скасовано."

    jarvis.audio.speak(f"Використовую скрипт {selected_file}.")

    # 4. Запуск обраного скрипта (у пісочниці)
    try:
        result = run_python(filepath, timeout=15)

        if result.timed_out:
            return "Скрипт завис і був примусово зупинений через 15 секунд."
        if not result.ok and result.stderr:
            return f"Під час роботи скрипта виникла помилка ({result.engine}): {result.stderr[-150:]}"

        output = (result.stdout or "").strip()

        if len(output) > 400:
            return f"Задачу виконано ({result.engine}). Результат завеликий, ось початок: {output[:250]}..."

        return f"Результат ({result.engine}): {output if output else 'Скрипт успішно відпрацював.'}"

    except Exception as e:
        return f"Не вдалося виконати скрипт: {str(e)}"