"""JARVIS API — точка входу.

Раніше тут жив увесь сервер (~740 рядків). Тепер логіка розкладена по пакету
:mod:`webapp` (стан, безпека, події, життєвий цикл, маршрути), а цей файл лише:

1. вантажить ``.env`` ДО того, як ``webapp.security`` прочитає ``JARVIS_API_KEY``;
2. налаштовує ротоване логування;
3. складає FastAPI-застосунок через :func:`webapp.factory.create_app`;
4. піднімає uvicorn при прямому запуску.
"""
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from webapp.logging_setup import configure_logging
configure_logging()

from webapp.factory import create_app

app = create_app()


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("API_PORT", 8001))
    uvicorn.run(app, host="0.0.0.0", port=port, reload=False)
