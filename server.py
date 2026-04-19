import os
import sys
import asyncio
import logging
import threading
import queue
import psutil
import uvicorn

from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from main import JarvisUltimate
import config
from dotenv import load_dotenv
load_dotenv()

# ---------------------------------------------------------------------------
# ЛОГУВАННЯ
# ---------------------------------------------------------------------------
logging.basicConfig(
    filename='jarvis_server.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [%(funcName)s] - %(message)s',
    encoding='utf-8'
)
logger = logging.getLogger(__name__)

jarvis: JarvisUltimate | None = None

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
            dead = []
            for client in self._clients:
                try:
                    await client.send_json(data)
                except Exception as e:
                    logger.warning(f"Помилка надсилання клієнту {client.client}: {e}")
                    dead.append(client)
            self._clients = [c for c in self._clients if c not in dead]

manager = ConnectionManager()

# ---------------------------------------------------------------------------
# Черга подій від Jarvis → WebSocket
# ---------------------------------------------------------------------------
event_queue: queue.Queue = queue.Queue()

def ui_callback(sender: str, text: str) -> None:
    """Викликається з потоків Jarvis — кладе подію в чергу"""
    event_queue.put({"sender": sender, "text": text})

async def event_broadcaster() -> None:
    """Фоновий таск — розсилає події всім клієнтам"""
    loop = asyncio.get_running_loop()
    while True:
        try:
            msg = await loop.run_in_executor(
                None,
                lambda: event_queue.get(timeout=0.05)
            )
            await manager.broadcast({"type": "message", **msg})
        except queue.Empty:
            pass
        except Exception:
            await asyncio.sleep(0.1)

async def metrics_broadcaster() -> None:
    """Метрики системи — раз на секунду"""
    while True:
        if not jarvis_ready.is_set():
            await asyncio.sleep(1.0)
            continue
        battery = psutil.sensors_battery()
        vol     = getattr(jarvis.audio, "current_volume", 0) if jarvis else 0

        await manager.broadcast({
            "type":    "metrics",
            "cpu":     psutil.cpu_percent(interval=None),
            "ram":     psutil.virtual_memory().percent,
            "battery": battery.percent if battery else None,
            "plugged": battery.power_plugged if battery else None,
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
async def send_command(body: CommandBody):
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

# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
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
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)