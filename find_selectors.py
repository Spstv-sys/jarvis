import requests
import urllib3
from bs4 import BeautifulSoup
from urllib.parse import quote

urllib3.disable_warnings()

url = "https://sinoptik.ua/" + quote("погода-суми", safe='')
response = requests.get(url, verify=False, headers={
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
})
response.encoding = 'utf-8'
soup = BeautifulSoup(response.text, 'html.parser')

# Шукаємо всі div/p з текстом про погоду
print("=== КАНДИДАТИ НА ОПИС ===")
for tag in soup.find_all(['div', 'p', 'span']):
    text = tag.get_text(strip=True)
    # Шукаємо елементи з описом погоди (хмарно, сонячно тощо)
    if any(w in text.lower() for w in ['хмар', 'сонч', 'дощ', 'сніг', 'ясн', 'туман']):
        if len(text) < 100:
            print(f"<{tag.name} class='{tag.get('class')}'> → {text}")