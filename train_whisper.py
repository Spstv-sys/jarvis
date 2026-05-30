import os
import sys
import glob
import argparse
import torch
import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Any, Dict, List, Union
from scipy.io import wavfile

from datasets import Dataset
from transformers import (
    WhisperForConditionalGeneration,
    WhisperProcessor,
    Seq2SeqTrainingArguments,
    Seq2SeqTrainer,
)
from peft import LoraConfig, get_peft_model, PeftModel
import random

# ==========================================
# 0. CLI
# ==========================================
parser = argparse.ArgumentParser(description="Whisper LoRA fine-tune (Jarvis UA)")
parser.add_argument("--resume", action="store_true", help="\u041f\u0440\u043e\u0434\u043e\u0432\u0436\u0438\u0442\u0438 \u0437 \u043e\u0441\u0442\u0430\u043d\u043d\u044c\u043e\u0433\u043e checkpoint-* \u0432 OUTPUT_DIR")
parser.add_argument("--max-steps", type=int, default=500, help="\u041c\u0430\u043a\u0441\u0438\u043c\u0443\u043c \u043a\u0440\u043e\u043a\u0456\u0432 \u0442\u0440\u0435\u043d\u0443\u0432\u0430\u043d\u043d\u044f")
parser.add_argument("--extra-steps", type=int, default=200, help="\u041f\u0440\u0438 --resume: \u043d\u0430 \u0441\u043a\u0456\u043b\u044c\u043a\u0438 \u0434\u043e\u0434\u0430\u0442\u043a\u043e\u0432\u0438\u0445 \u043a\u0440\u043e\u043a\u0456\u0432 \u043f\u0456\u0434\u043d\u044f\u0442\u0438 \u043b\u0456\u043c\u0456\u0442")
parser.add_argument("--lr", type=float, default=5e-4, help="Learning rate (default знижено до 5e-4 для стабільності на малому датасеті)")
parser.add_argument("--aug", action="store_true", default=True, help="Аугментація аудіо (гауссів шум + pitch-shift): default True")
parser.add_argument("--no-aug", dest="aug", action="store_false", help="Вимкнути аугментацію")
args = parser.parse_args()


# ==========================================
# 1. КОНФІГУРАЦІЯ
# ==========================================
MODEL_NAME = "openai/whisper-small"
LANGUAGE = "ukrainian"
TASK = "transcribe"

# Налаштування шляхів (Windows-friendly)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATASET_DIR = os.path.join(BASE_DIR, "my-dataset")
TSV_PATH = os.path.join(DATASET_DIR, "train.tsv")
OUTPUT_DIR = "./whisper-small-uk-jarvis"
FINAL_MODEL_DIR = "./whisper-final-model"

# ==========================================
# 2. ПІДГОТОВКА МОДЕЛІ (float32 + LoRA, CPU)
# ==========================================
device = "cuda" if torch.cuda.is_available() else "cpu"
print(f"[*] Пристрій: {device}")
print(f"[*] Завантаження процесора та моделі: {MODEL_NAME}")
processor = WhisperProcessor.from_pretrained(MODEL_NAME, language=LANGUAGE, task=TASK)

model = WhisperForConditionalGeneration.from_pretrained(
    MODEL_NAME,
    torch_dtype=torch.float32,
)
model = model.to(device)

# --- Resume detection -----------------------------------------------------
last_ckpt = None
if args.resume:
    ckpts = sorted(
        glob.glob(os.path.join(OUTPUT_DIR, "checkpoint-*")),
        key=lambda p: int(p.rsplit("-", 1)[-1]) if p.rsplit("-", 1)[-1].isdigit() else -1,
    )
    if ckpts:
        last_ckpt = ckpts[-1]
        last_step = int(last_ckpt.rsplit("-", 1)[-1])
        print(f"[*] Resume: \u0437\u043d\u0430\u0439\u0434\u0435\u043d\u043e {last_ckpt} (step={last_step})")
        # \u0417\u0430\u0432\u0430\u043d\u0442\u0430\u0436\u0438\u0442\u0438 LoRA \u0430\u0434\u0430\u043f\u0442\u0435\u0440 \u0437 \u0447\u0435\u043a\u043f\u043e\u0457\u043d\u0442\u0430 \u0437\u0430\u043c\u0456\u0441\u0442\u044c \u0441\u0432\u0456\u0436\u043e\u0457 \u0456\u043d\u0456\u0446\u0456\u0430\u043b\u0456\u0437\u0430\u0446\u0456\u0457
        model = PeftModel.from_pretrained(model, last_ckpt, is_trainable=True)
        # Підняти max_steps, щоб було куди рости (extra_steps від поточного)
        if args.max_steps <= last_step:
            args.max_steps = last_step + args.extra_steps
        print(f"[*] max_steps = {args.max_steps} (last_step={last_step} + extra={args.extra_steps})")
    else:
        print("[!] --resume \u0432\u043a\u0430\u0437\u0430\u043d\u043e, \u0430\u043b\u0435 \u0447\u0435\u043a\u043f\u043e\u0457\u043d\u0442\u0456\u0432 \u043d\u0435 \u0437\u043d\u0430\u0439\u0434\u0435\u043d\u043e \u2014 \u043f\u043e\u0447\u0438\u043d\u0430\u044e \u0437 \u043d\u0443\u043b\u044f.")
        args.resume = False

if not args.resume:
    lora_config = LoraConfig(
        r=32,
        lora_alpha=64,
        # Додаємо більше шарів для кращого навчання на малому датасеті
        target_modules=["q_proj", "v_proj", "k_proj", "out_proj"],
        lora_dropout=0.1,   # підвищено для регуляризації на ~250 семплах
        bias="none"
    )
    model = get_peft_model(model, lora_config)
model.print_trainable_parameters()

# ==========================================
# 3. СТРОГЕ ЗАВАНТАЖЕННЯ ДАНИХ (Pandas)
# ==========================================
if not os.path.exists(TSV_PATH):
    print(f"\n[!] КРИТИЧНА ПОМИЛКА: Файл {TSV_PATH} не знайдено!")
    sys.exit()

print(f"\n[*] Читання таблиці {TSV_PATH} через Pandas...")
df = pd.read_csv(TSV_PATH, sep='\t')

# Перевірка на "битий" TSV (пробіли замість Tab)
if len(df.columns) < 2:
    print(f"\n[!!!] СТОП! Pandas бачить лише одну колонку: {df.columns.tolist()}")
    print("[*] Ваш файл розділений пробілами, а має бути символом табуляції (TAB).")
    print("[*] Відкрийте train.tsv у редакторі (наприклад, Notepad++) і замініть пробіли між шляхом і текстом на Tab.")
    sys.exit()

print(f"[*] Знайдено колонок: {len(df.columns)} -> {df.columns.tolist()}")
print(f"[*] Перший запис: {df.iloc[0].to_dict()}")

# Конвертуємо DataFrame у формат Hugging Face
raw_dataset = Dataset.from_pandas(df)

def prepare_dataset_func(batch):
    # Шукаємо ключі нечутливо до регістру
    p_key = next((k for k in batch.keys() if k.lower() == "path"), None)
    t_key = next((k for k in batch.keys() if k.lower() in ["text", "transcript", "sentence"]), None)

    if not p_key or not t_key:
        return {"input_features": None, "labels": None}

    rel_path = batch[p_key]
    transcript = str(batch[t_key]).strip()

    # Складаємо шляхи (шукаємо файл у двох місцях)
    audio_path = os.path.normpath(os.path.join(DATASET_DIR, rel_path))
    if not os.path.exists(audio_path):
        audio_path = os.path.normpath(os.path.join(DATASET_DIR, "audio", rel_path))

    # Якщо файлу немає — сигналізуємо один раз
    if not os.path.exists(audio_path):
        if not hasattr(prepare_dataset_func, "warned_path"):
            print(f"\n[!] ПОПЕРЕДЖЕННЯ: Файл не знайдено за адресою: {audio_path}")
            prepare_dataset_func.warned_path = True
        return {"input_features": None, "labels": None}

    if not transcript:
        return {"input_features": None, "labels": None}

    try:
        sr, audio = wavfile.read(audio_path)
        if audio.dtype == np.int16:
            audio = audio.astype(np.float32) / 32768.0
        elif audio.dtype == np.int32:
            audio = audio.astype(np.float32) / 2147483648.0
        else:
            audio = audio.astype(np.float32)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        if sr != 16000:
            from scipy.signal import resample
            audio = resample(audio, int(len(audio) * 16000 / sr))
            sr = 16000

        # --- Аугментація (тільки якщо --aug, тобто за замовчуванням ON) --------
        # На малому датасеті (~250 семплів) аугментація критично важлива:
        # без неї модель перенавчається вже після 200-300 кроків і не
        # узагальнює нові вимови тих самих слів.
        #
        # 1) Гауссів шум (SNR ~20-40 dB) — симулює мікрофонний шум і
        #    різну відстань від мікрофона.
        # 2) Pitch-shift ±2 напівтони через ресемплінг — навчає модель
        #    не прив'язуватись до конкретного тембру голосу.
        # 3) Рандомне масштабування гучності ×0.7..1.3 — варіація рівня.
        if args.aug and random.random() < 0.75:  # аугментуємо 75% семплів
            aug_type = random.random()
            if aug_type < 0.4:
                # Гауссів шум
                snr_db = random.uniform(20, 40)
                noise_amp = float(np.std(audio)) / (10 ** (snr_db / 20))
                audio = audio + (noise_amp * np.random.randn(len(audio))).astype(np.float32)
            elif aug_type < 0.7:
                # Pitch-shift ±1-2 напівтони через ресемплінг
                semitones = random.choice([-2, -1, 1, 2])
                rate = 2 ** (semitones / 12.0)
                from scipy.signal import resample
                shifted = resample(audio, int(len(audio) / rate)).astype(np.float32)
                # Обрізаємо/паддінгуємо до оригінальної довжини
                if len(shifted) > len(audio):
                    audio = shifted[:len(audio)]
                else:
                    audio = np.pad(shifted, (0, len(audio) - len(shifted)))
            else:
                # Масштабування гучності
                gain = random.uniform(0.7, 1.3)
                audio = np.clip(audio * gain, -1.0, 1.0).astype(np.float32)

        # --- Silence trim ---------------------------------------------------
        # Усі семпли в датасеті жорстко обрізані до 4 с, тож ~40% мають
        # тривалу тишу в кінці. Без обрізки модель навчиться, що "після
        # команди завжди тиша" і галюцинуватиме на коротких фразах.
        # Алгоритм: рахуємо локальну енергію вікнами по 20 мс і відсікаємо
        # ділянки на початку/в кінці, що нижчі за 1.5% від піку. Залишаємо
        # 100 мс паддінгу з кожного боку, щоб не різати по складах.
        if len(audio) > 0:
            win = max(1, int(0.020 * sr))                 # 20 мс
            n_win = len(audio) // win
            if n_win > 1:
                frames = audio[: n_win * win].reshape(n_win, win)
                energy = np.sqrt(np.mean(frames.astype(np.float32) ** 2, axis=1))
                peak = float(energy.max())
                if peak > 1e-4:
                    threshold = peak * 0.015
                    voiced = np.where(energy > threshold)[0]
                    if voiced.size > 0:
                        pad = max(1, int(0.100 / 0.020))  # 100 мс паддінгу
                        start = max(0, int(voiced[0]) - pad) * win
                        end = min(n_win, int(voiced[-1]) + 1 + pad) * win
                        trimmed = audio[start:end]
                        # Захист: не вирізати все, залишити ≥0.3 с
                        if len(trimmed) >= int(0.3 * sr):
                            audio = trimmed

        example = {}
        example["input_features"] = processor.feature_extractor(
            audio, sampling_rate=16000
        ).input_features[0]
        example["labels"] = processor.tokenizer(text=transcript).input_ids
        return example
    except Exception as e:
        if not hasattr(prepare_dataset_func, "warned_err"):
            print(f"\n[!] ПОМИЛКА обробки: {e}")
            prepare_dataset_func.warned_err = True
        return {"input_features": None, "labels": None}

print("\n[*] Починаємо генерацію спектрограм та токенізацію (очікуйте, швидкість має бути 2-10 it/s)...")
processed_dataset = raw_dataset.map(
    prepare_dataset_func, 
    remove_columns=raw_dataset.column_names,
    load_from_cache_file=False
)

# Видаляємо биті та порожні записи
final_dataset = processed_dataset.filter(lambda x: x.get("input_features") is not None)
print(f"\n[*] ПІДСУМОК: {len(final_dataset)} записів успішно готові до навчання.")

if len(final_dataset) == 0:
    print("[!!!] Датасет порожній після обробки. Перевірте шляхи до файлів та вміст колонок!")
    sys.exit()

# ==========================================
# 4. COLLATOR ТА АРГУМЕНТИ ТРЕНУВАННЯ
# ==========================================
@dataclass
class DataCollatorSpeechSeq2SeqWithPadding:
    processor: Any
    def __call__(self, features: List[Dict[str, Union[List[int], torch.Tensor]]]) -> Dict[str, torch.Tensor]:
        input_features = [{"input_features": feature["input_features"]} for feature in features]
        batch = self.processor.feature_extractor.pad(input_features, return_tensors="pt")

        label_features = [{"input_ids": feature["labels"]} for feature in features]
        labels_batch = self.processor.tokenizer.pad(label_features, return_tensors="pt")
        
        labels = labels_batch["input_ids"].masked_fill(labels_batch.attention_mask.ne(1), -100)
        batch["labels"] = labels
        return batch

data_collator = DataCollatorSpeechSeq2SeqWithPadding(processor=processor)

use_fp16 = torch.cuda.is_available()

# --- Авторозрахунок max_steps якщо не вказано явно ---------------------------
# Для малого датасету рекомендуємо 10-15 епох замість фіксованих кроків.
# Ефективний батч = per_device_train_batch_size * gradient_accumulation_steps
_BATCH = 4
_ACCUM = 4  # збільшено з 2 → ефективний батч 16 (краще для малого датасету)
_EFFECTIVE_BATCH = _BATCH * _ACCUM
_steps_per_epoch = max(1, len(final_dataset) // _EFFECTIVE_BATCH)
_TARGET_EPOCHS = 15  # 15 повних проходів по датасету
_auto_steps = _steps_per_epoch * _TARGET_EPOCHS

if args.max_steps == 500 and not args.resume:
    # Якщо не вказано явно — використовуємо авторозрахунок
    effective_steps = _auto_steps
    print(f"[*] Авторозрахунок: {len(final_dataset)} семплів × {_TARGET_EPOCHS} epochs "
          f"/ batch {_EFFECTIVE_BATCH} = {effective_steps} steps")
else:
    effective_steps = args.max_steps

_warmup = min(100, effective_steps // 10)  # 10% від кроків, але не більше 100

training_args = Seq2SeqTrainingArguments(
    output_dir=OUTPUT_DIR,
    per_device_train_batch_size=_BATCH,
    gradient_accumulation_steps=_ACCUM,   # ефективний батч = 16
    learning_rate=args.lr,
    warmup_steps=_warmup,                 # авторозрахунок
    max_steps=effective_steps,
    lr_scheduler_type="cosine",           # cosine decay — краще для малих датасетів
    save_steps=max(50, effective_steps // 10),
    save_total_limit=3,
    logging_steps=10,
    fp16=use_fp16,
    bf16=False,
    dataloader_num_workers=0,             # Windows: уникаємо проблем з multiprocessing
    generation_max_length=225,
    remove_unused_columns=False,
    label_names=["labels"],
    report_to="none",
    # Для малого датасету weight_decay допомагає проти перенавчання
    weight_decay=0.01,
    max_grad_norm=1.0,
)

trainer = Seq2SeqTrainer(
    args=training_args,
    model=model,
    train_dataset=final_dataset,
    data_collator=data_collator,
    #tokenizer=processor.feature_extractor,
)

# ==========================================
# 5. ЗАПУСК ТРЕНУВАННЯ
# ==========================================
print("\n" + "="*40)
print("🚀 СТАРТ ТРЕНУВАННЯ ДЖАРВІСА")
if args.resume and last_ckpt:
    print(f"   resume_from_checkpoint = {last_ckpt}")
print(f"   max_steps={args.max_steps}, lr={args.lr}")
print("="*40 + "\n")

trainer.train(resume_from_checkpoint=last_ckpt if args.resume and last_ckpt else None)

# Фінальне збереження адаптерів (без зайвих файлів чекпоїнтів)
trainer.save_model(FINAL_MODEL_DIR)
processor.save_pretrained(FINAL_MODEL_DIR)

print(f"\n[+] Успіх! Чиста модель збережена в: {FINAL_MODEL_DIR}")