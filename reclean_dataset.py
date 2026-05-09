"""
Перетранскрибує всі WAV у my-dataset/audio через faster-whisper large-v3
та зберігає результат у my-dataset/train.tsv.cleaned для ручної звірки.

Після правки:
    Move-Item my-dataset/train.tsv my-dataset/train.tsv.bak
    Move-Item my-dataset/train.tsv.cleaned my-dataset/train.tsv
    python train_whisper.py
"""
import csv
import os
import sys
from pathlib import Path

from faster_whisper import WhisperModel

DATASET_DIR = Path("my-dataset")
AUDIO_DIR = DATASET_DIR / "audio"
SRC_TSV = DATASET_DIR / "train.tsv"
OUT_TSV = DATASET_DIR / "train.tsv.cleaned"

MODEL_SIZE = "large-v3"  # найточніший для української


def main() -> int:
    if not AUDIO_DIR.is_dir():
        print(f"[ERROR] Не знайдено {AUDIO_DIR}", file=sys.stderr)
        return 1

    # Старі мітки — для side-by-side порівняння
    old_labels: dict[str, str] = {}
    if SRC_TSV.exists():
        with SRC_TSV.open(encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter="\t")
            for row in reader:
                old_labels[row["path"]] = row["text"]

    print(f"[INFO] Завантажую faster-whisper {MODEL_SIZE} (GPU)...")
    model = WhisperModel(MODEL_SIZE, device="cuda", compute_type="float16")

    wavs = sorted(AUDIO_DIR.glob("*.wav"))
    print(f"[INFO] Знайдено {len(wavs)} аудіофайлів. Транскрибую...")

    rows: list[tuple[str, str, str]] = []
    for i, wav in enumerate(wavs, 1):
        rel = f"audio/{wav.name}"
        try:
            segments, _info = model.transcribe(
                str(wav),
                language="uk",
                beam_size=5,
                best_of=5,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=300),
                condition_on_previous_text=False,
            )
            text = "".join(s.text for s in segments).strip()
        except Exception as e:
            print(f"[WARN] {rel}: {e}")
            text = ""

        old = old_labels.get(rel, "")
        marker = "  " if text == old else "≠ "
        print(f"[{i:3d}/{len(wavs)}] {marker}{rel}")
        print(f"       OLD: {old}")
        print(f"       NEW: {text}")
        rows.append((rel, text, old))

    with OUT_TSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["path", "text"])
        for rel, text, _old in rows:
            w.writerow([rel, text])

    print(f"\n[OK] Збережено: {OUT_TSV}")
    print("[NEXT] Відкрий файл у редакторі, виправ помилки розпізнавання,")
    print("       потім підміни train.tsv та запусти train_whisper.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
