"""webapp — модульний бекенд JARVIS API.

Раніше весь сервер жив у одному ``server.py`` (~740 рядків). Тут він
розкладений на фокусні модулі:

* :mod:`webapp.state`          — спільний рантайм-стан (jarvis, черга, менеджер WS);
* :mod:`webapp.logging_setup`  — налаштування ротованого логування;
* :mod:`webapp.security`       — API-ключ + per-IP rate limiting;
* :mod:`webapp.ws_manager`     — ConnectionManager для WebSocket;
* :mod:`webapp.events`         — черга подій і фонові broadcaster-и;
* :mod:`webapp.lifecycle`      — запуск Jarvis у потоці + FastAPI lifespan;
* :mod:`webapp.factory`        — складання FastAPI-застосунку;
* :mod:`webapp.routes`         — REST та WebSocket маршрути.

``server.py`` лишається тонкою точкою входу.
"""
