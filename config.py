import os
from dotenv import load_dotenv

load_dotenv()


def _resolve_dir(env_name: str, default_path: str, *fallback_paths: str) -> str:
    configured_path = os.getenv(env_name)
    candidates = [configured_path, default_path, *fallback_paths]
    for path in candidates:
        if path and os.path.exists(path):
            return path
    return configured_path or default_path

# ==========================================
# 1. ШЛЯХИ ТА ДИРЕКТОРІЇ
# ==========================================
BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
DATA_DIR       = os.path.abspath(os.getenv("JARVIS_DATA_DIR", BASE_DIR))
os.makedirs(DATA_DIR, exist_ok=True)
CORE_DB_PATH   = os.path.join(DATA_DIR, "jarvis_core.db")
HEADLESS       = os.getenv("JARVIS_HEADLESS", "0").lower() in ("1", "true", "yes", "on")
SKILLS_DIR     = os.path.join(BASE_DIR, "skills")
KNOWLEDGE_DIR  = os.path.join(BASE_DIR, "knowledge_base")
SORTING_DRIVE  = _resolve_dir("JARVIS_DRIVE", "D:\\Jarvis_Sorted", "H:\\Jarvis_Sorted")
SECRET_DIR     = _resolve_dir("JARVIS_SECRET_DIR", "D:\\Sicrets", "H:\\Sicrets")
DOCUMENTS_DIR  = _resolve_dir("JARVIS_DOCUMENTS_DIR", "D:\\Documents")

# ==========================================
# 2. СИСТЕМНІ НАЛАШТУВАННЯ
# ==========================================
FUZZY_THRESHOLD = 85

VOICE_MODEL  = os.getenv("VOICE_MODEL", "uk-UA-OstapNeural")
VOICE_RATE   = os.getenv("VOICE_RATE", "+30%")
WAKE_WORDS   = ["hey_jarvis"]

# ----- Ім'я асистента -----
# Відображуване ім'я, яким асистент репрезентує себе в промптах,
# логах і TTS-виводі. Для зміни wake-word моделі openWakeWord
# потрібна окрема навчена модель (OWW_MODEL_NAME).
ASSISTANT_NAME       = os.getenv("ASSISTANT_NAME", "Нео")
# Список форм імені для Whisper-fallback розпізнавання та різання
# ведучих wake-слів з команди. "jarvis"/"джарвіс" лишено для
# зворотньої сумісності з чинною OWW-моделлю "hey_jarvis_v0.1".
ASSISTANT_NAME_FORMS = ["нео", "neo", "джарвіс", "джарвис", "jarvis"]

# ----- Параметри слухання голосових команд -----
# Усі значення в секундах. Можна перевизначити через .env.
# LISTEN_TIMEOUT          — скільки чекати ПОЧАТКУ мовлення після "Слухаю".
# LISTEN_PAUSE_THRESHOLD  — тиша всередині фрази, після якої запис вважається завершеним.
#                           Збільш, якщо Jarvis обриває на півслові, коли робиш паузу подумати.
# LISTEN_NON_SPEAKING     — мінімальна тиша на кінці, що додається до запису (м'якіше відсікання).
# LISTEN_PHRASE_LIMIT     — максимальна довжина однієї команди.
# DICTATION_PAUSE/LIMIT   — те саме для режиму "диктовка" (довгі тексти).
LISTEN_TIMEOUT          = float(os.getenv("LISTEN_TIMEOUT", "15"))
LISTEN_PAUSE_THRESHOLD  = float(os.getenv("LISTEN_PAUSE_THRESHOLD", "1.8"))
LISTEN_NON_SPEAKING     = float(os.getenv("LISTEN_NON_SPEAKING", "0.8"))
LISTEN_PHRASE_LIMIT     = int(os.getenv("LISTEN_PHRASE_LIMIT", "30"))
DICTATION_PAUSE_THRESHOLD = float(os.getenv("DICTATION_PAUSE_THRESHOLD", "2.5"))
DICTATION_PHRASE_LIMIT    = int(os.getenv("DICTATION_PHRASE_LIMIT", "240"))

# ----- Параметри обробки команд (BrainWorker pool) -----
# BRAIN_MAX_WORKERS — верхня межа одночасних команд. Захищає від спаму
# командами та DoS-подібного зростання потоків. 4 — комфортно для домашнього
# асистента: типово виконується 1-2 задачі, інші чекають у черзі пулу.
# BRAIN_COMMAND_TIMEOUT — після стількох секунд "висячий" обробник логує
# попередження (потік не вбивається — Python не вміє безпечно kill'ити thread,
# але видимість дає інженеру шанс перезапустити процес/перемкнути LLM).
BRAIN_MAX_WORKERS    = int(os.getenv("BRAIN_MAX_WORKERS", "4"))
BRAIN_COMMAND_TIMEOUT = float(os.getenv("BRAIN_COMMAND_TIMEOUT", "300"))

# ==========================================
# 3. ДОДАТКИ
# ==========================================
WOT_PATH = os.getenv("WOT_PATH", r"E:\Games\World_of_Tanks_EU\WorldOfTanks.exe")
EXCEL_PATH = os.getenv("EXCEL_PATH", "")

APPS_MAP = {
    "хром":     "chrome.exe",
    "телеграм": "Telegram.exe",
    "код":      "Code.exe",
    "танки":    "WorldOfTanks.exe",
    "дискорд":  "Discord.exe",
    "спотіфай": "Spotify.exe",
}

# ==========================================
# 4. ХАРАКТЕРИ ШІ
# ==========================================
AI_PROMPTS = {
    "стандартний": (
        f"Ти {ASSISTANT_NAME}, штучний інтелект та особистий помічник. "
        "Відповідай українською, чітко, структуровано та по суті."
    ),
    "програміст": (
        "Ти Senior Python Engineer з 10+ роками досвіду. "
        "Твій код завжди: використовує type annotations (PEP 484), "
        "має Google-style docstrings, обробляє конкретні типи виключень (НЕ bare except Exception), "
        "логує через logging (не print), дотримується SOLID, DRY та KISS принципів, "
        "валідує вхідні аргументи, уникає глобального стану. "
        "Пропонуй оптимальне рішення з поясненням трейд-офів, "
        "завжди вказуй на edge cases та потенційні проблеми з безпекою."
    ),
    "військовий": (
        "Ти тактичний ШІ. Відповідай коротко, згідно з військовими статутами. "+
        "Ніякої зайвої лірики, лише факти та розрахунки."
    ),
}

# ==========================================
# 5. МАРШРУТИ (TRIGGERS)
# ==========================================
TRIGGERS = {
    # --- Знання / навички ---
    "update_knowledge": ["онови знання", "прочитай документи", "онови базу знань", "проіндексуй файли"],
    "learn_skill":      ["створи навичку", "навчись", "напиши нову функцію"],
    "activate_skill":   ["активуй навичку", "підтверди навичку"],

    # --- Погода ---
    "weather_exact":    ["погода синоптик", "точна погода", "точно погода", "яка погода"],
    "weather":          ["погода", "температура"],

    # --- YouTube ---
    "youtube_play":     ["увімкни", "вруби", "запусти пісню", "грай", "включи"],
    "youtube_search":   ["знайди відео", "пошук ютуб", "пошук youtube",
                         "в ютубі", "в youtube", "на ютубі", "на youtube"],

    # --- Новини ---
    "news":             ["свіжі новини", "новини укрінформ", "останні новини"],

    # --- Фінанси ---
    "finance":          ["курс", "валют", "долар", "євро"],

    # --- ШІ ---
    "switch_ai":        ["перемкни", "використовуй", "активуй", "активний", "провайдер", "зміни ші"],
    "personality":      ["режим військовий", "режим програміст", "стандартний режим"],
    "check_ai":         ["який двигун", "який ші", "на чому працюєш", "статус інтелекту"],

    # --- Система ---
    "timer":            ["таймер", "нагадай"],
    "hardware":         ["гучніше", "тихіше", "вимкни звук", "яскравість"],
    "apps_os":          ["відкрий", "закрий", "час кодити", "диск",
                         "відкрити ексел", "відкрити ексель", "відкрити excel",
                         "запусти ексел", "запусти ексель", "запусти excel",
                         "запустити ексел", "запустити ексель", "запустити excel"],
    "vision":           ["що на екрані", "проаналізуй монітор", "що ти бачиш"],
    "code_assistant":   ["проаналізуй файл", "поясни код", "виправ код"],
    "calendar":         ["додай задачу", "заплануй", "розклад", "які плани"],
    "dictation":        ["режим диктування", "запиши мої думки"],

    # --- Пам'ять, пошук, файли (дублікати ВИДАЛЕНО — FIX #2) ---
    "memorize":         ["запам'ятай", "запамятай"],
    "web_search":       ["знайди в інтернеті", "знайди в мережі", "пошукай в інтернеті",
                         "пошукай в мережі", "в інтернеті", "в мережі", "загугли",
                         "що таке", "хто такий", "розкажи про",
                         "що ти скажеш про", "знайди інформацію"],
    "find_file":        ["знайди файл"],

    # --- Вихід ---
    "system_exit":      [
        "вимкни комп", "завершити роботу", "стоп система", "вимкнись",
        "закрий програму", "закрий застосунок", "закрий додаток",
        "вимкни нео", "вимкни себе",
        "заверши програму", "заверши застосунок",
        "вийти з програми", "вихід з програми",
        "стоп нео", "нео стоп", "нео вимкнись", "нео заверши роботу",
    ],
    "stop_voice":       ["стоп", "замовкни", "зупинись", "тиша", "досить"],
}

# ==========================================
# 6. ЛОКАЛІЗАЦІЯ / МІСТА
# ==========================================
DEFAULT_CITY    = os.getenv("DEFAULT_CITY",    "суми")
DEFAULT_CITY_EN = os.getenv("DEFAULT_CITY_EN", "Sumy")

# ==========================================
# 6.1. ОФЛАЙН ВІКІПЕДІЯ (kiwix-serve)
# ==========================================
# Запускати: kiwix-serve.exe --port 8080 wikipedia_uk_all_nopic.zim
# ZIM-файли: https://library.kiwix.org/  (шукати "Ukrainian Wikipedia")
WIKI_OFFLINE_URL  = os.getenv("WIKI_OFFLINE_URL",  "")  # напр. http://localhost:8080
WIKI_OFFLINE_BOOK = os.getenv("WIKI_OFFLINE_BOOK", "")  # напр. wikipedia_uk_all_nopic

# ==========================================
# 6.2. RAG / ПАМ'ЯТЬ (ембединги, література)
# ==========================================
# Модель ембедингів. Кращі для української:
#   - "intfloat/multilingual-e5-base"           (~440 MB, рекомендовано)
#   - "intfloat/multilingual-e5-large"          (~1.3 GB, найвища якість)
#   - "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"
#   - "sentence-transformers/all-MiniLM-L6-v2"  (стара, англомовна)
EMBEDDING_MODEL = os.getenv(
    "EMBEDDING_MODEL",
    "intfloat/multilingual-e5-base",
)

# Папка з художньою/науковою літературою для масової індексації
LITERATURE_DIR = _resolve_dir(
    "JARVIS_LITERATURE_DIR",
    os.path.join(BASE_DIR, "literature"),
)

# Параметри чанкування для прози (більші, ніж для довідки)
LITERATURE_CHUNK_SIZE    = int(os.getenv("LITERATURE_CHUNK_SIZE",    "1500"))
LITERATURE_CHUNK_OVERLAP = int(os.getenv("LITERATURE_CHUNK_OVERLAP", "300"))

# ==========================================
# 6.3. KNOWLEDGE GRAPH (spaCy + SQLite)
# ==========================================
# Шлях до SQLite-файлу графа сутностей (персонажі, місця, події).
KG_DB_PATH = os.getenv(
    "KG_DB_PATH",
    os.path.join(DATA_DIR, "jarvis_kg", "graph.sqlite"),
)
# Чи будувати граф під час ingest книг (вимкнено за замовчуванням —
# spaCy-модель ~500 МБ, потребує `python -m spacy download uk_core_news_md`).
KG_ENABLED = os.getenv("KG_ENABLED", "0") in ("1", "true", "True")

# ==========================================
# 6.4. INTENT ROUTER (LLM-fallback для нечітких команд)
# ==========================================
# Якщо звичайні тригери не зматчили, маршрутизатор спитає у LLM, який маршрут
# найбільше пасує. Результат кешується у SQLite.
INTENT_ROUTER_ENABLED = os.getenv("INTENT_ROUTER_ENABLED", "1") in ("1", "true", "True")
INTENT_ROUTER_LLM     = os.getenv("INTENT_ROUTER_LLM",     "1") in ("1", "true", "True")
INTENT_ROUTER_DB      = os.getenv(
    "INTENT_ROUTER_DB",
    os.path.join(DATA_DIR, "jarvis_intent_cache.sqlite"),
)
INTENT_ROUTER_MIN_CONF = float(os.getenv("INTENT_ROUTER_MIN_CONF", "0.55"))

# ==========================================
# 6.5. EPISODIC MEMORY (Шар 5 — довготривалий журнал подій)
# ==========================================
# Зберігає кожну виконану команду + ключові події (плани, факти, помилки)
# у SQLite з ембедингами для семантичного пошуку («коли я востаннє...»,
# «що я робив минулого тижня»).
EPISODIC_MEMORY_ENABLED = os.getenv("EPISODIC_MEMORY_ENABLED", "1") in ("1", "true", "True")
EPISODIC_DB_PATH        = os.getenv(
    "EPISODIC_DB_PATH",
    os.path.join(DATA_DIR, "jarvis_episodic.sqlite"),
)
EPISODIC_RETENTION_DAYS = int(os.getenv("EPISODIC_RETENTION_DAYS", "180"))

# ==========================================
# 6.6. THOUGHT LOG (Шар 6 — потік свідомості)
# ==========================================
# Кільцевий буфер думок (intent/plan/step/critic/episodic/...) + опційна
# персистенція в SQLite, щоб дивитись історію після рестарту.
THOUGHT_LOG_PERSIST        = os.getenv("THOUGHT_LOG_PERSIST", "1") in ("1", "true", "True")
THOUGHT_LOG_DB_PATH        = os.getenv(
    "THOUGHT_LOG_DB_PATH",
    os.path.join(DATA_DIR, "jarvis_thoughts.sqlite"),
)
THOUGHT_LOG_MAXLEN         = int(os.getenv("THOUGHT_LOG_MAXLEN", "200"))
THOUGHT_LOG_PRELOAD        = int(os.getenv("THOUGHT_LOG_PRELOAD", "100"))
THOUGHT_LOG_RETENTION_DAYS = int(os.getenv("THOUGHT_LOG_RETENTION_DAYS", "30"))

# ==========================================
# 6.7. REFLECTOR (Шар 7 — метакогніція / уроки з досвіду)
# ==========================================
# Періодично аналізує GoalStack history + Episodic events і формує "уроки",
# які Planner підтягує у промпт перед плануванням.
REFLECTOR_ENABLED         = os.getenv("REFLECTOR_ENABLED", "1") in ("1", "true", "True")
REFLECTOR_INTERVAL_SEC    = int(os.getenv("REFLECTOR_INTERVAL_SEC", "0"))   # 0 = лише вручну
REFLECTOR_WINDOW_HOURS    = int(os.getenv("REFLECTOR_WINDOW_HOURS", "24"))
REFLECTOR_MIN_EVENTS      = int(os.getenv("REFLECTOR_MIN_EVENTS", "5"))
REFLECTOR_USE_LLM         = os.getenv("REFLECTOR_USE_LLM", "0") in ("1", "true", "True")
REFLECTOR_MAX_LESSONS     = int(os.getenv("REFLECTOR_MAX_LESSONS", "5"))
REFLECTOR_LESSON_TTL_DAYS = int(os.getenv("REFLECTOR_LESSON_TTL_DAYS", "60"))

# ==========================================
# 6.8. ANTICIPATOR (Шар 8 — проактивні пропозиції)
# ==========================================
# Без запиту користувача формує підказки на основі рутин, провалених цілей,
# часових патернів та системних метрик. UI показує їх як банер; користувач
# приймає або відкидає.
ANTICIPATOR_ENABLED          = os.getenv("ANTICIPATOR_ENABLED", "1") in ("1", "true", "True")
ANTICIPATOR_INTERVAL_SEC     = int(os.getenv("ANTICIPATOR_INTERVAL_SEC", "0"))   # 0 = manual
ANTICIPATOR_MAX_PENDING      = int(os.getenv("ANTICIPATOR_MAX_PENDING", "5"))
ANTICIPATOR_EXPIRE_SEC       = int(os.getenv("ANTICIPATOR_EXPIRE_SEC", "1800"))
ANTICIPATOR_COOLDOWN_SEC     = int(os.getenv("ANTICIPATOR_COOLDOWN_SEC", "600"))
ANTICIPATOR_BATTERY_LOW_PCT  = int(os.getenv("ANTICIPATOR_BATTERY_LOW_PCT", "20"))
ANTICIPATOR_TIME_WINDOW_MIN  = int(os.getenv("ANTICIPATOR_TIME_WINDOW_MIN", "30"))
ANTICIPATOR_TIME_MIN_REPEATS = int(os.getenv("ANTICIPATOR_TIME_MIN_REPEATS", "3"))

# ==========================================
# 7. СТОП-СЛОВА
# ==========================================
STOP_WORDS_YOUTUBE_PLAY = [
    "увімкни", "вруби", "запусти пісню", "запусти відео",
    "грай", "включи", "на ютубі", "на youtube",
    "в ютубі", "в youtube", "джарвіс", "нео",
]

STOP_WORDS_YOUTUBE_SEARCH = [
    "знайди", "пошук", "що є на ютубі про",
    "на ютубі", "на youtube", "в ютубі", "в youtube",
    "в ютуб", "ютуб", "youtube", "відео", "про", "джарвіс", "нео",
]

STOP_WORDS_LEARN_SKILL = ["створи навичку", "навчись", "напиши нову функцію", "щоб ти", "для"]

# ==========================================
# 8. РОЗШИРЕННЯ ФАЙЛІВ
# ==========================================
VOICE_EXTENSION_MAP = {
    " крапка ": ".",
    " точка ":  ".",
    " пай":     ".py",
    " джес":    ".js",
    " текст":   ".txt",
}

VOICE_NAME_MAP = {
    "тест":   "test",
    "мейн":   "main",
    "скрипт": "script",
    "індекс": "index",
}

# ==========================================
# 9. СЕРВЕР
# ==========================================
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173").split(",")

# ==========================================
# 10. WAKE WORD DETECTION
# ==========================================
OWW_SENSITIVITY        = float(os.getenv("OWW_SENSITIVITY", "0.35"))   # застаріло; використовуйте OWW_WAKE_THRESHOLD
OWW_MODEL_NAME         = os.getenv("OWW_MODEL_NAME", "hey_jarvis_v0.1") # назва моделі OWW
OWW_VAD_THRESHOLD      = float(os.getenv("OWW_VAD_THRESHOLD", "0.5"))   # Silero VAD поріг (0 = вимкнено)
OWW_WAKE_THRESHOLD     = float(os.getenv("OWW_WAKE_THRESHOLD", "0.5"))  # поріг спрацювання wake word
OWW_DEBOUNCE_TIME      = float(os.getenv("OWW_DEBOUNCE_TIME", "2.0"))   # секунди між спрацюваннями
OWW_FALLBACK_TO_WHISPER = os.getenv("OWW_FALLBACK_TO_WHISPER", "true").lower() == "true"  # fallback на Whisper якщо OWW недоступний

# ==========================================
# 11. FUZZY-МАТЧ КОМАНД (STT-стійкість)
# ==========================================
# Однослівні тригери базовий роутер матчить лише точно, тож дрібні
# STT-помилки («погода»→«пагода», «тихіше»→«тихише») зривають маршрут і
# команда падає в LLM-чат. Цей блок вмикає fuzzy-фолбек САМЕ для однослівних
# тригерів (багатослівні вже мають власний fuzzy у command_router).
# Свідомо консервативно, щоб не плодити хибних спрацювань:
FUZZY_WORD_THRESHOLD     = float(os.getenv("FUZZY_WORD_THRESHOLD", "82"))    # 0..100; нижче = толерантніше
FUZZY_WORD_MIN_LEN       = int(os.getenv("FUZZY_WORD_MIN_LEN", "5"))         # короткі ризиковані слова (стоп/курс/диск) лишаємо точними
FUZZY_WORD_MAX_CMD_WORDS = int(os.getenv("FUZZY_WORD_MAX_CMD_WORDS", "4"))   # fuzzy лише для коротких команд
FUZZY_WORD_LONG_LEN      = int(os.getenv("FUZZY_WORD_LONG_LEN", "8"))      # дуже характерні тригери (>=8) fuzzy-матчаться навіть у довгих командах

# ASR bias prompt — підштовхує Whisper до доменного словника команд (core/asr_bias.py)
ASR_BIAS_ENABLED         = os.getenv("ASR_BIAS_ENABLED", "1") in ("1", "true", "True")

# ==========================================
# 12. VOICE LOG (Фаза 6 — замкнений цикл навчання)
# ==========================================
# Журнал живих голосових команд (аудіо + розпізнаний текст) для періодичного
# перегляду помилок і доливання виправлених кліпів у train.tsv.
VOICE_LOG_ENABLED = os.getenv("VOICE_LOG_ENABLED", "1") in ("1", "true", "True")
VOICE_LOG_DIR     = os.getenv("VOICE_LOG_DIR", "voice_logs")
VOICE_LOG_MAX     = int(os.getenv("VOICE_LOG_MAX", "1000"))   # макс. wav-файлів (0 = без ліміту)
