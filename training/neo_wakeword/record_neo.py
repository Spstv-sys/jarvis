"""
Інтерактивний запис реальних "Нео" з мікрофона.

USAGE
-----
    .venv-train\\Scripts\\activate
    python record_neo.py

Натискайте ENTER → 1.5 с запису → автоматичне обрізання тиші → збереження.
По замовчуванню робить 40 записів. Зберігає у:
    training_data/user_recordings/user_NN.wav  (для прослуховування)
    training_data/user_clips.npy               (для train_neo.py)

Після запису ОБОВʼЯЗКОВО видаліть кеш фіч позитивів, щоб вони перерахувалися:
    Remove-Item training_data\\positive_clips.npy
    Remove-Item training_data\\features\\positive_*.npy
А потім: python train_neo.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf

# Має співпадати з train_neo.py
SAMPLE_RATE   = 16_000
CLIP_DURATION = 1.28
CLIP_SAMPLES  = int(CLIP_DURATION * SAMPLE_RATE)   # 20 480
RECORD_SECONDS = 1.5     # трохи більше від CLIP_DURATION щоб був запас

DEFAULT_N    = 40
N_AUGMENT    = 4         # скільки шумних варіацій додатково зробити з кожного запису
NOISE_LEVEL  = 5e-3      # амплітуда additive noise

OUT_DIR     = Path(__file__).parent / "training_data"
WAV_DIR     = OUT_DIR / "user_recordings"
CLIPS_FILE  = OUT_DIR / "user_clips.npy"


def _trim_and_centre(audio: np.ndarray, threshold: float = 0.01) -> np.ndarray:
    """Знаходить найгучніший сегмент CLIP_SAMPLES довжиною."""
    audio = audio.astype(np.float32)
    if len(audio) <= CLIP_SAMPLES:
        pad = CLIP_SAMPLES - len(audio)
        return np.pad(audio, (pad // 2, pad - pad // 2), mode="constant")

    # знайти індекс максимальної енергії в ковзному вікні
    abs_audio = np.abs(audio)
    window = np.ones(CLIP_SAMPLES, dtype=np.float32) / CLIP_SAMPLES
    energy = np.convolve(abs_audio, window, mode="valid")
    centre = int(np.argmax(energy))
    return audio[centre: centre + CLIP_SAMPLES]


def _normalize(audio: np.ndarray) -> np.ndarray:
    peak = float(np.max(np.abs(audio)))
    if peak < 1e-4:
        return audio
    return (audio / peak * 0.95).astype(np.float32)


def record_one(idx: int, total: int) -> np.ndarray | None:
    print(f"\n[{idx}/{total}] Натисніть ENTER, потім чітко скажіть 'Нео'  (q + ENTER — вийти)")
    line = input("> ")
    if line.strip().lower() == "q":
        return None

    # коротка пауза щоб мікрофон не захопив звук клавіші
    time.sleep(0.15)
    print("    🎙  записую...", end="", flush=True)
    audio = sd.rec(int(RECORD_SECONDS * SAMPLE_RATE),
                   samplerate=SAMPLE_RATE, channels=1, dtype="float32")
    sd.wait()
    print("  ✓")

    audio = audio.flatten()
    peak = float(np.max(np.abs(audio)))
    print(f"    рівень сигналу: {peak:.3f}", end="")
    if peak < 0.02:
        print("  ⚠  занадто тихо — повторіть")
        return record_one(idx, total)
    print()

    clip = _normalize(_trim_and_centre(audio))
    return clip


def main() -> None:
    n_str = input(f"Скільки записів зробити? [{DEFAULT_N}]: ").strip()
    n_target = int(n_str) if n_str.isdigit() else DEFAULT_N

    print("\nДоступні мікрофони:")
    print(sd.query_devices())
    dev_str = input(f"Номер пристрою (Enter — за замовчуванням): ").strip()
    if dev_str.isdigit():
        sd.default.device = (int(dev_str), None)
    print(f"Використовую input device: {sd.query_devices(sd.default.device[0])['name']}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    WAV_DIR.mkdir(parents=True, exist_ok=True)

    # Зчитати вже існуючі записи (якщо хочемо продовжити)
    existing_clips: list[np.ndarray] = []
    if CLIPS_FILE.exists():
        ans = input(f"Знайдено {CLIPS_FILE.name}. Додати до існуючих? [Y/n]: ").strip().lower()
        if ans != "n":
            existing_clips = list(np.load(CLIPS_FILE))
            print(f"  Завантажено {len(existing_clips)} існуючих кліпів.")

    new_clips: list[np.ndarray] = []
    start_idx = len(list(WAV_DIR.glob("user_*.wav"))) + 1

    for i in range(n_target):
        clip = record_one(i + 1, n_target)
        if clip is None:
            print("Зупинено користувачем.")
            break

        wav_path = WAV_DIR / f"user_{start_idx + i:03d}.wav"
        sf.write(str(wav_path), clip, SAMPLE_RATE)
        new_clips.append(clip)
        print(f"    💾  {wav_path.name}")

    if not new_clips:
        print("\nНічого не записано.")
        return

    # Augmentation: додаємо N_AUGMENT шумних копій кожного запису
    print(f"\n🔊 Створюю {N_AUGMENT} шумних варіацій для кожного запису ({len(new_clips)} × {N_AUGMENT})…")
    augmented: list[np.ndarray] = []
    for clip in new_clips:
        for _ in range(N_AUGMENT):
            noise = np.random.randn(CLIP_SAMPLES).astype(np.float32) * NOISE_LEVEL
            noisy = np.clip(clip + noise, -1.0, 1.0)
            augmented.append(noisy)

    all_clips = existing_clips + new_clips + augmented
    arr = np.array(all_clips, dtype=np.float32)
    np.save(str(CLIPS_FILE), arr)

    print(f"\n✅ Збережено {len(all_clips)} кліпів → {CLIPS_FILE}")
    print(f"   ({len(new_clips)} нових записів + {len(augmented)} аугментацій"
          f" + {len(existing_clips)} існуючих)")
    print("\nДалі:")
    print("   Remove-Item training_data\\positive_clips.npy")
    print("   Remove-Item training_data\\features\\positive_*.npy")
    print("   python train_neo.py")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nПерервано.")
        sys.exit(0)
