# -*- coding: utf-8 -*-
"""Швидкий офлайн-тест донавченої Whisper LoRA моделі."""
import os
import sys
import random
import io
import warnings

# UTF-8 для Windows консолі
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
warnings.filterwarnings("ignore")

import torch
import soundfile as sf
import numpy as np
import librosa
import pandas as pd
from transformers import WhisperProcessor, WhisperForConditionalGeneration
from peft import PeftModel

BASE_MODEL = "openai/whisper-small"
ADAPTER_DIR = "./whisper-final-model"
DATASET_DIR = "./my-dataset"
TSV_PATH = os.path.join(DATASET_DIR, "train.tsv")
N_SAMPLES = 10

device = "cuda" if torch.cuda.is_available() else "cpu"
print(f"[*] Пристрій: {device}")

print("[*] Завантажую процесор та базову модель...")
processor = WhisperProcessor.from_pretrained(BASE_MODEL)
base_model = WhisperForConditionalGeneration.from_pretrained(BASE_MODEL).to(device)

print(f"[*] Завантажую LoRA адаптер з {ADAPTER_DIR}...")
model = PeftModel.from_pretrained(base_model, ADAPTER_DIR).to(device)
model.eval()

# forced decoder для української
forced_ids = processor.get_decoder_prompt_ids(language="uk", task="transcribe")

df = pd.read_csv(TSV_PATH, sep="\t")
random.seed(42)
indices = random.sample(range(len(df)), min(N_SAMPLES, len(df)))

print("\n" + "=" * 70)
print(f"ТЕСТ НА {len(indices)} ВИПАДКОВИХ ЗРАЗКАХ")
print("=" * 70)

for i, idx in enumerate(indices, 1):
    row = df.iloc[idx]
    wav_path = os.path.join(DATASET_DIR, row["path"])
    label = str(row["text"]).strip()

    audio, sr = sf.read(wav_path)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sr != 16000:
        audio = librosa.resample(audio.astype(np.float32), orig_sr=sr, target_sr=16000)
        sr = 16000

    inputs = processor(audio, sampling_rate=16000, return_tensors="pt")
    input_features = inputs.input_features.to(device)

    with torch.no_grad():
        pred_ids = model.generate(
            input_features,
            forced_decoder_ids=forced_ids,
            max_new_tokens=128,
        )
    pred = processor.batch_decode(pred_ids, skip_special_tokens=True)[0].strip()

    match = "✓" if pred.lower() == label.lower() else "✗"
    print(f"\n[{i}] {row['path']}  {match}")
    print(f"  МІТКА:  {label}")
    print(f"  МОДЕЛЬ: {pred}")

print("\n" + "=" * 70)
print("[+] Тест завершено")
