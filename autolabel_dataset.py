"""
Авто-транскрибування аудіо датасету базовою whisper-small моделлю.
Заповнює my-dataset/train.tsv транскрипціями.
Запуск: python autolabel_dataset.py
"""

import os
import csv
import torch
import numpy as np
from scipy.io import wavfile
from transformers import WhisperProcessor, WhisperForConditionalGeneration

AUDIO_DIR = os.path.join(os.path.dirname(__file__), "my-dataset", "audio")
TSV_PATH  = os.path.join(os.path.dirname(__file__), "my-dataset", "train.tsv")
BASE_MODEL = "openai/whisper-small"

def load_audio(path):
    sr, audio = wavfile.read(path)
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
    return audio

def main():
    print(f"Завантаження {BASE_MODEL}...")
    processor = WhisperProcessor.from_pretrained(BASE_MODEL)
    model = WhisperForConditionalGeneration.from_pretrained(
        BASE_MODEL, torch_dtype=torch.float32
    ).eval()

    audio_files = sorted(f for f in os.listdir(AUDIO_DIR) if f.endswith(".wav"))
    print(f"Знайдено {len(audio_files)} файлів. Починаю транскрибування...\n")

    rows = []
    for i, fname in enumerate(audio_files, 1):
        path = os.path.join(AUDIO_DIR, fname)
        try:
            audio = load_audio(path)
            inputs = processor(audio, sampling_rate=16000, return_tensors="pt")
            with torch.no_grad():
                ids = model.generate(
                    inputs.input_features,
                    language="uk",
                    task="transcribe",
                    max_new_tokens=225,
                )
            text = processor.batch_decode(ids, skip_special_tokens=True)[0].strip()
        except Exception as e:
            text = ""
            print(f"  [{i}/{len(audio_files)}] ПОМИЛКА {fname}: {e}")
        
        rows.append((f"audio/{fname}", text))
        print(f"  [{i:>3}/{len(audio_files)}] {fname}: {text[:80]}")

    # Зберігаємо TSV
    with open(TSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(["path", "text"])
        writer.writerows(rows)

    empty = sum(1 for _, t in rows if not t)
    print(f"\nГотово! Збережено {len(rows)} записів у {TSV_PATH}")
    print(f"Порожніх транскрипцій: {empty}/{len(rows)}")
    if empty > 0:
        print("Порожні записи — перевірте ці файли вручну.")

if __name__ == "__main__":
    main()
