import os
import sys
import time
import asyncio
import logging
import logging.handlers
import threading
import queue
import tempfile
import psutil
import uvicorn
from collections import defaultdict

import re
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from main import JarvisUltimate
import config
from dotenv import load_dotenv
load_dotenv()

# ---------------------------------------------------------------------------
# ЛОГУВАННЯ з ротацією
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)   # set root level first
_log_handler = logging.handlers.RotatingFileHandler(
    'jarvis_server.log',
    maxBytes=5 * 1024 * 1024,  # 5 MB
    backupCount=3,
    encoding='utf-8',
)
_log_handler.setFormatter(
    logging.Formatter('%(asctime)s - %(levelname)s - [%(funcName)s] - %(message)s')
)
logging.getLogger().handlers = [_log_handler]   # replace with rotating handler
logger = logging.getLogger(__name__)

jarvis: JarvisUltimate | None = None

# ---------------------------------------------------------------------------
# API-key auth + per-IP rate limiting for POST /command
# ---------------------------------------------------------------------------
_JARVIS_API_KEY  = os.getenv("JARVIS_API_KEY")
_RATE_LIMIT      = int(os.getenv("JARVIS_RATE_LIMIT", "30"))   # requests per window
_RATE_WINDOW     = 60                                            # seconds
_rate_counters: dict[str, list[float]] = defaultdict(list)
_rate_lock = threading.Lock()


def _check_rate_limit(client_ip: str) -> bool:
    """Return True if the request is within the rate limit, False otherwise.

    Also prunes expired entries for *all* tracked IPs to prevent unbounded
    memory growth when many unique addresses hit the server over time.
    """
    now = time.monotonic()
    with _rate_lock:
        # Prune expired entries for all IPs to prevent memory leak
        stale_ips = [
            ip for ip, ts_list in _rate_counters.items()
            if not any(now - t < _RATE_WINDOW for t in ts_list)
        ]
        for ip in stale_ips:
            del _rate_counters[ip]

        recent = [t for t in _rate_counters[client_ip] if now - t < _RATE_WINDOW]
        if len(recent) >= _RATE_LIMIT:
            _rate_counters[client_ip] = recent
            return False
        recent.append(now)
        _rate_counters[client_ip] = recent
    return True

# ---------------------------------------------------------------------------
# WebSocket менеджер
# ---------------------------------------------------------------------------
class ConnectionManager:
    def __init__(self):
        self._clients: list[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._clients.append(ws)
        logger.info(f"WebSocket клієнт підключився: {ws.client}. Всього клієнтів: {len(self._clients)}")

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients = [c for c in self._clients if c != ws]
        logger.info(f"WebSocket клієнт відключився: {ws.client}. Залишилось клієнтів: {len(self._clients)}")

    async def broadcast(self, data: dict) -> None:
        async with self._lock:
            if not self._clients:
                return
            # Паралельний send + видалення мертвих за один прохід.
            results = await asyncio.gather(
                *(c.send_json(data) for c in self._clients),
                return_exceptions=True,
            )
            self._clients = [
                c for c, r in zip(self._clients, results)
                if not isinstance(r, Exception)
            ]

manager = ConnectionManager()

# ---------------------------------------------------------------------------
# Черга подій від Jarvis → WebSocket
# ---------------------------------------------------------------------------
event_queue: queue.Queue = queue.Queue()

def ui_callback(sender: str, text: str) -> None:
    """Викликається з потоків Jarvis — кладе подію в чергу"""
    event_queue.put({"sender": sender, "text": text})


def _thought_to_event(t) -> dict:
    """Серіалізує Thought в dict для WS-broadcast'у."""
    return {
        "_kind": "thought",
        "ts":    t.ts,
        "stage": t.stage,
        "text":  t.text,
        "meta":  t.meta,
    }


def _on_thought(t) -> None:
    """Підписник ThoughtLog — публікує думку в чергу подій."""
    try:
        event_queue.put(_thought_to_event(t))
    except Exception:
        pass


def _on_goal_stack(snap: dict) -> None:
    """Підписник GoalStack — публікує snapshot стеку."""
    try:
        event_queue.put({"_kind": "goal_stack", **snap})
    except Exception:
        pass


def _on_reflection(snap: dict) -> None:
    """Підписник Reflector — публікує оновлені уроки."""
    try:
        event_queue.put({"_kind": "reflection", **snap})
    except Exception:
        pass


def _on_anticipator(snap: dict) -> None:
    """Підписник Anticipator — публікує проактивні пропозиції."""
    try:
        event_queue.put({"_kind": "anticipator", **snap})
    except Exception:
        pass

async def event_broadcaster() -> None:
    """Фоновий таск — розсилає події всім клієнтам"""
    loop = asyncio.get_running_loop()
    while True:
        try:
            msg = await loop.run_in_executor(
                None,
                lambda: event_queue.get(timeout=0.05)
            )
            if msg.get("_kind") == "thought":
                msg.pop("_kind", None)
                await manager.broadcast({"type": "thought", **msg})
            elif msg.get("_kind") == "goal_stack":
                msg.pop("_kind", None)
                await manager.broadcast({"type": "goal_stack", **msg})
            elif msg.get("_kind") == "reflection":
                msg.pop("_kind", None)
                await manager.broadcast({"type": "reflection", **msg})
            elif msg.get("_kind") == "anticipator":
                msg.pop("_kind", None)
                await manager.broadcast({"type": "anticipator", **msg})
            else:
                await manager.broadcast({"type": "message", **msg})
        except queue.Empty:
            pass
        except Exception:
            await asyncio.sleep(0.1)

async def cognition_broadcaster() -> None:
    """Live snapshot WM + intent stats — раз на 2 с (тільки якщо є зміни)."""
    last_sig = None
    while True:
        await asyncio.sleep(2.0)
        if not jarvis_ready.is_set() or jarvis is None:
            continue
        try:
            wm = getattr(jarvis, "working_memory", None)
            ctx = wm.context_block() if wm else ""
            ents = []
            if wm and hasattr(wm, "entities"):
                try:
                    ents = [e.__dict__ for e in wm.entities()][:8]
                except Exception:
                    ents = []
            ir = getattr(jarvis, "intent_router", None)
            ir_stats = ir.stats() if ir else {}
            sig = (ctx, len(ents), tuple(sorted(ir_stats.items())) if ir_stats else ())
            if sig == last_sig:
                continue
            last_sig = sig
            await manager.broadcast({
                "type": "cognition",
                "wm_context": ctx,
                "wm_entities": ents,
                "intent_stats": ir_stats,
            })
        except Exception:
            pass


async def metrics_broadcaster() -> None:
    """Метрики системи — раз на секунду"""
    # Кеш cpu_percent: перший виклик interval=None повертає 0.0,
    # подальші — середнє від останнього виклику. Оскільки ми створюємо
    # броадкаст раз на секунду, це OK. Просто виносимо виклики всіх сенсорів
    # у thread pool, щоб не блокувати event loop.
    loop = asyncio.get_running_loop()

    def _gather_metrics():
        battery = psutil.sensors_battery()
        return {
            "cpu":     psutil.cpu_percent(interval=None),
            "ram":     psutil.virtual_memory().percent,
            "battery": battery.percent if battery else None,
            "plugged": battery.power_plugged if battery else None,
        }

    while True:
        if not jarvis_ready.is_set():
            await asyncio.sleep(1.0)
            continue
        metrics = await loop.run_in_executor(None, _gather_metrics)
        vol = getattr(jarvis.audio, "current_volume", 0) if jarvis else 0
        await manager.broadcast({
            "type":    "metrics",
            **metrics,
            "volume":  float(vol),
        })
        await asyncio.sleep(1.0)

# ---------------------------------------------------------------------------
# Запуск Jarvis у фоновому потоці
# ---------------------------------------------------------------------------
jarvis_ready = threading.Event()

def start_jarvis() -> None:
    global jarvis
    try:
        jarvis = JarvisUltimate()
        jarvis.ui_callback = ui_callback
        jarvis.start_ears()
        jarvis.start_mouth()
        jarvis.audio.play_fx("startup")
        jarvis.enqueue_speech(
            f"Всі системи в нормі. Поточний інтелект: {jarvis.llm.active_provider}."
        )
        event_queue.put({"sender": "STATUS", "text": "SYSTEM ONLINE"})
        logger.info("SYSTEM ONLINE — Jarvis успішно запущено.")
    except Exception as e:
        event_queue.put({"sender": "STATUS", "text": f"INIT ERROR: {e}"})
        logger.error(f"INIT ERROR — Jarvis не вдалося запустити: {e}", exc_info=True)
    finally:
        jarvis_ready.set()

    if jarvis:
        jarvis.start_processing_loop()

# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    threading.Thread(target=start_jarvis, daemon=True).start()
    asyncio.create_task(event_broadcaster())
    asyncio.create_task(metrics_broadcaster())
    asyncio.create_task(cognition_broadcaster())
    # ThoughtLog (Шар 6) — підписуємо сервер на потік думок.
    try:
        from core.thought_log import get_thought_log
        get_thought_log().subscribe(_on_thought)
        logger.info("✅ ThoughtLog subscriber підключений до WebSocket broadcast.")
    except Exception as e:
        logger.warning(f"ThoughtLog subscribe failed: {e}")
    # GoalStack (Шар 6+) — візуалізація активних планів.
    try:
        from core.goal_stack import get_goal_stack
        get_goal_stack().subscribe(_on_goal_stack)
        logger.info("✅ GoalStack subscriber підключений до WebSocket broadcast.")
    except Exception as e:
        logger.warning(f"GoalStack subscribe failed: {e}")
    # Reflector (Шар 7) — метакогнітивні уроки.
    try:
        from core.reflector import get_reflector
        refl = get_reflector()
        refl.subscribe(_on_reflection)
        # Якщо в config задано REFLECTOR_INTERVAL_SEC > 0 — стартуємо фоновий цикл.
        try:
            interval = int(getattr(config, "REFLECTOR_INTERVAL_SEC", 0))
        except Exception:
            interval = 0
        if interval > 0:
            refl.start(interval_sec=interval)
        logger.info("✅ Reflector subscriber підключений до WebSocket broadcast.")
    except Exception as e:
        logger.warning(f"Reflector subscribe failed: {e}")
    # Anticipator (Шар 8) — проактивні пропозиції.
    try:
        from core.anticipator import get_anticipator
        ant = get_anticipator()
        ant.subscribe(_on_anticipator)
        try:
            a_interval = int(getattr(config, "ANTICIPATOR_INTERVAL_SEC", 0))
        except Exception:
            a_interval = 0
        if a_interval > 0:
            ant.start(interval_sec=a_interval)
        logger.info("✅ Anticipator subscriber підключений до WebSocket broadcast.")
    except Exception as e:
        logger.warning(f"Anticipator subscribe failed: {e}")
    yield

# ---------------------------------------------------------------------------
# Створення app — один раз, з lifespan та CORS
# ---------------------------------------------------------------------------
app = FastAPI(title="JARVIS API", lifespan=lifespan)

logger.info(f"CORS allowed origins: {config.ALLOWED_ORIGINS}")
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------
class CommandBody(BaseModel):
    text: str

@app.post("/command")
async def send_command(body: CommandBody, request: Request):
    client_ip = request.client.host if request.client else None
    if client_ip is None:
        # Cannot determine client IP — reject to prevent rate-limit bypass
        raise HTTPException(status_code=400, detail="Bad Request")

    if _JARVIS_API_KEY:
        api_key = request.headers.get("X-API-Key", "")
        if api_key != _JARVIS_API_KEY:
            logger.warning(f"REST /command: неправильний API-ключ від {client_ip}")
            raise HTTPException(status_code=401, detail="Unauthorized")

    if not _check_rate_limit(client_ip):
        logger.warning(f"REST /command: ліміт запитів вичерпано для {client_ip}")
        raise HTTPException(status_code=429, detail="Too Many Requests")

    if not jarvis_ready.is_set():
        logger.warning(f"Команда відхилена (Jarvis не готовий): {body.text!r}")
        return {"status": "not_ready"}
    logger.info(f"REST /command: {body.text!r}")
    jarvis.input_queue.put(body.text)
    return {"status": "ok"}

@app.get("/status")
async def get_status():
    ready    = jarvis_ready.is_set()
    provider = jarvis.llm.active_provider if jarvis else "—"
    return {"ready": ready, "provider": provider}


@app.get("/cognition")
async def get_cognition():
    """Снепшот когнітивного стану: WM + останні думки + епізоди."""
    snapshot: dict = {"ready": jarvis_ready.is_set()}
    try:
        from core.thought_log import get_thought_log
        snapshot["thoughts"] = [t.to_dict() for t in get_thought_log().recent(50)]
    except Exception as e:
        snapshot["thoughts_error"] = str(e)

    if jarvis is not None:
        wm = getattr(jarvis, "working_memory", None)
        if wm is not None:
            try:
                snapshot["working_memory"] = {
                    "context": wm.context_block(),
                    "entities": [e.__dict__ for e in getattr(wm, "entities", lambda: [])()][:10]
                                  if hasattr(wm, "entities") else [],
                }
            except Exception as e:
                snapshot["wm_error"] = str(e)
        em = getattr(jarvis, "episodic_memory", None)
        if em is not None:
            try:
                snapshot["episodic_recent"] = [
                    {"when": e.when, "kind": e.kind, "text": e.text}
                    for e in em.recent(15)
                ]
                snapshot["episodic_stats"] = em.stats()
            except Exception as e:
                snapshot["em_error"] = str(e)
        ir = getattr(jarvis, "intent_router", None)
        if ir is not None:
            try:
                snapshot["intent_router"] = ir.stats()
            except Exception:
                pass
    return snapshot


@app.get("/cognition/history")
async def get_cognition_history(
    limit: int = 200,
    stages: str = "",
    since: float | None = None,
    until: float | None = None,
):
    """Історія думок з SQLite (за весь час)."""
    try:
        from core.thought_log import get_thought_log
        stage_list = [s.strip() for s in stages.split(",") if s.strip()] or None
        items = get_thought_log().history(limit=limit, stages=stage_list, since=since, until=until)
        return {"thoughts": [t.to_dict() for t in items], "count": len(items)}
    except Exception as e:
        return {"error": str(e), "thoughts": []}


@app.get("/goal_stack")
async def get_goal_stack_snapshot():
    """Поточний стек активних планів + остання історія."""
    try:
        from core.goal_stack import get_goal_stack
        return get_goal_stack().snapshot()
    except Exception as e:
        return {"error": str(e), "active": [], "history": []}


@app.get("/reflection")
async def get_reflection_snapshot():
    """Останні згенеровані уроки + статистика рефлексії."""
    try:
        from core.reflector import get_reflector
        return get_reflector().snapshot()
    except Exception as e:
        return {"error": str(e), "recent": [], "stats": {}}


@app.post("/reflection/run")
async def trigger_reflection(window_hours: int | None = None):
    """Ручний запуск рефлексії; повертає створені уроки."""
    try:
        from core.reflector import get_reflector
        loop = asyncio.get_running_loop()
        lessons = await loop.run_in_executor(
            None, lambda: get_reflector().reflect_now(window_hours=window_hours)
        )
        return {"lessons": [l.to_dict() for l in lessons], "count": len(lessons)}
    except Exception as e:
        return {"error": str(e), "lessons": []}


# ---------------------------------------------------------------------------
# Anticipator REST (Шар 8)
# ---------------------------------------------------------------------------
@app.get("/anticipator")
async def get_anticipator_snapshot():
    """Поточний стек проактивних пропозицій + статистика."""
    try:
        from core.anticipator import get_anticipator
        return get_anticipator().snapshot()
    except Exception as e:
        return {"error": str(e), "pending": [], "recent": [], "stats": {}}


@app.post("/anticipator/scan")
async def trigger_anticipator_scan():
    """Ручний запуск сканування; повертає нові пропозиції."""
    try:
        from core.anticipator import get_anticipator
        loop = asyncio.get_running_loop()
        new_sugs = await loop.run_in_executor(None, lambda: get_anticipator().scan_now())
        return {"new": [s.to_dict() for s in new_sugs], "count": len(new_sugs)}
    except Exception as e:
        return {"error": str(e), "new": []}


@app.post("/anticipator/{sid}/accept")
async def accept_suggestion(sid: str):
    """Користувач прийняв пропозицію. Якщо є command — виконати через jarvis."""
    try:
        from core.anticipator import get_anticipator
        sug = get_anticipator().accept(sid)
        if not sug:
            return {"ok": False, "reason": "not_found"}
        executed = False
        if sug.command and jarvis_ready.is_set() and jarvis is not None:
            try:
                jarvis.input_queue.put(sug.command)
                executed = True
            except Exception as e:
                logger.warning(f"accept_suggestion: enqueue failed: {e}")
        return {"ok": True, "suggestion": sug.to_dict(), "executed": executed}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/anticipator/{sid}/dismiss")
async def dismiss_suggestion(sid: str):
    try:
        from core.anticipator import get_anticipator
        ok = get_anticipator().dismiss(sid)
        return {"ok": ok}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ---------------------------------------------------------------------------
# File upload
# ---------------------------------------------------------------------------
_UPLOAD_DIR = Path(__file__).parent / "uploads"
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

@app.post("/upload")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    prompt: str = Form(""),
):
    client_ip = request.client.host if request.client else None
    if client_ip is None:
        raise HTTPException(status_code=400, detail="Bad Request")

    if _JARVIS_API_KEY:
        api_key = request.headers.get("X-API-Key", "")
        if api_key != _JARVIS_API_KEY:
            logger.warning(f"REST /upload: неправильний API-ключ від {client_ip}")
            raise HTTPException(status_code=401, detail="Unauthorized")

    if not _check_rate_limit(client_ip):
        logger.warning(f"REST /upload: ліміт запитів вичерпано для {client_ip}")
        raise HTTPException(status_code=429, detail="Too Many Requests")

    if not jarvis_ready.is_set():
        return {"status": "not_ready"}

    safe_name = _safe_filename(file.filename or "upload.bin")
    ext = os.path.splitext(safe_name)[1].lower()
    if ext not in _ALLOWED_EXTS:
        raise HTTPException(
            status_code=415,
            detail=f"Формат {ext or '?'} не підтримується. Дозволено: " + ", ".join(sorted(_ALLOWED_EXTS)),
        )

    # Streaming write with size cap to prevent memory exhaustion / DoS
    timestamp = int(time.time())
    final_name = f"{timestamp}_{safe_name}"
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
    event_queue.put({"sender": "USER", "text": chat_echo})
    jarvis.input_queue.put(command)
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

@app.post("/transcribe")
async def transcribe_audio(request: Request, file: UploadFile = File(...)):
    client_ip = request.client.host if request.client else None
    if client_ip is None:
        raise HTTPException(status_code=400, detail="Bad Request")

    if _JARVIS_API_KEY:
        api_key = request.headers.get("X-API-Key", "")
        if api_key != _JARVIS_API_KEY:
            raise HTTPException(status_code=401, detail="Unauthorized")

    if not _check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="Too Many Requests")

    if not jarvis_ready.is_set() or jarvis is None:
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
        text = await loop.run_in_executor(None, jarvis.audio.transcribe_file, tmp_path)
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

# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket, token: str = ""):
    """Accept WebSocket connections.

    When ``JARVIS_API_KEY`` is set, the client must pass a matching
    ``?token=<key>`` query parameter.  This is consistent with the
    authentication model used on POST /command.
    """
    if _JARVIS_API_KEY and token != _JARVIS_API_KEY:
        logger.warning(f"WebSocket: відхилено з'єднання від {ws.client} — невірний токен")
        await ws.close(code=1008)  # 1008 = Policy Violation (RFC 6455)
        return

    await manager.connect(ws)
    try:
        while True:
            data = await ws.receive_json()
            if data.get("type") == "command" and jarvis_ready.is_set():
                logger.info(f"WebSocket команда від {ws.client}: {data.get('text')!r}")
                jarvis.input_queue.put(data["text"])
    except WebSocketDisconnect:
        await manager.disconnect(ws)

# ---------------------------------------------------------------------------
# Роздаємо React build (production)
# ---------------------------------------------------------------------------
DIST = os.path.join(os.path.dirname(__file__), "ui", "web", "dist")
if os.path.exists(DIST):
    app.mount("/", StaticFiles(directory=DIST, html=True), name="static")

# ---------------------------------------------------------------------------
# Запуск
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.getenv("API_PORT", 8001))
    uvicorn.run(app, host="0.0.0.0", port=port, reload=False)