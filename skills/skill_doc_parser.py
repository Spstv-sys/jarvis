import requests
from bs4 import BeautifulSoup
from ddgs import DDGS
import logging

# Слова-тригери для активації парсера
TRIGGERS = [
    "знайди в документації", 
    "шукай в документації", 
    "як працює в", 
    "документація по"
]

def execute(cmd, jarvis):
    # 1. Очищаємо запит від тригерів
    query = cmd.lower()
    for t in TRIGGERS:
        query = query.replace(t, "")
    query = query.strip()

    if not query:
        return "Що саме мені потрібно знайти?"

    jarvis.audio.speak(f"Шукаю інформацію про {query}...")

    try:
        # 2. Пошук посилання через DuckDuckGo
        # Додаємо слово "documentation", щоб шукати саме технічні статті
        search_query = f"{query} documentation OR official tutorial"
        
        with DDGS() as ddgs:
            # Беремо перший найрелевантніший лінк
            results = list(ddgs.text(search_query, max_results=1))

        if not results:
            return "Я не знайшов релевантних статей у мережі."

        top_link = results[0]['href']
        jarvis.audio.speak("Читаю сторінку документації...")

        # 3. Завантаження сторінки
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        response = requests.get(top_link, headers=headers, timeout=10)
        response.raise_for_status()

        # 4. Парсинг (витягуємо тільки текст, без меню та скриптів)
        soup = BeautifulSoup(response.text, 'html.parser')
        for script in soup(["script", "style", "nav", "header", "footer"]):
            script.extract()
        
        # Об'єднуємо текст і беремо перші 5000 символів (щоб не перевантажити ШІ)
        text = soup.get_text(separator=' ', strip=True)[:5000]

        # 5. Аналіз через вашу локальну/хмарну нейромережу
        prompt = (
            f"Ти Senior Developer. Користувач запитує: '{query}'.\n"
            f"Ось текст із офіційної документації ({top_link}):\n"
            f"\"\"\"{text}\"\"\"\n"
            f"ЗАВДАННЯ: Дай коротку, чітку відповідь на запит користувача на основі цього тексту. "
            f"Не лий воду. Якщо в тексті є короткий фрагмент коду, який вирішує проблему - обов'язково продиктуй його. "
            f"Відповідай українською мовою."
        )

        # Викликаємо LLM тихо (щоб не засмічувати історію діалогу сирим текстом документації)
        answer = jarvis.llm.generate_response(prompt, is_system_call=True)

        if not answer:
            return "Я прочитав документацію, але нейромережа не змогла сформулювати відповідь."

        # Повертаємо фінальну вичавку, яку Джарвіс зачитає вголос
        return answer

    except Exception as e:
        logging.error(f"Помилка парсера документації: {e}")
        return "На жаль, сайт документації заблокував доступ або виникла помилка під час його читання."