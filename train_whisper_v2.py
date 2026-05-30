"""Whisper LoRA fine-tune — Фаза 3 (speaker-dependent, з евалом).

Відмінності від train_whisper.py (під ціль «лише мій голос — на відмінно»):
  1. БЕЗ pitch-shift в аугментації. Pitch-shift навмисно розмиває тембр —
     корисно для багатоголосу і ШКІДЛИВО, коли ціль — прилипнути до одного
     голосу. Натомість: гауссів шум + гучність + легка реверберація.
  2. EVAL-SPLIT + WER-метрика: датасет ділиться на train/eval, на кожному
     eval-кроці рахується WER, найкращий чекпойнт обирається АВТОМАТИЧНО
     (load_best_model_at_end за метрикою wer), а не «останній наосліп».
  3. Рання зупинка (EarlyStoppingCallback) — не перетреновуватись.

Аугментація застосовується ЛИШЕ до train-частини (eval лишається чистим).

Запуск:
    python train_whisper_v2.py                 # з нуля, авто-кроки за розміром даних
    python train_whisper_v2.py --max-steps 1200
    python train_whisper_v2.py --no-aug        # без аугментації
    python train_whisper_v2.py --eval-frac 0.1 # частка на eval (default 0.12)
"""
import os
import sys
import argparse
import random

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
    EarlyStoppingCallback,
)
from peft import LoraConfig, get_peft_model

try:
    import evaluate as _hf_evaluate
except Exception:
    _hf_evaluate = None

# ==========================================
# 0. CLI
# ==========================================
parser = argparse.ArgumentParser(description="Whisper LoRA fine-tune v2 (Jarvis UA, speaker-dependent)")
parser.add_argument("--max-steps", type=int, default=0, help="Максимум кроків (0 = авто за розміром датасету)")
parser.add_argument("--lr", type=float, default=5e-4, help="Learning rate")
parser.add_argument("--aug", action="store_true", default=True, help="Аугментація (шум+гучність+реверб): default ON")
parser.add_argument("--no-aug", dest="aug", action="store_false", help="Вимкнути аугментацію")
parser.add_argument("--eval-frac", type=float, default=0.12, help="Частка датасету на eval (held-out усередині train.tsv)")
parser.add_argument("--patience", type=int, default=4, help="Рання зупинка: скільки eval-кроків без покращення WER")
args = parser.parse_args()

# ==========================================
# 1. КОНФІГУРАЦІЯ
# ==========================================
MODEL_NAME = "openai/whisper-small"
LANGUAGE = "ukrainian"
TASK = "transcribe"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATASET_DIR = os.path.join(BASE_DIR, "my-dataset")
TSV_PATH = os.path.join(DATASET_DIR, "train.tsv")
OUTPUT_DIR = "./whisper-small-uk-jarvis-v2"
FINAL_MODEL_DIR = "./whisper-final-model"   # той самий шлях, що читає audio_manager

device = "cuda" if torch.cuda.is_available() else "cpu"
use_fp16 = torch.cuda.is_available()
print(f"[*] Пристрій: {device}")
print(f"[*] Завантаження процесора та моделі: {MODEL_NAME}")
processor = WhisperProcessor.from_pretrained(MODEL_NAME, language=LANGUAGE, task=TASK)

model = WhisperForConditionalGeneration.from_pretrained(MODEL_NAME, dtype=torch.float32).to(device)

# КРИТИЧНО для eval з predict_with_generate: інакше generate кидає
# CUDA device-side assert (forced_decoder_ids конфліктує з language/task).
model.config.forced_decoder_ids = None
model.config.suppress_tokens = []
model.generation_config.language = LANGUAGE
model.generation_config.task = TASK
model.generation_config.forced_decoder_ids = None

lora_config = LoraConfig(
    r=32,
    lora_alpha=64,
    target_modules=["q_proj", "v_proj", "k_proj", "out_proj"],
    lora_dropout=0.1,
    bias="none",
)
model = get_peft_model(model, lora_config)
model.print_trainable_parameters()

# ==========================================
# 2. ДАНІ
# ==========================================
if not os.path.exists(TSV_PATH):
    print(f"\n[!] КРИТИЧНА ПОМИЛКА: {TSV_PATH} не знайдено!")
    sys.exit(1)

df = pd.read_csv(TSV_PATH, sep="\t")
if len(df.columns) < 2:
    print(f"\n[!!!] СТОП! Pandas бачить лише одну колонку: {df.columns.tolist()} — TSV має бути розділений Tab.")
    sys.exit(1)
print(f"[*] Колонок: {len(df.columns)} -> {df.columns.tolist()}; рядків: {len(df)}")

raw_dataset = Dataset.from_pandas(df)

# Розділяємо ДО обробки, щоб аугментацію вмикати лише для train.
split = raw_dataset.train_test_split(test_size=args.eval_frac, seed=42)
raw_train, raw_eval = split["train"], split["test"]
print(f"[*] Split: train={len(raw_train)}  eval={len(raw_eval)} (eval_frac={args.eval_frac})")


def _load_audio(rel_path: str):
    full = os.path.join(DATASET_DIR, rel_path)
    if not os.path.exists(full):
        alt = os.path.join(DATASET_DIR, "audio1", os.path.basename(rel_path))
        full = alt if os.path.exists(alt) else full
    sr, audio = wavfile.read(full)
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
    return sr, audio


def _augment(audio: np.ndarray, sr: int) -> np.ndarray:
    """Speaker-dependent аугментація — БЕЗ pitch-shift (зберігаємо тембр).

    Розподіл: 35% шум, 35% гучність, 20% легка реверберація, 10% без змін.
    """
    r = random.random()
    if r < 0.35:
        snr_db = random.uniform(18, 40)
        noise_amp = float(np.std(audio)) / (10 ** (snr_db / 20))
        audio = audio + (noise_amp * np.random.randn(len(audio))).astype(np.float32)
    elif r < 0.70:
        gain = random.uniform(0.7, 1.3)
        audio = np.clip(audio * gain, -1.0, 1.0).astype(np.float32)
    elif r < 0.90:
        # дешева кімнатна реверберація: кілька загасаючих відлунь
        ir_len = int(sr * random.uniform(0.02, 0.06))
        ir = np.zeros(ir_len, dtype=np.float32)
        ir[0] = 1.0
        for _ in range(random.randint(2, 4)):
            pos = random.randint(1, max(1, ir_len - 1))
            ir[pos] += random.uniform(0.1, 0.35)
        audio = np.convolve(audio, ir)[: len(audio)].astype(np.float32)
        m = float(np.max(np.abs(audio)) or 1.0)
        if m > 1.0:
            audio = audio / m
    # else: 10% — без змін
    return audio


def _silence_trim(audio: np.ndarray, sr: int) -> np.ndarray:
    if len(audio) == 0:
        return audio
    win = max(1, int(0.020 * sr))
    n_win = len(audio) // win
    if n_win <= 1:
        return audio
    frames = audio[: n_win * win].reshape(n_win, win)
    energy = np.sqrt(np.mean(frames.astype(np.float32) ** 2, axis=1))
    thr = 0.015 * float(energy.max() + 1e-9)
    loud = np.where(energy > thr)[0]
    if len(loud) == 0:
        return audio
    pad = max(1, int(0.100 / 0.020))
    s_idx = max(0, loud[0] - pad)
    e_idx = min(n_win, loud[-1] + pad + 1)
    return audio[s_idx * win : e_idx * win]


def _make_prepare(do_aug: bool):
    def _prep(batch):
        p_key = next((k for k in batch.keys() if k.lower() == "path"), None)
        t_key = next((k for k in batch.keys() if k.lower() in ["text", "transcript", "sentence"]), None)
        rel, transcript = batch[p_key], batch[t_key]
        if not transcript:
            return {"input_features": None, "labels": None}
        try:
            sr, audio = _load_audio(rel)
            if do_aug and args.aug and random.random() < 0.75:
                audio = _augment(audio, sr)
            audio = _silence_trim(audio, sr)
            feats = processor.feature_extractor(audio, sampling_rate=16000, return_tensors="np")
            batch["input_features"] = feats.input_features[0]
            batch["labels"] = processor.tokenizer(transcript).input_ids
            return batch
        except Exception as e:
            print(f"[!] Помилка обробки {rel}: {e}")
            return {"input_features": None, "labels": None}
    return _prep


print("\n[*] Обробка train (з аугментацією) та eval (чистий)...")
train_ds = raw_train.map(_make_prepare(True), remove_columns=raw_train.column_names, desc="train")
eval_ds = raw_eval.map(_make_prepare(False), remove_columns=raw_eval.column_names, desc="eval")
train_ds = train_ds.filter(lambda x: x["input_features"] is not None and x["labels"] is not None)
eval_ds = eval_ds.filter(lambda x: x["input_features"] is not None and x["labels"] is not None)
print(f"[*] Валідних: train={len(train_ds)}  eval={len(eval_ds)}")

# ==========================================
# 3. КОЛАТОР
# ==========================================
@dataclass
class DataCollatorSpeechSeq2SeqWithPadding:
    processor: Any

    def __call__(self, features: List[Dict[str, Union[List[int], torch.Tensor]]]) -> Dict[str, torch.Tensor]:
        input_features = [{"input_features": f["input_features"]} for f in features]
        batch = self.processor.feature_extractor.pad(input_features, return_tensors="pt")
        label_features = [{"input_ids": f["labels"]} for f in features]
        labels_batch = self.processor.tokenizer.pad(label_features, return_tensors="pt")
        labels = labels_batch["input_ids"].masked_fill(labels_batch.attention_mask.ne(1), -100)
        if (labels[:, 0] == self.processor.tokenizer.bos_token_id).all().cpu().item():
            labels = labels[:, 1:]
        batch["labels"] = labels
        return batch


collator = DataCollatorSpeechSeq2SeqWithPadding(processor=processor)

# ==========================================
# 4. МЕТРИКА (WER)
# ==========================================
_metric = _hf_evaluate.load("wer") if _hf_evaluate is not None else None


def _compute_metrics(pred):
    pred_ids = pred.predictions
    label_ids = pred.label_ids
    label_ids = np.where(label_ids != -100, label_ids, processor.tokenizer.pad_token_id)
    pred_str = processor.tokenizer.batch_decode(pred_ids, skip_special_tokens=True)
    label_str = processor.tokenizer.batch_decode(label_ids, skip_special_tokens=True)
    if _metric is not None:
        wer = 100.0 * _metric.compute(predictions=pred_str, references=label_str)
    else:
        # фолбек: проста по-словна відстань, якщо evaluate не встановлено
        def _wer(r, h):
            r, h = r.lower().split(), h.lower().split()
            d = [[0] * (len(h) + 1) for _ in range(len(r) + 1)]
            for i in range(len(r) + 1):
                d[i][0] = i
            for j in range(len(h) + 1):
                d[0][j] = j
            for i in range(1, len(r) + 1):
                for j in range(1, len(h) + 1):
                    c = 0 if r[i - 1] == h[j - 1] else 1
                    d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + c)
            return d[len(r)][len(h)] / max(1, len(r))
        wer = 100.0 * float(np.mean([_wer(r, h) for r, h in zip(label_str, pred_str)]))
    return {"wer": wer}


# ==========================================
# 5. ТРЕНУВАННЯ (eval + best-checkpoint + early stop)
# ==========================================
_BATCH = 8 if torch.cuda.is_available() else 1
_ACCUM = 2 if torch.cuda.is_available() else 16

if args.max_steps > 0:
    max_steps = args.max_steps
else:
    eff_batch = _BATCH * _ACCUM
    steps_per_epoch = max(1, len(train_ds) // eff_batch)
    max_steps = max(300, steps_per_epoch * 12)   # ~12 епох, але не менше 300
warmup = min(100, max_steps // 10)
eval_steps = max(25, max_steps // 20)            # ~20 точок оцінки
print(f"[*] max_steps={max_steps}  eval_steps={eval_steps}  warmup={warmup}")

training_args = Seq2SeqTrainingArguments(
    output_dir=OUTPUT_DIR,
    per_device_train_batch_size=_BATCH,
    per_device_eval_batch_size=_BATCH,
    gradient_accumulation_steps=_ACCUM,
    learning_rate=args.lr,
    warmup_steps=warmup,
    max_steps=max_steps,
    fp16=use_fp16,
    predict_with_generate=True,
    generation_max_length=128,
    eval_strategy="steps",
    eval_steps=eval_steps,
    save_strategy="steps",
    save_steps=eval_steps,                       # збіг із eval → можна обрати найкращий
    save_total_limit=3,
    logging_steps=10,
    load_best_model_at_end=True,
    metric_for_best_model="wer",
    greater_is_better=False,                     # WER: менше = краще
    report_to="none",
    remove_unused_columns=False,
    label_names=["labels"],
)

trainer = Seq2SeqTrainer(
    model=model,
    args=training_args,
    train_dataset=train_ds,
    eval_dataset=eval_ds,
    data_collator=collator,
    compute_metrics=_compute_metrics,
    callbacks=[EarlyStoppingCallback(early_stopping_patience=args.patience)],
)

model.config.use_cache = False

print("\n[*] СТАРТ ТРЕНУВАННЯ...")
trainer.train()

print("\n[*] Найкращий (за WER) чекпойнт уже завантажено в модель. Зберігаю адаптер...")
model.save_pretrained(FINAL_MODEL_DIR)
processor.save_pretrained(FINAL_MODEL_DIR)

try:
    best = trainer.state.best_metric
    print(f"[*] Найкращий eval WER: {best:.2f}%")
except Exception:
    pass
print(f"[*] Готово! Адаптер у {FINAL_MODEL_DIR}")
print("[*] Перевір на held-out: python eval_whisper.py")