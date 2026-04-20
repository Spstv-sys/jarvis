# JARVIS

Голосовий AI-асистент на Python.

## 🖥️ Запуск на Windows (GUI + мікрофон)

GUI і мікрофон на Windows завжди запускаються локально через `RUN_JARVIS.bat`.
Docker використовується лише для Ollama та ChromaDB.

```bash
# 1. Створити venv
python -m venv .venv
.venv\Scripts\activate

# 2. Встановити залежності
pip install -r requirements.txt

# 3. Налаштувати змінні середовища
copy .env.example .env

# 4. Запустити інфраструктуру в Docker
docker-compose up -d

# 5. Запустити GUI локально
RUN_JARVIS.bat
```

## 🐳 Запуск у Docker (API режим — без GUI та мікрофону)

Запускає: FastAPI сервер + ChromaDB + Ollama.

### Підготовка

```bash
copy .env.example .env
# Відкрити .env та заповнити API ключі
# Змінити OLLAMA_HOST=http://ollama:11434
```

### Запуск

```bash
docker-compose -f docker-compose.server.yml up -d
```

### Завантажити LLM модель (тільки перший раз)

```bash
docker exec -it jarvis-ollama ollama pull llama3
```

### Зупинка

```bash
docker-compose -f docker-compose.server.yml down
```

### Доступні адреси

| Сервіс | URL |
|--------|-----|
| Jarvis API | http://localhost:8001 |
| WebSocket | ws://localhost:8001/ws |
| ChromaDB | http://localhost:8000 |
| Ollama | http://localhost:11434 |
