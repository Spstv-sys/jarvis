import os
import subprocess

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
    code_dir = os.path.join(os.getenv("JARVIS_DRIVE", "H:\\Jarvis_Sorted"), "Code")
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
    selected_file = ai_decision.strip()

    # 3. Аналіз рішення та запуск
    if selected_file == "NONE" or selected_file not in available_files:
        return f"Я переглянув свої файли, але не знайшов скрипта, який підходить для задачі: {task_description}."

    jarvis.audio.speak(f"Використовую скрипт {selected_file}.")

    # Запуск обраного скрипта
    try:
        filepath = os.path.join(code_dir, selected_file)
        result = subprocess.run(["python", filepath], capture_output=True, text=True, timeout=15)
        
        if result.stderr:
            return f"Під час роботи скрипта виникла помилка: {result.stderr[-150:]}"
        
        output = result.stdout.strip()
        
        if len(output) > 400:
            return f"Задачу виконано. Результат завеликий, ось початок: {output[:250]}..."
            
        return f"Результат: {output if output else 'Скрипт успішно відпрацював.'}"

    except subprocess.TimeoutExpired:
        return "Скрипт завис і був примусово зупинений через 15 секунд."
    except Exception as e:
        return f"Не вдалося виконати скрипт: {str(e)}"