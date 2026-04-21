"""
Shared logging configuration for Jarvis modules.

Usage::
    from core.log import setup_rotating_logger
    setup_rotating_logger('jarvis_system.log')
"""
import logging
import logging.handlers


def setup_rotating_logger(
    log_file: str,
    level: int = logging.INFO,
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 3,
) -> None:
    """Configure the root logger with a size-rotating file handler.

    Args:
        log_file:     Path to the log file.
        level:        Root logging level (default: INFO).
        max_bytes:    Maximum file size before rotation (default: 5 MB).
        backup_count: Number of rotated backup files to keep (default: 3).
    """
    logging.basicConfig(level=level)
    handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding='utf-8',
    )
    handler.setFormatter(
        logging.Formatter('%(asctime)s - %(levelname)s - [%(funcName)s] - %(message)s')
    )
    logging.getLogger().handlers = [handler]
