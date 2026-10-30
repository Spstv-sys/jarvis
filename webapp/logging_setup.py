"""Налаштування логування з ротацією файлів."""
from __future__ import annotations

import logging
import logging.handlers
import os
import config


def configure_logging() -> logging.Logger:
    """Налаштувати ротований файловий логер і повернути root-логер модуля.

    Поведінка ідентична попередній inline-конфігурації в ``server.py``:
    рівень INFO, файл ``jarvis_server.log`` (5 МБ × 3 бекапи, UTF-8),
    єдиний RotatingFileHandler на root-логері.
    """
    logging.basicConfig(level=logging.INFO)  # set root level first
    handler = logging.handlers.RotatingFileHandler(
        os.path.join(config.DATA_DIR, 'jarvis_server.log'),
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=3,
        encoding='utf-8',
    )
    handler.setFormatter(
        logging.Formatter('%(asctime)s - %(levelname)s - [%(funcName)s] - %(message)s')
    )
    logging.getLogger().handlers = [handler]  # replace with rotating handler
    return logging.getLogger("webapp")
