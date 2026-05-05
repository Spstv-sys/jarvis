import os
import re
from datetime import datetime
from colorama import Fore
import config

# 1. СЛОВА-ТРИГЕРИ (якщо Джарвіс чує це, він запускає цей плагін)
TRIGGERS = ["згенеруй тест кейси для", "напиши тести для", "протестуй функціонал", "зроби чек-лист для", "чек-лист для"]

def execute(cmd, jarvis):
    """Головна функція плагіна"""
    
    # Витягуємо суть того, що треба протестувати
    target = cmd
    for t in TRIGGERS:
        target = target.replace(t, "")
    target = target.strip(" .")

    if not target:
        return "Будь ласка, вкажіть конкретний функціонал для тестування. Наприклад: 'згенеруй тест кейси для форми входу'."

    jarvis.audio.speak(f"Прийнято. Запускаю протокол QA-аналізу для модуля: {target}. Генерую тестове покриття.")
    print(f"{Fore.CYAN}🧪 [QA Plugin]: Аналіз функціоналу '{target}'...")

    # Суворий системний промпт для генерації професійного QA-звіту
    prompt = f"""
    Ти Senior QA Engineer з 10+ роками досвіду. Задача: створити детальний чек-лист тестування для: "{target}".

    ВИМОГИ ДО ЗВІТУ:
    1. Структура: Позитивні тести → Негативні тести → Граничні значення (Edge Cases) → Нефункціональні (безпека, продуктивність).
    2. Кожен тест-кейс: ID, Назва, Передумова, Кроки, Очікуваний результат.
    3. Покрий: валідацію введення, SQL/XSS ін'єкції, таймаути, конкурентний доступ, відмову залежностей.
    4. Використовуй BDD-термінологію (Given/When/Then) для складних сценаріїв.
    5. Не пиши вступних слів — тільки готовий до роботи Markdown-чек-лист.
    """

    try:
        # Робимо запит до нейромережі (не додаючи це в історію короткої розмови)
        qa_report = jarvis.llm.generate_response(prompt, is_system_call=True)
        
        # Створюємо безпечне ім'я файлу (прибираємо пробіли та спецсимволи)
        safe_target = re.sub(r'[^a-zA-Zа-яА-Я0-9]', '_', target)[:20]
        filename = f"QA_Report_{safe_target}_{datetime.now().strftime('%H%M%S')}.md"
        
        # Визначаємо шлях збереження (Твоя робоча папка Documents)
        save_dir = config.DOCUMENTS_DIR
        os.makedirs(save_dir, exist_ok=True)
        
        filepath = os.path.join(save_dir, filename)
        
        # Зберігаємо звіт
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(f"# QA Звіт: {target.upper()}\n")
            f.write(f"Дата генерації: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            f.write(qa_report)
            
        print(f"{Fore.GREEN}✅ [QA Plugin]: Звіт збережено -> {filepath}")
        return f"Аналіз завершено. Професійний QA-звіт збережено у папку Документи під назвою {filename}."

    except Exception as e:
        print(f"{Fore.RED}❌ [QA Plugin] Помилка: {e}")
        return "Виникла помилка під час генерації тест-кейсів."