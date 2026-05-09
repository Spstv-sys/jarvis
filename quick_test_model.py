"""Швидка перевірка натренованої LoRA моделі на кількох семплах з train.tsv."""
import os, sys, csv, random, torch
from transformers import WhisperForConditionalGeneration, WhisperProcessor
from peft import PeftModel
from scipy.io import wavfile
import numpy as np

BASE = "openai/whisper-small"
ADAPTER = "./whisper-final-model"
TSV = "my-dataset/train.tsv"
AUDIO_DIR = "my-dataset"
N_SAMPLES = 8

device = "cuda" if torch.cuda.is_available() else "cpu"
dtype = torch.float16 if device == "cuda" else torch.float32

print(f"[*] Пристрій: {device}")
print(f"[*] Завантаження базової моделі: {BASE}")
processor = WhisperProcessor.from_pretrained(ADAPTER)
base_model = WhisperForConditionalGeneration.from_pretrained(BASE, torch_dtype=dtype)
print(f"[*] Підключення LoRA адаптера: {ADAPTER}")
model = PeftModel.from_pretrained(base_model, ADAPTER)
model.to(device).eval()

# Force Ukrainian
forced_ids = processor.get_decoder_prompt_ids(language="uk", task="transcribe")

# Зчитати TSV
rows = []
with open(TSV, encoding="utf-8") as f:
    r = csv.reader(f, delimiter="\t")
    next(r)
    for line in r:
        if len(line) >= 2:
            rows.append((line[0].strip(), line[1].strip()))

# Взяти семпли з різних діапазонів (старі + нові)
random.seed(42)
old = [r for r in rows if int(os.path.basename(r[0])[6:10]) < 106]
new = [r for r in rows if int(os.path.basename(r[0])[6:10]) >= 106]
samples = random.sample(old, min(4, len(old))) + random.sample(new, min(4, len(new)))

print(f"\n{'='*70}")
print(f"Тестую {len(samples)} семплів (старі + нові)")
print(f"{'='*70}\n")

correct = 0
for path, ref_text in samples:
    fp = os.path.join(AUDIO_DIR, path)
    sr, audio = wavfile.read(fp)
    if audio.ndim > 1:
        audio = audio[:, 0]
    audio = audio.astype(np.float32) / 32768.0

    inputs = processor(audio, sampling_rate=sr, return_tensors="pt")
    input_features = inputs.input_features.to(device).to(dtype)

    with torch.no_grad():
        pred_ids = model.generate(
            input_features,
            forced_decoder_ids=forced_ids,
            max_new_tokens=200,
        )
    pred_text = processor.batch_decode(pred_ids, skip_special_tokens=True)[0].strip()

    is_old = int(os.path.basename(path)[6:10]) < 106
    tag = "СТАРИЙ" if is_old else "НОВИЙ"
    match = "✓" if pred_text.lower().strip(".,!?") == ref_text.lower().strip(".,!?") else "✗"
    if match == "✓":
        correct += 1

    print(f"[{tag}] {os.path.basename(path)}  {match}")
    print(f"  REF : {ref_text}")
    print(f"  PRED: {pred_text}")
    print()

print(f"{'='*70}")
print(f"Точне співпадіння: {correct}/{len(samples)}")
print(f"{'='*70}")
