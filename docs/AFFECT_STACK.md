# Афективно-проактивний стек Jarvis

Документ описує 5 нових модулів (травень 2026), що додають Jarvis емоційну
осведомленість, проактивну делікатність, рефлексію над користувачем і
безпечне виконання довільного коду.

## Огляд

```
голос ──┐         ┌─► Affect.observe_audio ─┐
        │         │                          │
WAV ───►│ Audio   │                          ▼
        │ features│                  ┌──────────────┐
text ──►│         ├─► Affect.observe_│  Affect      │──► system prompt LLM
        │         │   text           │  (state EMA) │       АФЕКТ: ...
        │         │                  └──────┬───────┘
        └─► Whisper                         │
                                            ├──► Anticipator._affect_gate()
                                            │       └─ stress/anger ⇒ silent
                                            │       └─ tired ⇒ priority↓
                                            │
                                            └──► Reflector._detect_user_traits()
                                                    └─ SCOPE_USER_TRAIT
                                                        └─ Planner: ХАРАКТЕР КОРИСТУВАЧА

skill_finder_run / skill_smart_run ──► core.sandbox.run_python ──► Docker | subprocess(-I)
```

---

## 1. `core/affect.py` — модель афективного стану

3-вісна модель емоції користувача з EMA-згладжуванням і decay:

| Вісь    | Діапазон | Сенс                                          |
|---------|----------|-----------------------------------------------|
| valence | -1..+1   | негатив ↔ позитив                             |
| arousal | 0..1     | спокій ↔ збудження                            |
| stress  | 0..1     | тиск/тривога                                   |

**API:**
```python
from core.affect import get_affect
af = get_affect()
af.observe_text("я ВТОМИВСЯ")           # лексикон або NLP-scorer
af.observe_audio({"jitter":0.4, ...})   # з audio_features
af.manual(valence=-0.3, stress=0.7)     # ручне втручання
st = af.snapshot()                      # {valence, arousal, stress, label, samples}
af.as_prompt_block()                    # рядок для системного промпта
af.set_text_scorer(fn)                  # підмінити скорер (NLP)
af.subscribe(cb); af.reset()
```

**Лейбли:** `спокійний / нейтральний / напружений / роздратований / втомлений / збуджений / позитивний`

Стан інжектується у `core/llm_handler.py` як секція `АФЕКТ:` між `ПРОФІЛЬ` і `WORKING MEMORY`.

---

## 2. `core/audio_features.py` — голосовий канал афекту

Без зовнішніх залежностей (numpy + stdlib `wave`). Витягує:

- `energy` — нормалізована RMS енергія
- `speech_rate` — частка voiced-фреймів × ZCR
- `jitter` — стрибки RMS між фреймами (нервовість)
- `pitch_var` — варіативність авто-кореляційної оцінки висоти
- `voiced_ratio` — % фреймів понад поріг (0.012 RMS)
- `duration_sec`

**Інтеграція:** `core/audio_manager.py::listen_for_command()` після
`audio.get_wav_data()` викликає `extract_features_from_bytes()` → `Affect.observe_audio()`.
Вимикається через `AFFECT_AUDIO=0`.

---

## 3. `core/affect_nlp.py` — XLM-RoBERTa як real text scorer

Лексика — швидкий fallback, але обмежена. Реальний скорер:
`cardiffnlp/twitter-xlm-roberta-base-sentiment` (мультимовний, ~280 MB,
підтримує українську).

**Лінива асинхронна загрузка:** `enable_nlp_scorer()` повертає миттєво,
модель тягнеться у фоновому потоці. Доки не готова — `Affect`
використовує лексикон. Кеш на 256 текстів.

```python
from core.affect_nlp import enable_nlp_scorer
enable_nlp_scorer()   # викликається в main.py після UserProfile init
```

**Env:** `AFFECT_NLP=0` (вимкнути), `AFFECT_NLP_MODEL=...`, `AFFECT_NLP_DEVICE=cpu|auto`.

Маппінг: `valence = p_pos − p_neg`, `arousal = 1 − p_neu`, `stress = p_neg^1.4`.

---

## 4. `core/anticipator.py` — affect-gate проактивності

Перед генерацією/показом пропозицій викликається `_affect_gate()`:

| Афективна умова                          | Дія                              |
|------------------------------------------|----------------------------------|
| `stress ≥ 0.6`                           | блокувати все, крім URGENT      |
| `valence ≤ −0.45 AND arousal ≥ 0.5`      | блокувати (гнів)                 |
| `valence ≤ −0.35`                        | пропустити, але `priority − 1`  |
| інакше                                    | пропустити без змін              |

URGENT-пропозиції (`PRIORITY_URGENT=4` — батарея, помилки системи)
**ніколи** не блокуються — критичне попередження важливіше за делікатність.

**Конфіг (env):**
```
ANTICIPATOR_AFFECT_ENABLED=1
ANTICIPATOR_AFFECT_STRESS_MUTE=0.6
ANTICIPATOR_AFFECT_ANGER_MUTE=0.45
ANTICIPATOR_AFFECT_TIRED_DEMOTE=-0.35
```

Усі рішення фіксуються у `ThoughtLog` зі `scope="anticipator"`.

---

## 5. `core/reflector.py` — `SCOPE_USER_TRAIT` уроки

Новий тип уроку — **про користувача**, не про власну поведінку.

`_detect_user_traits(episodes)` генерує до 4 уроків:

1. **`active_period`** — коли користувач активний (ранок/день/вечір/ніч),
   якщо ≥55% запитів припадає на одне вікно і ≥4 запити.
2. **`terse`** / **`verbose`** — стиль формулювання за середньою кількістю слів
   (≤3 = `terse`, ≥12 = `verbose`).
3. **`themes`** — топ-3 повторюваних тематичних слова (≥3 показів,
   не стоп-слова, довжина ≥3).
4. **`baseline_stress` / `baseline_positive` / `baseline_low`** — поточний
   афективний фон з `Affect.state()` за ≥8 сэмплами.

**Інтеграція в Planner:** `core/planner.py` ділить уроки на дві секції:
```
ХАРАКТЕР КОРИСТУВАЧА:    ← всі SCOPE_USER_TRAIT, незалежно від теми
УРОКИ З ДОСВІДУ:         ← решта, релевантна цілі
```

---

## 6. `core/sandbox.py` — ізольоване виконання Python

Захист для довільних `.py` (з `Code/`, через `skill_finder_run`/`skill_smart_run`).

**API:**
```python
from core.sandbox import run_python, status
res = run_python("/path/script.py", args=["x"], timeout=15)
# res.{ok, returncode, stdout, stderr, engine, duration_sec, timed_out}
status()  # {mode, engine, docker_present, image, memory_limit, ...}
```

**Docker-ізоляція** (якщо встановлений):
- `--network=none` — без мережі
- `--read-only` + `--tmpfs /tmp:size=64m`
- `--memory=256m --cpus=1.0 --pids-limit=128`
- `--user 65534:65534` (nobody) + `--cap-drop ALL` + `no-new-privileges`
- скрипт mount-иться `:ro` у `/sandbox/run.py`

**Fallback:** `subprocess.run([python, '-I', script])` з timeout
(прапор `-I` ізолює від `PYTHONPATH` і site-packages користувача).

**Конфіг (env / config.py):**
```
JARVIS_SANDBOX=auto|docker|subprocess|disabled  (default auto)
JARVIS_SANDBOX_IMAGE=python:3.11-slim
JARVIS_SANDBOX_MEM=256m
JARVIS_SANDBOX_CPUS=1.0
JARVIS_SANDBOX_TIMEOUT=15
```

---

## Тести

`tests/test_affect_stack.py` (19 тестів):

| Група                        | Покриття                                            |
|------------------------------|-----------------------------------------------------|
| `TestAffectText`             | початковий стан, лексикон pos/neg, prompt block     |
| `TestAudioFeatures`          | calm vs nervous WAV, observe_audio інтеграція       |
| `TestAnticipatorAffectGate`  | усі 4 гілки + URGENT-пас                            |
| `TestReflectorUserTraits`    | active_period+terse, baseline_low з афекту          |
| `TestSandbox`                | status, ok, error, timeout-kill, missing file       |
| `TestEndToEndStressLoop`     | стрес → промпт + Anticipator мовчить                |

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_affect_stack.py -v
```

---

## Послідовність ініціалізації (`main.py`)

1. `UserProfile` створюється
2. `enable_nlp_scorer()` — кидає завантаження XLM-RoBERTa у фон
3. У `execute_command(cmd)`: `get_affect().observe_text(cmd)` — **завжди**
4. `audio_manager.listen_for_command()`: спостереження аудіо за `AFFECT_AUDIO=1`
5. `Reflector` (фоновий цикл) періодично додає `SCOPE_USER_TRAIT` уроки
6. `Anticipator.scan_now()` поважає афект перед генерацією/показом
7. `LLM` отримує системний промпт з блоком `АФЕКТ: ...` + `ХАРАКТЕР КОРИСТУВАЧА:`
8. Виконання .py-скриптів — через `core.sandbox.run_python()`

---

## Швидке вимкнення для дебагу

```bash
# Жодних спостережень аудіо:
$env:AFFECT_AUDIO = "0"

# Без NLP-моделі (тільки лексикон):
$env:AFFECT_NLP = "0"

# Anticipator ігнорує афект:
$env:ANTICIPATOR_AFFECT_ENABLED = "0"

# Sandbox жорстко в subprocess (швидше для розробки):
$env:JARVIS_SANDBOX = "subprocess"

# Sandbox повністю заборонити (production lock-down):
$env:JARVIS_SANDBOX = "disabled"
```
