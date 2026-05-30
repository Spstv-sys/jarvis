"""Audio features — лёгкий екстрактор акустичних метрик для core.affect.

Працює з готовим WAV (16-bit PCM) — байтами або шляхом до файлу.
Без зовнішніх залежностей крім numpy і stdlib `wave`. Не претендує на якість
повноцінного DSP — це **дешеві проксі**, достатні щоб годувати Affect-движок:

    energy       — нормалізована RMS-енергія голосу       (0..1)
    speech_rate  — пропорційно до zero-crossing-rate       (0..1, ~0.5 норма)
    jitter       — нестабільність енергії між фреймами     (0..1, високий → стрес)
    pitch_var    — нестабільність ZCR між фреймами (proxy F0 var) (0..1)

Усе вираховується по фреймах ~30 мс. Тиша автоматично відфільтровується по
порогу енергії — інакше pause-фрейми ламали б jitter/pitch_var.

API:
    feats = extract_features_from_bytes(wav_bytes)
    feats = extract_features_from_path(path)
    # → {"energy": .., "speech_rate": .., "jitter": .., "pitch_var": ..,
    #    "duration_sec": .., "voiced_ratio": ..}
"""
from __future__ import annotations

import io
import wave
import logging
from typing import Dict

import numpy as np

logger = logging.getLogger(__name__)

# Параметри фреймування. 30 мс — стандартний компроміс для voice activity.
_FRAME_MS = 30
# Поріг RMS (на нормалізованому сигналі -1..+1), нижче якого фрейм вважається тишею.
_VOICED_RMS_THRESH = 0.012
# Нормалізатори (підібрані емпірично під 16 kHz мовлення).
_ENERGY_REF      = 0.18    # RMS ~0.18 → energy=1.0
_ZCR_NORM        = 0.18    # ZCR ~0.18 → speech_rate=1.0
_JITTER_NORM     = 0.6     # CV(енергії) ~0.6 → jitter=1.0
_PITCH_VAR_NORM  = 0.4     # std(ZCR) ~0.4 → pitch_var=1.0


# ---------------------------------------------------------------------------
def extract_features_from_bytes(wav_bytes: bytes) -> Dict[str, float]:
    """Прочитати WAV з пам'яті й порахувати фічі. {} при помилці."""
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            return _extract(wf)
    except Exception as e:
        logger.debug(f"[audio_features] read bytes failed: {e}")
        return {}


def extract_features_from_path(path: str) -> Dict[str, float]:
    """Прочитати WAV з диска й порахувати фічі. {} при помилці."""
    try:
        with wave.open(path, "rb") as wf:
            return _extract(wf)
    except Exception as e:
        logger.debug(f"[audio_features] read path failed: {e}")
        return {}


# ---------------------------------------------------------------------------
def _extract(wf: "wave.Wave_read") -> Dict[str, float]:
    sr = wf.getframerate()
    n_channels = wf.getnchannels()
    sampwidth = wf.getsampwidth()
    n_frames = wf.getnframes()
    if n_frames <= 0 or sr <= 0:
        return {}

    raw = wf.readframes(n_frames)
    if not raw:
        return {}

    # Підтримуємо лише 16-bit PCM (формат, у якому Whisper-пайплайн пише WAV).
    if sampwidth != 2:
        logger.debug(f"[audio_features] unsupported sampwidth={sampwidth}")
        return {}

    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if n_channels > 1:
        samples = samples.reshape(-1, n_channels).mean(axis=1)

    duration = float(samples.size) / float(sr)
    if samples.size < int(0.1 * sr):  # < 100 мс — не рахуємо нічого
        return {"duration_sec": duration}

    frame_len = max(1, int(sr * _FRAME_MS / 1000))
    n_full = samples.size // frame_len
    if n_full < 2:
        # Замало фреймів для статистики — повертаємо лише сумарну енергію.
        rms = float(np.sqrt(np.mean(samples ** 2)))
        return {
            "duration_sec": duration,
            "energy": _clip01(rms / _ENERGY_REF),
            "voiced_ratio": 1.0 if rms > _VOICED_RMS_THRESH else 0.0,
        }
    frames = samples[: n_full * frame_len].reshape(n_full, frame_len)

    # RMS на фрейм.
    rms_per_frame = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-12)
    voiced_mask = rms_per_frame > _VOICED_RMS_THRESH
    voiced_ratio = float(voiced_mask.mean())

    if voiced_mask.sum() < 2:
        # Майже тиша — повертаємо мінімум.
        return {
            "duration_sec": duration,
            "energy": _clip01(float(rms_per_frame.mean()) / _ENERGY_REF),
            "voiced_ratio": voiced_ratio,
        }

    voiced_frames = frames[voiced_mask]
    voiced_rms = rms_per_frame[voiced_mask]

    # Zero-crossing rate на фрейм (тільки voiced).
    signs = np.signbit(voiced_frames)
    zcr_per_frame = np.mean(signs[:, 1:] != signs[:, :-1], axis=1)

    # Метрики.
    mean_rms = float(voiced_rms.mean())
    energy   = _clip01(mean_rms / _ENERGY_REF)

    mean_zcr = float(zcr_per_frame.mean())
    speech_rate = _clip01(mean_zcr / _ZCR_NORM)

    # Jitter: коефіцієнт варіації RMS (std/mean) — нестабільність гучності.
    cv_rms = float(voiced_rms.std() / (mean_rms + 1e-9))
    jitter = _clip01(cv_rms / _JITTER_NORM)

    # Pitch variance proxy: розкид ZCR між voiced фреймами.
    pitch_var = _clip01(float(zcr_per_frame.std()) / _PITCH_VAR_NORM)

    return {
        "duration_sec": duration,
        "voiced_ratio": voiced_ratio,
        "energy": energy,
        "speech_rate": speech_rate,
        "jitter": jitter,
        "pitch_var": pitch_var,
    }


def _clip01(x: float) -> float:
    if x < 0.0: return 0.0
    if x > 1.0: return 1.0
    return float(x)
