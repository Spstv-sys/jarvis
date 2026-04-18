import requests
from bs4 import BeautifulSoup

response = requests.get(
    "https://www.ukrinform.ua/",
    headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'},
    timeout=10
)
response.encoding = 'utf-8'
soup = BeautifulSoup(response.text, 'html.parser')

print("=== УСІ ПОСИЛАННЯ З ТЕКСТОМ 30-200 СИМВОЛІВ ===")
seen = set()
count = 0
for tag in soup.find_all('a', href=True):
    text = tag.get_text(strip=True)
    cls  = tag.get('class')
    href = tag.get('href', '')
    if 30 <= len(text) <= 200 and text not in seen:
        seen.add(text)
        print(f"class='{cls}' href='{href[:50]}' → {text[:80]}")
        count += 1
    if count >= 20:
        break