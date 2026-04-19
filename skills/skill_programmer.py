import os
import time
import re

TRIGGERS = [
    "напиши код",
    "напиши скрипт",
    "запрограмуй",
    "створи скрипт",
    "створи код",
    "зроби скрипт",
    "зроби програму",
    "напиши програму",
    "генеруй код",
    "напиши функцію",
    "створи функцію",
]

def execute(cmd, jarvis):
    request = cmd
    for t in TRIGGERS:
        request = request.replace(t, "")
    request = request.strip()

    if not request:
        return "Що саме мені потрібно запрограмувати?"

    jarvis.audio.speak("Аналізую задачу та формую структуру файлу.")

    # Просимо ШІ видати назву і код
    prompt = (
        f"Ти Senior Python Developer. Завдання: '{request}'.\n"
        f"Поверни відповідь у форматі:\n"
        f"FILENAME: назва_файлу.py\n"
        f"CODE:\n"
        f"ваш_код\n\n"
        f"ПРАВИЛО: Назва має бути короткою, латиницею, через підкреслення. Тільки чистий код."
    )

    response = jarvis.llm.generate_response(prompt, is_system_call=True)
    if not response: return "Помилка зв'язку з ШІ."

    # Парсимо назву та код
    try:
        filename_match = re.search(r"FILENAME:\s*(.*\.py)", response)
        filename = filename_match.group(1).strip() if filename_match else f"script_{int(time.time())}.py"
        
        code_part = response.split("CODE:")[1].strip()
        clean_code = code_part.replace("```python", "").replace("```", "").strip()
    except Exception:
        return "Не вдалося розібрати структуру коду від ШІ."

    # Збереження на диск H:
    target_dir = os.getenv("JARVIS_DRIVE", "H:\\Jarvis_Sorted")
    code_dir = os.path.join(target_dir, "Code")
    os.makedirs(code_dir, exist_ok=True)
    filepath = os.path.join(code_dir, filename)

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(clean_code)
    
    return f"Скрипт '{filename}' успішно створено та збережено."