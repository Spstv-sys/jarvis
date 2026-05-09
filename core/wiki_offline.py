"""
Офлайн-доступ до Вікіпедії через локальний kiwix-serve.

Вимагає:
  1) Завантажити ZIM-файл української Вікіпедії з https://library.kiwix.org/
     (наприклад wikipedia_uk_all_nopic — ~1 GB, або _maxi з картинками ~7 GB)
  2) Завантажити kiwix-tools для Windows: https://download.kiwix.org/release/kiwix-tools/
  3) Запустити: kiwix-serve.exe --port 8080 wikipedia_uk_all_nopic.zim
  4) Прописати в .env:
        WIKI_OFFLINE_URL=http://localhost:8080
        WIKI_OFFLINE_BOOK=wikipedia_uk_all_nopic

Модуль НЕ падає, якщо сервер недоступний — просто повертає [].
"""
from __future__ import annotations

import logging
import re
from typing import List, Dict, Optional
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_html(html: str, max_len: int = 600) -> str:
    text = _TAG_RE.sub(" ", html)
    text = _WS_RE.sub(" ", text).strip()
    if len(text) > max_len:
        text = text[:max_len].rsplit(" ", 1)[0] + "…"
    return text


class WikiOffline:
    """Тонкий клієнт kiwix-serve REST API."""

    def __init__(self, base_url: str, book: str, timeout: float = 3.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.book = book
        self.timeout = timeout
        self._available: Optional[bool] = None  # ліниво кешуємо стан

    # ------------------------------------------------------------------
    def is_available(self) -> bool:
        """Перевірити, чи відповідає сервер. Кешує результат."""
        if self._available is not None:
            return self._available
        try:
            r = requests.get(f"{self.base_url}/", timeout=self.timeout)
            self._available = r.status_code < 500
        except Exception as e:
            logger.warning(f"WikiOffline: kiwix-serve недоступний ({e})")
            self._available = False
        return self._available

    # ------------------------------------------------------------------
    def search(self, query: str, limit: int = 3) -> List[Dict[str, str]]:
        """Повернути список { 'title', 'body', 'url' } з offline Вікіпедії."""
        if not query or not self.is_available():
            return []
        try:
            params = {
                "books.name": self.book,
                "pattern": query,
                "pageLength": str(limit),
            }
            r = requests.get(
                f"{self.base_url}/search", params=params, timeout=self.timeout * 2
            )
            r.raise_for_status()
            html = r.text
        except Exception as e:
            logger.warning(f"WikiOffline.search HTTP помилка: {e}")
            self._available = False
            return []

        # Парсимо посилання на статті з HTML результатів пошуку.
        # kiwix-serve рендерить <a href="/viewer#book/A/Title">Title</a>
        hrefs = re.findall(
            r'<a[^>]+href="(/viewer#[^"]+)"[^>]*>([^<]+)</a>',
            html,
        )
        seen = set()
        articles: List[tuple[str, str]] = []
        for href, title in hrefs:
            if href in seen:
                continue
            seen.add(href)
            articles.append((href, title.strip()))
            if len(articles) >= limit:
                break

        results: List[Dict[str, str]] = []
        for href, title in articles:
            # /viewer#wikipedia_uk_all_nopic/A/Україна  →  /content/wikipedia_uk_all_nopic/A/Україна
            content_path = href.replace("/viewer#", "/content/", 1)
            try:
                page = requests.get(
                    f"{self.base_url}{content_path}", timeout=self.timeout * 2
                )
                page.raise_for_status()
                snippet = _strip_html(page.text, max_len=800)
            except Exception as e:
                logger.warning(f"WikiOffline: не вдалось завантажити {content_path}: {e}")
                snippet = title
            results.append(
                {
                    "title": title,
                    "body": f"{title}. {snippet}",
                    "url": f"{self.base_url}{content_path}",
                }
            )

        if results:
            logger.info(
                f"WikiOffline: знайдено {len(results)} статей для '{query}' (offline)"
            )
        return results


# Глобальний lazy-singleton ----------------------------------------------------
_INSTANCE: Optional[WikiOffline] = None


def get_wiki_offline() -> Optional[WikiOffline]:
    """Повернути глобальний екземпляр або None, якщо не сконфігуровано."""
    global _INSTANCE
    if _INSTANCE is not None:
        return _INSTANCE
    try:
        import config  # відкладений імпорт, щоб уникнути циклів

        url = getattr(config, "WIKI_OFFLINE_URL", "") or ""
        book = getattr(config, "WIKI_OFFLINE_BOOK", "") or ""
        if not url or not book:
            return None
        _INSTANCE = WikiOffline(url, book)
        return _INSTANCE
    except Exception as e:
        logger.warning(f"get_wiki_offline init failed: {e}")
        return None
