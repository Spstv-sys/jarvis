"""
Train a custom 'Neo' wake-word model for openWakeWord.

USAGE
-----
1. Create a SEPARATE virtual environment (do NOT touch Jarvis venv):

    python -m venv .venv-train
    .venv-train\\Scripts\\activate

2. Install dependencies:

    pip install -r requirements-train.txt

3. Run:

    python train_neo.py

Output: ./output/neo.onnx  →  copy into
    .venv\\Lib\\site-packages\\openwakeword\\resources\\models\\neo.onnx
then set OWW_MODEL_NAME=neo in .env or config.py.

GPU is strongly recommended (NVIDIA, >=6 GB VRAM, CUDA 12.x).
On CPU expect ~10-20 hours; on a modern GPU ~30-90 min.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# CONFIG — edit these to customise the wake-word
# ---------------------------------------------------------------------------
TARGET_PHRASE = "Neo"          # English transliteration (TTS quality is best)
ALT_PHRASES   = ["Hey Neo", "Neo one", "Neo wake"]  # extra positives to harden
MODEL_NAME    = "neo"
OUTPUT_DIR    = Path(__file__).parent / "output"
TRAINING_DIR  = Path(__file__).parent / "training_data"

N_POSITIVES   = 10_000          # synthetic samples with the wake-word
N_NEGATIVES   = 30_000          # samples without it (background, speech, music)
N_VAL         = 1_000
TRAIN_STEPS   = 50_000
BATCH_SIZE    = 128
LEARNING_RATE = 1e-4
TARGET_FALSE_POSITIVES_PER_HOUR = 0.5  # tunes accept threshold

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    TRAINING_DIR.mkdir(parents=True, exist_ok=True)

    try:
        import torch
        import openwakeword
        from openwakeword.train import train_model_from_config_file
        from openwakeword.data import generate_synthetic_clips_with_piper
    except ImportError as e:
        print(f"[ERROR] Missing dependency: {e}")
        print("Run: pip install -r requirements-train.txt")
        sys.exit(1)

    if not torch.cuda.is_available():
        print("[WARNING] CUDA not available — training will be VERY slow on CPU.")
        print("          Consider using Google Colab (free T4 GPU): see README.md")
        ans = input("Continue on CPU anyway? [y/N]: ").strip().lower()
        if ans != "y":
            sys.exit(0)
    else:
        print(f"[OK] GPU detected: {torch.cuda.get_device_name(0)}")

    # ---------------------------------------------------------------------
    # 1. Generate synthetic positive samples via Piper TTS
    # ---------------------------------------------------------------------
    print(f"\n[1/3] Generating {N_POSITIVES} positive samples for '{TARGET_PHRASE}'...")
    pos_dir = TRAINING_DIR / "positive"
    pos_dir.mkdir(exist_ok=True)

    phrases = [TARGET_PHRASE] + ALT_PHRASES
    generate_synthetic_clips_with_piper(
        phrases=phrases,
        output_dir=str(pos_dir),
        n_samples_per_phrase=N_POSITIVES // len(phrases),
        # Piper voices to randomise speaker characteristics
        voices=["en_US-lessac-medium", "en_US-amy-medium", "en_GB-alan-medium"],
        # Apply pitch/speed/noise augmentation automatically
        augment=True,
    )

    # ---------------------------------------------------------------------
    # 2. Download negative datasets if not already present
    # ---------------------------------------------------------------------
    print(f"\n[2/3] Preparing {N_NEGATIVES} negative samples...")
    neg_dir = TRAINING_DIR / "negative"
    neg_dir.mkdir(exist_ok=True)
    if not any(neg_dir.iterdir()):
        print("    Downloading FMA + AudioSet (this may take 30-60 min, ~12 GB)...")
        openwakeword.utils.download_negative_datasets(
            output_dir=str(neg_dir),
            n_samples=N_NEGATIVES,
        )
    else:
        print("    Negative samples already present — skipping download.")

    # ---------------------------------------------------------------------
    # 3. Train
    # ---------------------------------------------------------------------
    print(f"\n[3/3] Training {MODEL_NAME}.onnx ({TRAIN_STEPS} steps)...")
    config = {
        "model_name":            MODEL_NAME,
        "target_phrase":         TARGET_PHRASE,
        "training_data_path":    str(TRAINING_DIR),
        "output_dir":            str(OUTPUT_DIR),
        "n_positive_samples":    N_POSITIVES,
        "n_negative_samples":    N_NEGATIVES,
        "n_validation_samples":  N_VAL,
        "training_steps":        TRAIN_STEPS,
        "batch_size":            BATCH_SIZE,
        "learning_rate":         LEARNING_RATE,
        "target_fp_per_hour":    TARGET_FALSE_POSITIVES_PER_HOUR,
        "export_onnx":           True,
        "export_tflite":         False,
    }
    train_model_from_config_file(config)

    out_path = OUTPUT_DIR / f"{MODEL_NAME}.onnx"
    if out_path.exists():
        size_mb = out_path.stat().st_size / 1024 / 1024
        print(f"\n✅ DONE — {out_path}  ({size_mb:.2f} MB)")
        print("\nNext steps:")
        print(f"  1. Copy {out_path} into:")
        print(r"     .venv\Lib\site-packages\openwakeword\resources\models\neo.onnx")
        print(f"  2. Set OWW_MODEL_NAME=neo in .env or config.py")
        print(f"  3. Restart Jarvis and say 'Neo' :)")
    else:
        print("\n[ERROR] Training finished but output file is missing. Check logs.")
        sys.exit(1)


if __name__ == "__main__":
    main()
