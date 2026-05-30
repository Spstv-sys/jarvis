"""Складання FastAPI-застосунку: lifespan, CORS, маршрути, статика."""
from __future__ import annotations

import os
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import config
from webapp.lifecycle import lifespan
from webapp.routes import all_routers

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Створити та налаштувати інстанс FastAPI (один раз, з lifespan і CORS)."""
    app = FastAPI(title="JARVIS API", lifespan=lifespan)

    logger.info(f"CORS allowed origins: {config.ALLOWED_ORIGINS}")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.ALLOWED_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # REST + WebSocket маршрути.
    for router in all_routers:
        app.include_router(router)

    # Роздаємо React build (production). Монтується ОСТАННІМ — catch-all "/".
    dist = Path(__file__).resolve().parent.parent / "ui" / "web" / "dist"
    if os.path.exists(dist):
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="static")

    return app
