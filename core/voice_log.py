"""Voice-log (Фаза 6) — журнал живих голосових команд для замкненого циклу.

Кожна розпізнана команда зберігається як WAV + рядок у index.jsonl
(час, файл, текст, маршрут). Потім `review_voice_log.py` дає переглянути
помилки й долити виправлені кліпи в train.tsv — і модель доб'є саме свої
реальні слабкі місця.

Усе захищено try/except: журнал НІКОЛИ не ламає розпізнавання.
Вимикач: config.VOICE_LOG_ENABLED.
"""
from __future__ import annotations

import os
import time
import json
import glob
import logging

import config

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _enabled() -> bool:
    return getattr(config, "VOICE_LOG_ENABLED", True) is not False


def _dir() -> str:
    d = getattr(config, "VOICE_LOG_DIR", "voice_logs")
    return d if os.path.isabs(d) else os.path.join(_PROJECT_ROOT, d)


def log_utterance(text: str, wav=None, route: str | None = None) -> None:
    """Записати одну команду в журнал.

    Args:
        text:  розпізнаний текст (порожній — ігнор).
        wav:   bytes WAV-аудіо АБО шлях до wav-файлу (опційно).
        route: ключ маршруту, якщо відомий (опційно).
    """
    if not _enabled() or not text or not text.strip():
        return
    try:
        d = _dir()
        os.makedirs(d, exist_ok=True)
        ts = time.time()
        name = ""
        if isinstance(wav, (bytes, bytearray)):
            name = f"{int(ts * 1000)}.wav"
            with open(os.path.join(d, name), "wb") as f:
                f.write(wav)
        elif isinstance(wav, str) and os.path.exists(wav):
            import shutil
            name = f"{int(ts * 1000)}.wav"
            shutil.copy(wav, os.path.join(d, name))
        row = {
            "ts": ts,
            "iso": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)),
            "wav": name,
            "text": text.strip(),
            "route": route,
        }
        with open(os.path.join(d, "index.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        _enforce_retention(d)
    except Exception as e:
        logger.debug(f"voice_log: не вдалося записати: {e}")


def _enforce_retention(d: str) -> None:
    """Тримати не більше VOICE_LOG_MAX wav-файлів (видаляємо найстаріші)."""
    try:
        cap = int(getattr(config, "VOICE_LOG_MAX", 1000))
        wavs = sorted(glob.glob(os.path.join(d, "*.wav")), key=os.path.getmtime)
        for old in wavs[:-cap] if cap > 0 else []:
            try:
                os.remove(old)
            except OSError:
                pass
    except Exception:
        pass
