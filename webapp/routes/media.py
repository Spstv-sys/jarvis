"""Маршрути роботи з файлами: POST /upload, POST /transcribe."""
from __future__ import annotations

import os
import asyncio
import re
import time
import logging
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Request, HTTPException, UploadFile, File, Form

from webapp import state
import config
from webapp.security import enforce_access

logger = logging.getLogger(__name__)
router = APIRouter()

# ---------------------------------------------------------------------------
# File upload
# ---------------------------------------------------------------------------
_UPLOAD_DIR = Path(config.DATA_DIR) / "uploads"
_UPLOAD_DIR.mkdir(exist_ok=True)
_MAX_UPLOAD_SIZE = int(os.getenv("JARVIS_MAX_UPLOAD_MB", "25")) * 1024 * 1024
_ALLOWED_EXTS = {
    ".pdf", ".docx", ".txt", ".md", ".csv", ".py", ".json", ".log",
    ".jpg", ".jpeg", ".png", ".bmp",
}


def _safe_filename(name: str) -> str:
    """Strip directory components and dangerous chars from a user-supplied filename."""
    name = os.path.basename(name or "").strip()
    # Replace anything that isn't alnum/dot/dash/underscore with _
    name = re.sub(r"[^A-Za-z0-9._\-\u0400-\u04FF]+", "_", name)
    # Disallow leading dots or empty
    name = name.lstrip(".") or "file"
    return name[:120]


@router.post("/upload")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    prompt: str = Form(""),
):
    enforce_access(request, "REST /upload")

    if not state.jarvis_ready.is_set() or state.jarvis is None:
        raise HTTPException(status_code=503, detail="Jarvis not ready")

    safe_name = _safe_filename(file.filename or "upload.bin")
    ext = os.path.splitext(safe_name)[1].lower()
    if ext not in _ALLOWED_EXTS:
        raise HTTPException(
            status_code=415,
            detail=f"Формат {ext or '?'} не підтримується. Дозволено: " + ", ".join(sorted(_ALLOWED_EXTS)),
        )

    # Streaming write with size cap to prevent memory exhaustion / DoS
    timestamp = int(time.time())
    final_name = f"{timestamp}_{uuid.uuid4().hex}_{safe_name}"
    dest = _UPLOAD_DIR / final_name
    written = 0
    try:
        with dest.open("wb") as f:
            while chunk := await file.read(1024 * 1024):  # 1 MB chunks
                written += len(chunk)
                if written > _MAX_UPLOAD_SIZE:
                    f.close()
                    dest.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=413,
                        detail=f"Файл перевищує ліміт {_MAX_UPLOAD_SIZE // (1024*1024)} МБ",
                    )
                f.write(chunk)
    finally:
        await file.close()

    abs_path = str(dest.resolve())
    user_prompt = (prompt or "").strip()
    if user_prompt:
        # User asked something specific about the file — pass both prompt and path
        command = f"{user_prompt} {abs_path}"
        chat_echo = f"📎 {safe_name} — {user_prompt}"
    else:
        # Default: ingest into RAG memory via existing skill_learn_doc trigger
        command = f"вивчи файл {abs_path}"
        chat_echo = f"📎 {safe_name} (додаю в пам'ять)"

    # Echo to chat so user sees what was attached
    state.event_queue.put({"sender": "USER", "text": chat_echo})
    state.jarvis.input_queue.put(command)
    logger.info(f"REST /upload: {safe_name} ({written} bytes) → {command!r}")
    return {
        "status":   "ok",
        "filename": safe_name,
        "size":     written,
        "path":     abs_path,
    }


# ---------------------------------------------------------------------------
# Speech-to-text (web mic)
# ---------------------------------------------------------------------------
_TRANSCRIBE_EXTS = {".wav", ".webm", ".ogg", ".oga", ".mp3", ".m4a", ".flac"}
_MAX_TRANSCRIBE_SIZE = 25 * 1024 * 1024  # 25 MB


@router.post("/transcribe")
async def transcribe_audio(request: Request, file: UploadFile = File(...)):
    enforce_access(request, "REST /transcribe", log=False)

    if not state.jarvis_ready.is_set() or state.jarvis is None:
        raise HTTPException(status_code=503, detail="Jarvis not ready")

    # Determine extension from filename or content-type
    raw_name = file.filename or "audio.webm"
    ext = os.path.splitext(raw_name)[1].lower()
    if ext not in _TRANSCRIBE_EXTS:
        # MediaRecorder зазвичай надсилає audio/webm — нормалізуємо
        ct = (file.content_type or "").lower()
        if "webm" in ct:
            ext = ".webm"
        elif "ogg" in ct:
            ext = ".ogg"
        elif "wav" in ct:
            ext = ".wav"
        elif "mpeg" in ct or "mp3" in ct:
            ext = ".mp3"
        elif "mp4" in ct or "m4a" in ct:
            ext = ".m4a"
        else:
            raise HTTPException(status_code=415, detail=f"Unsupported audio format: {ext or ct}")

    # Stream-write to temp file with size cap
    tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
    tmp_path = tmp.name
    tmp.close()
    written = 0
    try:
        with open(tmp_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > _MAX_TRANSCRIBE_SIZE:
                    raise HTTPException(status_code=413, detail="Audio file too large")
                f.write(chunk)
        await file.close()

        if written < 200:
            return {"status": "ok", "text": ""}

        # Run blocking transcribe in thread to avoid stalling the event loop
        loop = asyncio.get_running_loop()
        text = await loop.run_in_executor(None, state.jarvis.audio.transcribe_file, tmp_path)
        logger.info(f"REST /transcribe: {written} bytes → {text!r}")
        return {"status": "ok", "text": text or ""}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"REST /transcribe: помилка: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
