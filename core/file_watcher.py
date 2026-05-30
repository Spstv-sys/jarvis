"""File watcher — автосортування завантажень.

Винесено з main.py у межах рефакторингу (розвантаження god-file).
``DownloadHandler`` слухає теку Downloads і розкладає нові файли по
підтеках цільового диска за розширенням. Кожне сортування виконується
у фоновому потоці, щоб не блокувати watchdog-observer.
"""
from __future__ import annotations

import os
import time
import shutil
import logging
import threading

from watchdog.events import FileSystemEventHandler

import config

logger = logging.getLogger(__name__)


class DownloadHandler(FileSystemEventHandler):
    EXT_MAP = {
        'Documents': ['.pdf', '.docx', '.txt', '.xlsx', '.pptx'],
        'Images':    ['.jpg', '.jpeg', '.png', '.svg'],
        'Code':      ['.py', '.kt', '.js', '.go', '.json', '.cpp', '.html', '.css'],
        'Archives':  ['.zip', '.rar', '.7z'],
        'Torrents':  ['.torrent'],
        'Programs':  ['.exe', '.msi'],
    }

    def __init__(self):
        super().__init__()
        self.target_drive = config.SORTING_DRIVE

    def on_created(self, event):
        if event.is_directory:
            return
        # Винесено у фоновий потік: ``time.sleep(2)`` блокував би observer
        # і накопичував чергу подій при швидкому надходженні файлів.
        threading.Thread(
            target=self._sort_file,
            args=(event.src_path,),
            daemon=True,
            name="DownloadSorter",
        ).start()

    def _sort_file(self, file_path: str) -> None:
        filename = os.path.basename(file_path)
        file_ext = os.path.splitext(filename)[1].lower()

        for folder, extensions in self.EXT_MAP.items():
            if file_ext in extensions:
                dest_dir = os.path.join(self.target_drive, folder)
                os.makedirs(dest_dir, exist_ok=True)
                # Невелика затримка щоб дочекатися завершення запису файла
                # (download tools створюють .tmp/.part і дописують пізніше).
                time.sleep(2)
                try:
                    shutil.move(file_path, os.path.join(dest_dir, filename))
                    logger.info(f"Файл {filename} відсортовано у {dest_dir}")
                except (OSError, shutil.Error) as e:
                    logger.error(f"Помилка сортування файлу {filename}: {e}")
                break
