"""
Train a custom "Нео" wake-word model using openWakeWord 0.6 — Windows compatible.

USAGE
-----
1. Separate venv (already created by setup):
    .venv-train\\Scripts\\activate

2. Run:
    python train_neo.py

Output: ./output/neo.onnx
    → Copy into ..\\..\\.venv\\Lib\\site-packages\\openwakeword\\resources\\models\\neo.onnx
    → Add  OWW_MODEL_NAME=neo  to .env
    → Restart Jarvis and say "Нео"

How it works (Windows-compatible pipeline):
  Step 1 — Positive clips:  edge_tts (Ukrainian + English voices, 5 rates)
  Step 2 — Validation data: pre-computed 11h negative features from HuggingFace (~100 MB)
  Step 3 — Negative clips:  FMA-small music dataset via HuggingFace datasets (~0.5 h)
  Step 4 — Embed:           openwakeword AudioFeatures  (GPU used if available)
  Step 5 — Train:           openwakeword Model.auto_train()
  Step 6 — Export:          ONNX

GPU recommended (NVIDIA >=6 GB VRAM + CUDA 12).  On CPU expect ~3-8 h.
"""
from __future__ import annotations

# Disable torch.compile/dynamo before any torch import — prevents speechbrain lazy-module
# inspection crash on Windows (k2, transformers stubs not needed).
import os
os.environ.setdefault("TORCHDYNAMO_DISABLE", "1")
os.environ.setdefault("TORCH_COMPILE_DISABLE", "1")


def _patch_speechbrain_lazy_modules() -> None:
    """Patch speechbrain LazyModule so inspect.hasattr checks don't trigger ImportError.

    torch._dynamo calls inspect.getmodule which calls hasattr(module, '__file__') on
    every entry in sys.modules. speechbrain's LazyModule.__getattr__ tries to load the
    lazy target on ANY attribute access, including '__file__', and raises ImportError when
    optional deps (k2, transformers, numba) are missing. This patch makes those dunder
    attribute lookups return None instead of crashing.
    """
    try:
        from speechbrain.utils.importutils import LazyModule
        _orig = LazyModule.__getattr__

        def _safe_getattr(self: object, attr: str) -> object:
            if attr.startswith("__") and attr.endswith("__"):
                try:
                    return _orig(self, attr)
                except (ImportError, AttributeError, Exception):
                    return None
            return _orig(self, attr)

        LazyModule.__getattr__ = _safe_getattr  # type: ignore[method-assign]
    except Exception:
        pass


_patch_speechbrain_lazy_modules()

import asyncio
import io
import logging
import sys
import urllib.request
from pathlib import Path

import numpy as np
import torch
import torchaudio

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CONFIG — edit here to customise the wake-word
# ---------------------------------------------------------------------------
TARGET_PHRASES: list[str] = [
    "Нео",
    "Hey Neo",
    "Neo",
    "Нео, слухай",
    "Нео, до роботи",
    "Neo, wake up",
]
TTS_VOICES: list[str] = [
    "uk-UA-OstapNeural",
    "uk-UA-PolinaNeural",
    "en-US-JennyNeural",
    "en-US-GuyNeural",
    "en-US-AriaNeural",
    "en-GB-RyanNeural",
]
TTS_RATES: list[str] = ["-20%", "-10%", "+0%", "+10%", "+20%"]
N_REPEATS: int = 20           # random-noise copies per phrase/voice/rate combo
NEG_HOURS: float = 3.0        # hours of negative audio (was 0.5 — caused overfit)

CLIP_DURATION:  float = 1.28  # seconds — openWakeWord standard window
SAMPLE_RATE:    int   = 16_000
CLIP_SAMPLES:   int   = int(CLIP_DURATION * SAMPLE_RATE)  # 20 480

TRAIN_STEPS:    int   = 10_000  # was 20_000 — reduced to prevent overfitting
FP_VAL_BATCH:   int   = 2_048  # FP-validation DataLoader batch size (fit 6 GB VRAM)
MODEL_NAME:     str   = "neo"

OUTPUT_DIR   = Path(__file__).parent / "output"
TRAINING_DIR = Path(__file__).parent / "training_data"
FEATURE_DIR  = TRAINING_DIR / "features"

VALIDATION_URL  = (
    "https://huggingface.co/datasets/davidscripka/openwakeword_features"
    "/resolve/main/validation_set_features.npy"
)
VALIDATION_FILE = TRAINING_DIR / "validation_set_features.npy"

# ---------------------------------------------------------------------------
# 1. Positive clip generation with edge_tts
# ---------------------------------------------------------------------------

async def _tts_to_numpy(text: str, voice: str, rate: str) -> np.ndarray | None:
    """Generate one TTS clip → 16 kHz mono float32 ndarray."""
    try:
        import edge_tts  # type: ignore[import]
        communicate = edge_tts.Communicate(text, voice, rate=rate)
        chunks: list[bytes] = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                chunks.append(chunk["data"])
        if not chunks:
            return None
        buf = io.BytesIO(b"".join(chunks))
        wav, sr = torchaudio.load(buf, format="mp3")
        if sr != SAMPLE_RATE:
            wav = torchaudio.functional.resample(wav, sr, SAMPLE_RATE)
        return wav.mean(dim=0).numpy().astype(np.float32)
    except Exception as exc:
        logger.debug("TTS failed [%s / %s / %s]: %s", text, voice, rate, exc)
        return None


def _pad_or_centre(audio: np.ndarray, n: int) -> np.ndarray:
    if len(audio) >= n:
        start = (len(audio) - n) // 2
        return audio[start: start + n]
    pad = n - len(audio)
    return np.pad(audio, (pad // 2, pad - pad // 2), mode="constant")


def _load_user_recordings() -> list[np.ndarray]:
    """Load real microphone recordings produced by record_neo.py (if any)."""
    user_file = TRAINING_DIR / "user_clips.npy"
    if not user_file.exists():
        return []
    arr = np.load(str(user_file))
    # Pad/trim to CLIP_SAMPLES just in case
    fixed = [_pad_or_centre(c.astype(np.float32), CLIP_SAMPLES) for c in arr]
    logger.info("    + %d real microphone recordings from %s",
                len(fixed), user_file.name)
    return fixed


def generate_positive_clips() -> list[np.ndarray]:
    """Generate TTS clips for all phrase / voice / rate combinations."""
    pos_cache = TRAINING_DIR / "positive_clips.npy"
    if pos_cache.exists():
        logger.info("[1/5] Loading cached positive clips …")
        arr = np.load(str(pos_cache), mmap_mode="r")
        logger.info("    Loaded %d positive clips.", len(arr))
        return list(arr)

    combos = [
        (phrase, voice, rate)
        for phrase in TARGET_PHRASES
        for voice in TTS_VOICES
        for rate in TTS_RATES
    ]
    total_expected = len(combos) * N_REPEATS
    logger.info("[1/5] Generating ~%d positive clips via edge_tts …", total_expected)

    async def _run_all() -> list[np.ndarray]:
        results: list[np.ndarray] = []
        for phrase, voice, rate in combos:
            audio = await _tts_to_numpy(phrase, voice, rate)
            if audio is None:
                continue
            padded = _pad_or_centre(audio, CLIP_SAMPLES)
            for _ in range(N_REPEATS):
                # Tiny additive noise prevents exact-duplicate embeddings
                noisy = padded + np.random.randn(CLIP_SAMPLES).astype(np.float32) * 5e-4
                results.append(np.clip(noisy, -1.0, 1.0))
        return results

    clips = asyncio.run(_run_all())
    logger.info("    Generated %d positive clips from TTS.", len(clips))

    # Add real microphone recordings (heavily weighted via duplication x4)
    user_clips = _load_user_recordings()
    for clip in user_clips:
        for _ in range(4):
            noisy = clip + np.random.randn(CLIP_SAMPLES).astype(np.float32) * 5e-4
            clips.append(np.clip(noisy, -1.0, 1.0))

    logger.info("    Total positives: %d", len(clips))
    TRAINING_DIR.mkdir(parents=True, exist_ok=True)
    np.save(str(pos_cache), np.array(clips, dtype=np.float32))
    return clips


# ---------------------------------------------------------------------------
# 2. Validation features (11 h pre-computed negatives from HuggingFace)
# ---------------------------------------------------------------------------

def download_validation_features() -> None:
    if VALIDATION_FILE.exists():
        logger.info("[2/5] Validation features already present — skipping.")
        return
    TRAINING_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("[2/5] Downloading validation features from HuggingFace (~100 MB) …")

    def _progress(block: int, block_size: int, total: int) -> None:
        done = block * block_size
        pct  = min(100, done * 100 // max(total, 1))
        mb   = done // (1024 * 1024)
        tmb  = total // (1024 * 1024)
        print(f"\r    {pct:3d}%  ({mb} / {tmb} MB)", end="", flush=True)

    urllib.request.urlretrieve(VALIDATION_URL, str(VALIDATION_FILE), _progress)
    print()
    logger.info("    Saved → %s", VALIDATION_FILE)


# ---------------------------------------------------------------------------
# 3. Negative clips (FMA-small via HuggingFace datasets, ~0.5 h)
# ---------------------------------------------------------------------------

def get_negative_clips(n_hours: float = 0.5) -> list[np.ndarray]:
    """Return a list of 1.28-s float32 arrays (negative / no wake-word)."""
    neg_cache = TRAINING_DIR / "negative_clips.npy"
    if neg_cache.exists():
        logger.info("[3/5] Loading cached negative clips …")
        arr = np.load(str(neg_cache), mmap_mode="r")
        return list(arr)

    n_needed = int(n_hours * 3_600 / CLIP_DURATION)
    logger.info("[3/5] Downloading ~%d negative clips from FMA-small …", n_needed)

    clips: list[np.ndarray] = []
    try:
        import datasets as hf  # type: ignore[import]
        from tqdm import tqdm  # type: ignore[import]

        fma = hf.load_dataset("rudraml/fma", name="small", split="train", streaming=True)
        fma = iter(fma.cast_column("audio", hf.Audio(sampling_rate=SAMPLE_RATE)))

        for row in tqdm(fma, total=n_needed, desc="    FMA"):
            audio: np.ndarray = row["audio"]["array"].astype(np.float32)
            for start in range(0, len(audio) - CLIP_SAMPLES, CLIP_SAMPLES):
                clips.append(audio[start: start + CLIP_SAMPLES])
                if len(clips) >= n_needed:
                    break
            if len(clips) >= n_needed:
                break
    except Exception as exc:
        logger.warning("FMA download failed: %s — using white-noise fallback.", exc)
        clips = [
            np.random.randn(CLIP_SAMPLES).astype(np.float32) * 0.05
            for _ in range(n_needed)
        ]

    TRAINING_DIR.mkdir(parents=True, exist_ok=True)
    np.save(str(neg_cache), np.array(clips, dtype=np.float32))
    logger.info("    Saved %d negative clips.", len(clips))
    return clips


# ---------------------------------------------------------------------------
def build_negative_features_from_validation(n_frames: int) -> str:
    """Build negative training features by sliding a window over the 11h
    pre-computed validation features. Returns path to .npy file (N, n_frames, 96).

    This avoids the FMA download step entirely and uses real, diverse audio
    (~480k frames ≈ 11 hours of speech/music/noise embeddings).
    """
    FEATURE_DIR.mkdir(parents=True, exist_ok=True)
    feat_path = FEATURE_DIR / "negative_features.npy"
    lbl_path  = FEATURE_DIR / "negative_labels.npy"

    if feat_path.exists():
        logger.info("    negative features already computed — skipping.")
        return str(feat_path)

    logger.info("[3-4/5] Building negative features from validation set …")
    raw = np.load(str(VALIDATION_FILE))
    if raw.dtype != np.float32:
        raw = raw.astype(np.float32)
    logger.info("    val features: shape=%s", raw.shape)

    # Reserve the FIRST 30 % for FP-validation (held out from training)
    fp_split = int(raw.shape[0] * 0.30)
    train_part = raw[fp_split:]
    logger.info("    train part shape: %s (~%.1f h)",
                train_part.shape, train_part.shape[0] * 0.08 / 3600)

    # Build non-overlapping windows for training negatives
    n_windows = train_part.shape[0] // n_frames
    train_windows = train_part[: n_windows * n_frames].reshape(n_windows, n_frames, 96)
    # Also add a stride-2 set for diversity
    from numpy.lib.stride_tricks import sliding_window_view
    sw = sliding_window_view(train_part, (n_frames, 96))[:, 0, :, :]
    extra = np.ascontiguousarray(sw[::n_frames // 2 if n_frames > 1 else 1])
    all_neg = np.concatenate([train_windows, extra[: len(train_windows)]], axis=0)
    np.random.shuffle(all_neg)
    all_neg = all_neg.astype(np.float16)

    np.save(str(feat_path), all_neg)
    np.save(str(lbl_path),  np.zeros(len(all_neg), dtype=np.int32))
    logger.info("    negative features → shape %s (~%.1f MB)",
                all_neg.shape, all_neg.nbytes / (1024 * 1024))
    return str(feat_path)


# ---------------------------------------------------------------------------
# 4. Embed & save features
# ---------------------------------------------------------------------------

def embed_and_save(clips: list[np.ndarray], label: int, prefix: str) -> str:
    """Embed audio clips with AudioFeatures and return path to .npy features file."""
    FEATURE_DIR.mkdir(parents=True, exist_ok=True)
    feat_path = FEATURE_DIR / f"{prefix}_features.npy"
    lbl_path  = FEATURE_DIR / f"{prefix}_labels.npy"

    if feat_path.exists():
        logger.info("    %s features already computed — skipping.", prefix)
        return str(feat_path)

    from openwakeword.utils import AudioFeatures  # type: ignore[import]

    oww_device = "gpu" if torch.cuda.is_available() else "cpu"
    af = AudioFeatures(device=oww_device)

    # AudioFeatures.embed_clips expects int16 PCM audio (not float32)
    arr_f32 = np.stack(clips, axis=0)                                        # (N, CLIP_SAMPLES)
    arr_i16 = (arr_f32 * 32767).clip(-32768, 32767).astype(np.int16)
    logger.info("    Embedding %d %s clips on %s …", len(clips), prefix, oww_device)
    features = af.embed_clips(arr_i16, batch_size=256)    # (N, frames, 96)
    features = features.astype(np.float16)

    np.save(str(feat_path), features)
    np.save(str(lbl_path),  np.full(len(clips), label, dtype=np.int32))
    logger.info("    %s → shape %s", feat_path.name, features.shape)
    return str(feat_path)


# ---------------------------------------------------------------------------
# 5. Train & export
# ---------------------------------------------------------------------------

def train_and_export(pos_feat_path: str, neg_feat_path: str) -> None:
    from openwakeword.train import Model  # type: ignore[import]

    logger.info("[5/5] Training model …")

    pos_arr = np.load(pos_feat_path).astype(np.float32)  # (N, frames, 96)
    neg_arr = np.load(neg_feat_path).astype(np.float32)

    n_frames: int = pos_arr.shape[1]   # typically 16 for 1.28-s clips
    logger.info("    input_shape = (%d, 96)", n_frames)

    # ---- Validation set (accuracy / recall) — 10 % split ----
    pos_split = max(1, len(pos_arr) // 10)
    neg_split = max(1, len(neg_arr) // 10)
    pos_val,   pos_train = pos_arr[:pos_split], pos_arr[pos_split:]
    neg_val,   neg_train = neg_arr[:neg_split], neg_arr[neg_split:]

    val_X = torch.from_numpy(np.vstack([pos_val, neg_val]))
    val_y = torch.from_numpy(
        np.hstack([np.ones(len(pos_val)), np.zeros(len(neg_val))]).astype(np.float32)
    )
    X_val = torch.utils.data.DataLoader(
        torch.utils.data.TensorDataset(val_X, val_y),
        batch_size=len(val_y),
    )

    # ---- False-positive validation (held-out 30 % of pre-computed negatives) ----
    # Use stride_tricks for an O(1) sliding-window view (no per-window copy).
    FP_STRIDE = 4   # ~25 % overlap; 4× less memory than stride=1
    logger.info("    Loading FP-validation features …")
    val_fp_raw = np.load(str(VALIDATION_FILE))
    if val_fp_raw.dtype != np.float32:
        val_fp_raw = val_fp_raw.astype(np.float32)
    # Hold out FIRST 30 % so it never overlaps with training negatives
    fp_split = int(val_fp_raw.shape[0] * 0.30)
    val_fp_raw = val_fp_raw[:fp_split]
    logger.info("    val_fp_raw shape = %s, dtype = %s",
                val_fp_raw.shape, val_fp_raw.dtype)

    from numpy.lib.stride_tricks import sliding_window_view
    # sliding_window_view gives shape (T-n_frames+1, n_frames, 96) — zero-copy
    windows_view = sliding_window_view(val_fp_raw, (n_frames, 96))[:, 0, :, :]
    val_fp_windows = np.ascontiguousarray(windows_view[::FP_STRIDE])
    val_fp_labels = np.zeros(len(val_fp_windows), dtype=np.float32)
    logger.info("    val_fp_windows = %s  (~%.1f MB)",
                val_fp_windows.shape,
                val_fp_windows.nbytes / (1024 * 1024))

    X_val_fp = torch.utils.data.DataLoader(
        torch.utils.data.TensorDataset(
            torch.from_numpy(val_fp_windows),
            torch.from_numpy(val_fp_labels),
        ),
        batch_size=FP_VAL_BATCH,   # small batches to fit 6 GB VRAM
    )

    # ---- Training batch generator ----
    def _make_generator(
        pos: np.ndarray,
        neg: np.ndarray,
        batch_size: int = 128,
        n_pos: int = 16,
    ):
        """Infinite generator of (x, y) tensors mixing positives and negatives."""
        n_neg = batch_size - n_pos
        p_idx = np.random.permutation(len(pos))
        n_idx = np.random.permutation(len(neg))
        pi = ni = 0
        while True:
            # Wrap-around positive indices
            if pi + n_pos > len(p_idx):
                p_idx = np.random.permutation(len(pos))
                pi = 0
            # Wrap-around negative indices
            if ni + n_neg > len(n_idx):
                n_idx = np.random.permutation(len(neg))
                ni = 0

            x = np.vstack([pos[p_idx[pi: pi + n_pos]], neg[n_idx[ni: ni + n_neg]]])
            y = np.hstack([np.ones(n_pos), np.zeros(n_neg)]).astype(np.float32)
            perm = np.random.permutation(batch_size)

            yield torch.from_numpy(x[perm]), torch.from_numpy(y[perm])
            pi += n_pos
            ni += n_neg

    X_train = _make_generator(pos_train, neg_train)

    # ---- Model ----
    model = Model(n_classes=1, input_shape=(n_frames, 96), model_type="dnn")

    best_model = model.auto_train(
        X_train=X_train,
        X_val=X_val,
        false_positive_val_data=X_val_fp,
        steps=TRAIN_STEPS,
        target_fp_per_hour=0.5,
    )

    # auto_train returns the inner Net (best checkpoint).  export_to_onnx is
    # a method on the Model wrapper, so attach the best Net back onto `model`.
    if best_model is not None:
        model.model = best_model

    # ---- Export ----
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    onnx_path = str(OUTPUT_DIR / f"{MODEL_NAME}.onnx")
    model.export_to_onnx(onnx_path)

    size_mb = Path(onnx_path).stat().st_size / 1024 / 1024
    logger.info("\n✅ DONE — %s  (%.2f MB)", onnx_path, size_mb)
    logger.info("\nNext steps:")
    logger.info("  1. Copy %s  into:", onnx_path)
    logger.info(r"     ..\..\\.venv\Lib\site-packages\openwakeword\resources\models\neo.onnx")
    logger.info("  2. Add  OWW_MODEL_NAME=neo  to .env  (or config.py)")
    logger.info("  3. Restart Jarvis and say  'Нео'  :)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    logger.info("=== Нео Wake-Word Trainer (openwakeword 0.6, Windows) ===")
    if torch.cuda.is_available():
        logger.info("GPU: %s", torch.cuda.get_device_name(0))
    else:
        logger.info("GPU: not detected — training on CPU (expect 3-8 h)")
        ans = input("Continue on CPU? [y/N]: ").strip().lower()
        if ans != "y":
            sys.exit(0)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    TRAINING_DIR.mkdir(parents=True, exist_ok=True)

    # 1 — Positive clips
    pos_clips = generate_positive_clips()
    if not pos_clips:
        logger.error("No positive clips generated. Check internet connection.")
        sys.exit(1)

    # 2 — Validation features (also used as negative source)
    download_validation_features()

    # 3 — Embed positives only (negatives come from validation features)
    logger.info("[4/5] Embedding positive clips …")
    pos_feat = embed_and_save(pos_clips, label=1, prefix="positive")

    # 4 — Build negative features from the 11 h validation set (no FMA download)
    # Determine n_frames from the just-saved positive features
    n_frames = int(np.load(pos_feat, mmap_mode="r").shape[1])
    neg_feat = build_negative_features_from_validation(n_frames)

    # 5 — Train & export
    train_and_export(pos_feat, neg_feat)


if __name__ == "__main__":
    main()
