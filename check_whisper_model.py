"""
Перевірка роботоспроможності fine-tuned Whisper моделі (LoRA адаптер).
Завантажує базову модель openai/whisper-small + LoRA з whisper-final-model/
та транскрибує кілька тестових аудіофайлів.
"""

import os
import sys
import time
import torch
import numpy as np
from scipy.io import wavfile
from transformers import WhisperProcessor, WhisperForConditionalGeneration
from peft import PeftModel

BASE_MODEL = "openai/whisper-small"
ADAPTER_DIR = os.path.join(os.path.dirname(__file__), "whisper-final-model")
TEST_AUDIO_DIR = os.path.join(os.path.dirname(__file__), "my-dataset", "audio")
NUM_TEST_FILES = 5  # скільки файлів перевірити

def load_model():
    print(f"[1/3] Завантаження процесора з: {ADAPTER_DIR}")
    processor = WhisperProcessor.from_pretrained(ADAPTER_DIR)

    print(f"[2/3] Завантаження базової моделі: {BASE_MODEL}")
    base_model = WhisperForConditionalGeneration.from_pretrained(
        BASE_MODEL,
        torch_dtype=torch.float32,
    )

    print(f"[3/3] Застосування LoRA адаптера з: {ADAPTER_DIR}")
    model = PeftModel.from_pretrained(base_model, ADAPTER_DIR)
    model.eval()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = model.to(device)
    print(f"     Пристрій: {device}\n")
    return processor, model, device


def transcribe(audio_path, processor, model, device):
    sr, audio = wavfile.read(audio_path)
    # конвертація в float32 mono [-1, 1]
    if audio.dtype == np.int16:
        audio = audio.astype(np.float32) / 32768.0
    elif audio.dtype == np.int32:
        audio = audio.astype(np.float32) / 2147483648.0
    elif audio.dtype != np.float32:
        audio = audio.astype(np.float32)

    if audio.ndim > 1:
        audio = audio.mean(axis=1)

    # ресемплінг до 16 кГц якщо потрібно
    if sr != 16000:
        from scipy.signal import resample
        num_samples = int(len(audio) * 16000 / sr)
        audio = resample(audio, num_samples)

    inputs = processor(audio, sampling_rate=16000, return_tensors="pt")
    input_features = inputs.input_features.to(device)

    forced_ids = processor.get_decoder_prompt_ids(language="uk", task="transcribe")

    with torch.no_grad():
        predicted_ids = model.generate(
            input_features,
            forced_decoder_ids=forced_ids,
            max_new_tokens=225,
        )

    transcription = processor.batch_decode(predicted_ids, skip_special_tokens=True)
    return transcription[0].strip()


def main():
    # --- перевірка наявності адаптера ---
    required = ["adapter_config.json", "adapter_model.safetensors",
                "tokenizer.json", "tokenizer_config.json"]
    missing = [f for f in required if not os.path.exists(os.path.join(ADAPTER_DIR, f))]
    if missing:
        print(f"ПОМИЛКА: У {ADAPTER_DIR} відсутні файли: {missing}")
        sys.exit(1)

    # --- завантаження ---
    try:
        processor, model, device = load_model()
    except Exception as e:
        print(f"ПОМИЛКА завантаження моделі: {e}")
        sys.exit(1)

    # --- вибір тестових файлів ---
    audio_files = sorted([
        f for f in os.listdir(TEST_AUDIO_DIR) if f.endswith(".wav")
    ])[:NUM_TEST_FILES]

    if not audio_files:
        print(f"ПОМИЛКА: Немає WAV файлів у {TEST_AUDIO_DIR}")
        sys.exit(1)

    print(f"{'='*60}")
    print(f"Транскрибування {len(audio_files)} тестових файлів...")
    print(f"{'='*60}\n")

    success = 0
    for i, fname in enumerate(audio_files, 1):
        path = os.path.join(TEST_AUDIO_DIR, fname)
        print(f"[{i}/{len(audio_files)}] {fname}")
        try:
            t0 = time.time()
            text = transcribe(path, processor, model, device)
            elapsed = time.time() - t0
            print(f"  Результат : {text if text else '(порожньо)'}")
            print(f"  Час       : {elapsed:.2f}s")
            success += 1
        except Exception as e:
            print(f"  ПОМИЛКА   : {e}")
        print()

    print(f"{'='*60}")
    print(f"Успішно: {success}/{len(audio_files)}")
    if success == len(audio_files):
        print("Модель ПРАЦЮЄ коректно.")
    elif success > 0:
        print("Модель частково працює — є помилки на окремих файлах.")
    else:
        print("Модель НЕ ПРАЦЮЄ.")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
