# JARVIS

Голосовий AI-асистент на Python.

## 🖥️ Запуск на Windows (повний режим з GUI та мікрофоном)

```bash
# 1. Створити venv
python -m venv .venv
.venv\Scripts\activate

# 2. Встановити залежності
pip install -r requirements.txt

# 3. Налаштувати змінні середовища
copy .env.example .env

# 4. Запустити
RUN_JARVIS.bat
```

## 🐳 Запуск у Docker (API режим — без GUI та мікрофону)

Запускає: FastAPI сервер + ChromaDB + Ollama.

### Підготовка

```bash
copy .env.example .env
# Відкрити .env та заповнити API ключі
```

### Запуск

```bash
docker-compose up -d
```

### Завантажити LLM модель (тільки перший раз)

```bash
docker exec -it jarvis-ollama ollama pull llama3
```

### Зупинка

```bash
docker-compose down
```

### Доступні адреси

| Сервіс | URL |
|--------|-----|
| Jarvis API | http://localhost:8001 |
| WebSocket | ws://localhost:8001/ws |
| ChromaDB | http://localhost:8000 |
| Ollama | http://localhost:11434 |
