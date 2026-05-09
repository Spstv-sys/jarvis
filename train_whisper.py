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

# ==========================================
# 0. CLI
# ==========================================
parser = argparse.ArgumentParser(description="Whisper LoRA fine-tune (Jarvis UA)")
parser.add_argument("--resume", action="store_true", help="\u041f\u0440\u043e\u0434\u043e\u0432\u0436\u0438\u0442\u0438 \u0437 \u043e\u0441\u0442\u0430\u043d\u043d\u044c\u043e\u0433\u043e checkpoint-* \u0432 OUTPUT_DIR")
parser.add_argument("--max-steps", type=int, default=500, help="\u041c\u0430\u043a\u0441\u0438\u043c\u0443\u043c \u043a\u0440\u043e\u043a\u0456\u0432 \u0442\u0440\u0435\u043d\u0443\u0432\u0430\u043d\u043d\u044f")
parser.add_argument("--extra-steps", type=int, default=200, help="\u041f\u0440\u0438 --resume: \u043d\u0430 \u0441\u043a\u0456\u043b\u044c\u043a\u0438 \u0434\u043e\u0434\u0430\u0442\u043a\u043e\u0432\u0438\u0445 \u043a\u0440\u043e\u043a\u0456\u0432 \u043f\u0456\u0434\u043d\u044f\u0442\u0438 \u043b\u0456\u043c\u0456\u0442")
parser.add_argument("--lr", type=float, default=1e-3, help="Learning rate")
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
        # \u041f\u0456\u0434\u043d\u044f\u0442\u0438 max_steps, \u0449\u043e\u0431 \u0431\u0443\u043b\u043e \u043a\u0443\u0434\u0438 \u0440\u043e\u0441\u0442\u0438
        if args.max_steps <= last_step:
            args.max_steps = last_step + args.extra_steps
            print(f"[*] max_steps \u043f\u0456\u0434\u043d\u044f\u0442\u043e \u0434\u043e {args.max_steps} (last_step={last_step} + extra={args.extra_steps})")
    else:
        print("[!] --resume \u0432\u043a\u0430\u0437\u0430\u043d\u043e, \u0430\u043b\u0435 \u0447\u0435\u043a\u043f\u043e\u0457\u043d\u0442\u0456\u0432 \u043d\u0435 \u0437\u043d\u0430\u0439\u0434\u0435\u043d\u043e \u2014 \u043f\u043e\u0447\u0438\u043d\u0430\u044e \u0437 \u043d\u0443\u043b\u044f.")
        args.resume = False

if not args.resume:
    lora_config = LoraConfig(
        r=32,
        lora_alpha=64,
        target_modules=["q_proj", "v_proj"],
        lora_dropout=0.05,
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
training_args = Seq2SeqTrainingArguments(
    output_dir=OUTPUT_DIR,
    per_device_train_batch_size=4,      # GPU: вмістимо 4 семпли у VRAM
    gradient_accumulation_steps=2,      # Ефективний батч = 8
    learning_rate=args.lr,
    warmup_steps=50,
    max_steps=args.max_steps,
    save_steps=100,
    save_total_limit=3,                 # тримаємо лише 3 останніх чекпоінти
    logging_steps=10,
    fp16=use_fp16,
    bf16=False,
    dataloader_num_workers=0,           # Windows: уникаємо проблем з multiprocessing
    generation_max_length=225,
    remove_unused_columns=False,
    label_names=["labels"],
    report_to="none",
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