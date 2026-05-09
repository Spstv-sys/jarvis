"""
Жива діагностика neo wake-word моделі.

Постійно слухає мікрофон і виводить бал моделі в реальному часі.
Допомагає підібрати OWW_WAKE_THRESHOLD і побачити що модель "чує".

USAGE:
    .venv\\Scripts\\activate
    python training\\neo_wakeword\\debug_neo_live.py
"""
from __future__ import annotations

import os
import sys
import time

import numpy as np
import pyaudio
from openwakeword.model import Model

MODEL_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", ".venv",
    "Lib", "site-packages", "openwakeword", "resources", "models", "neo.onnx"
)
MODEL_PATH = os.path.abspath(MODEL_PATH)

VAD_THRESHOLD = 0.0   # 0 = вимкнено, щоб бачити сирі бали
CHUNK         = 1280  # 80 ms @ 16 kHz

print(f"Завантажую модель: {MODEL_PATH}")
oww = Model(wakeword_models=[MODEL_PATH], inference_framework="onnx",
            vad_threshold=VAD_THRESHOLD)
print(f"Модель готова. Класи: {list(oww.models.keys())}")
print("Скажіть 'Нео'. Ctrl+C — вийти.\n")

pa = pyaudio.PyAudio()
stream = pa.open(format=pyaudio.paInt16, channels=1, rate=16000,
                 input=True, frames_per_buffer=CHUNK)

last_print = 0.0
peak_score = 0.0
try:
    while True:
        data = np.frombuffer(stream.read(CHUNK, exception_on_overflow=False),
                             dtype=np.int16)
        rms = float(np.sqrt(np.mean(data.astype(np.float64) ** 2)))
        pred = oww.predict(data)
        score = float(max(pred.values(), default=0.0))

        peak_score = max(peak_score, score)
        bar_len = int(score * 40)
        bar = "█" * bar_len + "·" * (40 - bar_len)
        marker = " 🔥 WAKE!" if score > 0.5 else ""
        print(f"\rRMS={rms:6.0f}  score={score:.3f}  peak={peak_score:.3f}  "
              f"|{bar}|{marker}", end="", flush=True)

        # Reset peak every 5 s
        now = time.time()
        if now - last_print > 5:
            last_print = now
            print(f"   <peak reset, було {peak_score:.3f}>")
            peak_score = 0.0
except KeyboardInterrupt:
    print("\n\nЗавершено.")
finally:
    stream.stop_stream()
    stream.close()
    pa.terminate()
