# 🎙️ Створення власної голосової моделі для J.A.R.V.I.S.

Цей документ описує **повний цикл** навчання власних голосових моделей для голосового вводу:

1. **Wake-word модель** (`Neo`) — реагує на слово-активатор (openWakeWord, ONNX).
2. **Whisper STT-модель** (українська, fine-tuned) — розпізнає вашу мову після активації.

---

## 📋 Загальна архітектура голосового пайплайну

```
🎤 Мікрофон
   │
   ▼
[Wake-word детектор]  ──►  чує "Neo" / "Hey Neo"
   │
   ▼
[Whisper STT]  ──►  транскрибує українську мову у текст
   │
   ▼
[LLM + Skills]  ──►  виконує команду
```

Обидві моделі тренуються **окремо** і **незалежно**.

---

# Частина 1. Власне Wake-Word ("Neo")

Тренування wake-word через [openWakeWord](https://github.com/dscripka/openWakeWord).
Усі скрипти лежать у [training/neo_wakeword/](../training/neo_wakeword/).

## 1.1. Підготовка середовища

> ⚠️ **Окремий venv!** Не змішуйте з основним venv Jarvis — версії torch/onnx конфліктують.

```powershell
cd training\neo_wakeword

# Окремий venv для тренування
python -m venv .venv-train
.venv-train\Scripts\activate

# PyTorch з CUDA (для NVIDIA GPU)
pip install torch --index-url https://download.pytorch.org/whl/cu121

# Решта залежностей
pip install -r requirements-train.txt
```

## 1.2. (Опційно) Запис власних позитивних семплів

Скрипт [training/neo_wakeword/record_neo.py](../training/neo_wakeword/record_neo.py)
дозволяє записати власні вимови слова "Neo" у різних інтонаціях.
Це покращує точність для **вашого** голосу.

```powershell
python record_neo.py
# → запише .wav у training_data\positive\
```

Рекомендації:
- 50–200 записів власного голосу
- різні інтонації (запитальна, наказова, тиха, голосна)
- різні відстані від мікрофона (0.3–2 м)
- різні фонові шуми (тиша, музика, телевізор)

## 1.3. Тренування

```powershell
# ~30–90 хв на GPU (RTX 30xx+)
python train_neo.py
```

Параметри в [train_neo.py](../training/neo_wakeword/train_neo.py):

| Параметр          | Опис                                                            |
| ----------------- | --------------------------------------------------------------- |
| `TARGET_PHRASE`   | "Neo" (за замовчуванням) або "Hey Neo" (якщо багато false-trigger) |
| `N_SAMPLES`       | Кількість синтетичних позитивних прикладів (TTS)                |
| `BATCH_SIZE`      | 64–256 залежно від VRAM                                         |
| `EPOCHS`          | 50–200                                                          |

Результат: `output\neo.onnx`.

## 1.4. Розгортання моделі

Скопіюйте `.onnx` у openWakeWord:

```powershell
copy output\neo.onnx ..\..\.venv\Lib\site-packages\openwakeword\resources\models\
```

Активуйте у [config.py](../config.py) (рядок ~154) або в `.env`:

```python
OWW_MODEL_NAME    = "neo"
OWW_VAD_THRESHOLD = 0.5    # 0.4 = чутливіше, 0.7 = строгіше
```

## 1.5. Без GPU? — Google Colab (безкоштовний T4)

1. Відкрийте офіційний ноутбук:
   [openWakeWord automatic_model_training_simple.ipynb](https://colab.research.google.com/github/dscripka/openWakeWord/blob/main/notebooks/automatic_model_training_simple.ipynb)
2. У першій клітинці: `target_word = "Neo"`
3. Runtime → Change runtime type → **T4 GPU**
4. Runtime → Run all (~45–60 хв)
5. Завантажте `neo.onnx` і покладіть у
   `.venv\Lib\site-packages\openwakeword\resources\models\`

## 1.6. Дебаг

```powershell
python debug_neo_live.py
# Промовляйте "Neo" — побачите score у реальному часі
```

---

# Частина 2. Власна Whisper STT-модель (українська)

Fine-tune [`openai/whisper-small`](https://huggingface.co/openai/whisper-small)
методом **LoRA** на вашому голосі. Працює на **CPU** (повільно) або **GPU** (швидко).

Скрипти:
- [record_dataset.py](../record_dataset.py) — запис семплів
- [train_whisper.py](../train_whisper.py) — fine-tune
- [test_model_live.py](../test_model_live.py) — перевірка вживу
- [reclean_dataset.py](../reclean_dataset.py) — очистка датасету

## 2.1. Підготовка фраз

Створіть/відредагуйте [my-dataset/phrases_to_record.txt](../my-dataset/phrases_to_record.txt):
по одній фразі в рядок, які ви хочете говорити Jarvis.

```
відкрий браузер
що по новинах
скільки часу
запусти музику
...
```

Чим **різноманітніше** і чим більше фраз — тим краще (рекомендовано **300+**).

## 2.2. Запис голосових семплів

```powershell
# Активуйте основний venv (не -train!)
.venv\Scripts\activate

# Інтерактивний запис з VAD-автозупинкою
python record_dataset.py

# Або з фіксованою тривалістю
python record_dataset.py --duration 4
```

Команди в інтерактивному режимі:

| Ввід           | Дія                                            |
| -------------- | ---------------------------------------------- |
| `<текст>` ⏎    | Записати новий семпл з цим текстом             |
| `r` ⏎          | Перезаписати **останній** семпл (той же текст) |
| `s` / порожньо | Пропустити                                     |
| `exit` / Ctrl+C | Вийти                                          |

Результат:
- `my-dataset/audio/audio_0001.wav`, `audio_0002.wav`, …
- `my-dataset/train.tsv` — `path<TAB>text`

> 💡 `train.tsv` в **append-режимі** — старі семпли не затираються.

## 2.3. Автоматичне очищення/перевірка датасету

```powershell
# Перевірити, що всі .wav відповідають запису в train.tsv
python check_paths.py

# Перетранскрибувати старі записи новою моделлю (для звірки)
python autolabel_dataset.py

# Видалити биті/занадто короткі записи
python reclean_dataset.py
```

## 2.4. Тренування Whisper LoRA

```powershell
# Тренування з нуля (~500 кроків)
python train_whisper.py --max-steps 500 --lr 1e-3

# Продовжити з останнього checkpoint
python train_whisper.py --resume --extra-steps 200
```

Параметри CLI:

| Прапор              | За замовчуванням | Опис                                               |
| ------------------- | ---------------- | -------------------------------------------------- |
| `--max-steps N`     | 500              | Максимум кроків тренування                         |
| `--resume`          | off              | Продовжити з останнього `checkpoint-*` в `OUTPUT_DIR` |
| `--extra-steps N`   | 200              | При `--resume`: на скільки кроків підняти ліміт   |
| `--lr FLOAT`        | 1e-3             | Learning rate                                      |

Виходи:
- `whisper-small-uk-jarvis/checkpoint-XXXX/` — проміжні чекпоінти LoRA
- `whisper-final-model/` — фінальна модель (LoRA-адаптер)

> ⏱️ Орієнтовний час:
> - **GPU (RTX 3060+)**: 15–40 хв на 500 кроків
> - **CPU (i7+)**: кілька годин

## 2.5. Перевірка моделі вживу

```powershell
python test_model_live.py
# Скажіть фразу — побачите транскрипцію
```

Або тест без мікрофона:

```powershell
python quick_test_model.py
python test_whisper_pipeline.py
```

## 2.6. Підключення моделі до Jarvis

У [config.py](../config.py) знайдіть налаштування Whisper і вкажіть шлях:

```python
WHISPER_MODEL_PATH = "./whisper-final-model"   # або шлях до конкретного checkpoint
WHISPER_LANGUAGE   = "ukrainian"
WHISPER_TASK       = "transcribe"
```

Перезапустіть Jarvis:

```powershell
RUN_JARVIS.bat
```

## 2.7. Ітеративне покращення

1. Запускаємо Jarvis, користуємось.
2. Помічаємо слова, які розпізнаються погано.
3. `python record_dataset.py` → дописуємо проблемні фрази (10–30 шт).
4. `python train_whisper.py --resume --extra-steps 150`
5. Перевіряємо знову.

---

# 🚫 Важливо про git

Великі артефакти **навмисно ігноруються** в [.gitignore](../.gitignore) і **НЕ заливаються** в репозиторій:

```
whisper-final-model/
whisper-small-uk-jarvis/
training/neo_wakeword/.venv-train/
training/neo_wakeword/training_data/
training/neo_wakeword/output/
my-dataset/
live_test_recordings/
```

Це правильно: моделі важать **сотні МБ – одиниці ГБ**.
Якщо потрібно ділитися моделлю — використовуйте:
- [Hugging Face Hub](https://huggingface.co/) (для моделей)
- Git LFS (для невеликих бінарних артефактів)
- Звичайний хмарний диск (OneDrive / Google Drive) для датасетів

---

# 🛠️ Швидкий чек-лист

- [ ] Записано 300+ семплів у `my-dataset/`
- [ ] `python check_paths.py` — без помилок
- [ ] `python train_whisper.py --max-steps 500`
- [ ] `python test_model_live.py` — точність задовільна
- [ ] У `config.py` вказано `WHISPER_MODEL_PATH`
- [ ] (опційно) Натреновано `neo.onnx` і скопійовано в openWakeWord
- [ ] У `.env`/`config.py`: `OWW_MODEL_NAME=neo`
- [ ] `RUN_JARVIS.bat` — голосовий ввід працює 🎉
