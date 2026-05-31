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
docker exec -it jarvis-ollama ollama pull qwen2.5:3b
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
| Ollama | http://localhost:11534 |

---

## 🎙️ Власна голосова модель (wake-word + STT)

Хочете, щоб Jarvis розпізнавав **ваш** голос точніше?
Повний гайд як натренувати власне wake-word ("Neo") + fine-tune Whisper на українську:

➡️ **[docs/TRAINING_VOICE_MODEL.md](docs/TRAINING_VOICE_MODEL.md)**

Коротко:

```powershell
# 1. Wake-word "Neo" (openWakeWord, ONNX)
cd training\neo_wakeword
python -m venv .venv-train; .venv-train\Scripts\activate
pip install -r requirements-train.txt
python train_neo.py
copy output\neo.onnx ..\..\.venv\Lib\site-packages\openwakeword\resources\models\

# 2. Whisper STT (LoRA fine-tune на власному голосі)
.venv\Scripts\activate
python record_dataset.py        # записати 300+ семплів
python train_whisper.py         # ~500 кроків
python test_model_live.py       # перевірити
```

Деталі, CLI-параметри, поради по датасету та ітеративному покращенню — у [docs/TRAINING_VOICE_MODEL.md](docs/TRAINING_VOICE_MODEL.md).

## 🧠 Проактивні шари (опційно)

Когнітивні шари **Reflector** (метакогніція: уроки з досвіду) та **Anticipator**
(проактивні пропозиції) увімкнені (`*_ENABLED=1`) і отримують дані під час
виконання планів. Проте їхній **автономний фоновий цикл за замовчуванням
вимкнено** (`*_INTERVAL_SEC=0` = лише вручну / за тригером) — щоб Нео не діяв
несподівано «з коробки» і не витрачав ресурси на періодичне сканування.

Щоб увімкнути автономну проактивність, задайте інтервали (секунди) у `.env`:

```bash
REFLECTOR_INTERVAL_SEC=3600     # рефлексія раз на годину
ANTICIPATOR_INTERVAL_SEC=900    # пропозиції раз на 15 хв
```

Ручний запуск без фонового циклу — через API: `POST /reflection/run`
та `POST /anticipator/scan`. Knowledge Graph вимкнено за замовчуванням
(`KG_ENABLED=0`); увімкнення потребує spaCy-моделі (`pip install -e ".[kg]"`,
далі `python -m spacy download uk_core_news_md`).
