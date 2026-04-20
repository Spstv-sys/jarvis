# JARVIS

## 🐳 Docker (API режим)

Запускає лише серверну частину: FastAPI + ChromaDB + Ollama.
GUI, мікрофон та аудіо — запускаються окремо на хості.

### Підготовка
1. Скопіюй `.env.example` → `.env` та заповни API ключі
2. `TESSERACT_PATH` в Docker вже налаштовано автоматично (`/usr/bin/tesseract`)

### Запуск
```bash
docker-compose up -d
```

### Завантажити LLM модель (після першого запуску)
```bash
docker exec -it jarvis-ollama ollama pull llama3
```

### Зупинка
```bash
docker-compose down
```

### API доступний на
- REST API: http://localhost:8001
- WebSocket: ws://localhost:8001/ws
- ChromaDB: http://localhost:8000
- Ollama: http://localhost:11434
