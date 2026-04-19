import os

# 1. КЛЮЧІ ДОСТУПУ (Шорткати)
# Залишено лише одну папку. Пошук іде за коренем "секретн"
MY_FOLDERS = {
    "секретн": r"H:\Sicrets"
}

# Тригери для запуску навички
TRIGGERS = [
    "що в папці",
    "що лежить в",
    "що лежить у",
    "аналізуй папку",
    "аналізуй директорію",
    "скануй папку",
    "скануй директорію",
    "перевір папку",
    "перевір директорію",
    "структура папки",
    "структура директорії",
    "що в секретній",
    "вміст папки",
    "вміст директорії",
]

def execute(cmd, jarvis):
    request = cmd.lower()

    # 2. Перевіряємо наявність кореня слова у вашій фразі
    target_dir = None
    folder_name_spoken = ""
    
    for root_word, path in MY_FOLDERS.items():
        if root_word in request:
            target_dir = path
            # Нормалізуємо назву для озвучки
            folder_name_spoken = "Секретна папка"
            break

    # Якщо користувач не вимовив слово-ключ
    if not target_dir:
        return "Я маю доступ лише до вашої Секретної папки. Уточніть запит."

    # Перевіряємо, чи існує папка фізично на диску H:
    if not os.path.exists(target_dir):
        return f"{folder_name_spoken} не знайдена за вказаним шляхом. Перевірте підключення диска."

    jarvis.audio.speak(f"Доступ підтверджено. Сканую вміст: {folder_name_spoken}...")

    # 3. Читаємо вміст папки
    try:
        items = os.listdir(target_dir)
        
        if not items:
            return f"{folder_name_spoken} абсолютно порожня."

        files = [f for f in items if os.path.isfile(os.path.join(target_dir, f))]
        folders = [d for d in items if os.path.isdir(os.path.join(target_dir, d))]

        # Обмежуємо кількість для ШІ
        files_str = ", ".join(files[:50])
        folders_str = ", ".join(folders[:20])

        # 4. Аналіз через нейромережу
        prompt = (
            f"Ти системний аналітик. Користувач запитує про свою '{folder_name_spoken}'.\n"
            f"Я (Python) прочитав її і знайшов {len(folders)} підпапок та {len(files)} файлів.\n"
            f"Ось папки: {folders_str}\n"
            f"Ось файли: {files_str}\n\n"
            f"Зроби короткий огляд того, що знаходиться в цій директорії (які типи файлів переважають, "
            f"для чого, ймовірно, використовується ця папка). Не перераховуй все підряд. "
            f"Зберігай професійний, але лаконічний тон."
        )

        answer = jarvis.llm.generate_response(prompt, is_system_call=True)
        return answer if answer else "Сканування завершено, але я не зміг згенерувати звіт."

    except PermissionError:
        return f"Windows заблокував мені доступ до папки. Недостатньо прав."
    except Exception as e:
        return f"Виникла помилка під час читання папки: {e}"