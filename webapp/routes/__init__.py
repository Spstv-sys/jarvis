"""REST та WebSocket маршрути JARVIS API, згруповані за доменом."""
from __future__ import annotations

from webapp.routes.commands import router as commands_router
from webapp.routes.cognition import router as cognition_router
from webapp.routes.anticipator import router as anticipator_router
from webapp.routes.media import router as media_router
from webapp.routes.websocket import router as websocket_router

# Порядок важливий лише для самодокументації; префіксів немає — шляхи
# повністю збігаються з історичним монолітним server.py.
all_routers = [
    commands_router,
    cognition_router,
    anticipator_router,
    media_router,
    websocket_router,
]
