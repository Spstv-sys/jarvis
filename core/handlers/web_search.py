"""WebSearchMixin — веб-пошук (офлайн Вікіпедія → DDG → Wikipedia REST) і AI-чат."""
from __future__ import annotations

import re
import time
import logging
import threading

import requests
from colorama import Fore
try:
    from ddgs import DDGS
except ImportError:  # fallback на старий пакет
    from duckduckgo_search import DDGS  # type: ignore[no-redef]

import config
from core.utils import strip_triggers

logger = logging.getLogger(__name__)


class WebSearchMixin:
    # ==================================================================
    # WEB SEARCH
    # ==================================================================
    def cmd_web_search(self, cmd: str) -> str:
        logger.info(f"cmd_web_search: {cmd!r}")
        search_triggers = config.TRIGGERS.get("web_search", [])
        query = strip_triggers(cmd, search_triggers)
        if not query:
            self.audio.speak("Що саме мені знайти?")
            return "CONTINUE"
        self.audio.speak(f"Шукаю інформацію про {query}...")
        try:
            results = self._web_search(query)
            if results:
                # Якщо джерело — локальна Вікіпедія, додаємо позначку для контексту
                source_hint = "офлайн Вікіпедія" if results[0].get("url", "").startswith(
                    config.WIKI_OFFLINE_URL or "::none::"
                ) else "мережа"
                search_context = "\n".join(r['body'] for r in results)
                answer = self.llm.generate_response(
                    f"Користувач шукає: '{query}'.\n"
                    f"Джерело: {source_hint}.\n"
                    f"Ось що знайдено:\n{search_context}\n"
                    "Зроби чіткий інформативний підсумок українською мовою.",
                    is_system_call=True,
                )
                if answer:
                    self.audio.speak(answer)
                    threading.Thread(
                        target=self.brain.memorize,
                        args=(f"Довідка з мережі щодо '{query}': {answer}",),
                        kwargs={"source": "web_search"},
                        daemon=True,
                    ).start()
                    print(f"{Fore.MAGENTA}🧠 Знання завантажено у пам'ять (Web-RAG).")
                else:
                    self.audio.speak("Не вдалося сформувати відповідь.")
            else:
                self.audio.speak("На жаль, я нічого не знайшов за цим запитом.")
        except Exception as e:
            logger.error(f"Помилка пошуку: {e}", exc_info=True)
            self.audio.speak("Виникла проблема з доступом до інтернету.")
        return "CONTINUE"

    def _web_search(self, query: str) -> list:
        # 1) Офлайн Вікіпедія (якщо kiwix-serve запущено)
        try:
            from core.wiki_offline import get_wiki_offline
            wiki = get_wiki_offline()
            if wiki and wiki.is_available():
                offline = wiki.search(query, limit=3)
                if offline:
                    logger.info(
                        f"_web_search: {len(offline)} офлайн-результатів з Вікіпедії"
                    )
                    return offline
        except Exception as e:
            logger.warning(f"_web_search: offline lookup failed: {e}")

        # 2) DuckDuckGo (онлайн)
        for attempt in range(3):
            try:
                with DDGS() as ddgs:
                    results = list(
                        ddgs.text(query, max_results=3, region="ua-uk", safesearch="off", backend="lite")
                    )
                if results:
                    logger.info(f"_web_search: знайдено {len(results)} результатів для {query!r}")
                    return results
                logger.warning(f"DDGS: порожній результат (спроба {attempt + 1})")
            except Exception as e:
                logger.warning(f"DDGS спроба {attempt + 1} невдала: {e}")
            time.sleep(1.5 * (attempt + 1))

        # 3) Wikipedia REST (онлайн fallback)
        logger.warning(f"DDGS заблоковано — використовую Wikipedia для '{query}'")
        return self._wikipedia_search(query)

    def _wikipedia_search(self, query: str) -> list:
        try:
            params = {
                "action": "query", "list": "search", "srsearch": query,
                "srlimit": 3, "format": "json", "utf8": 1,
            }
            res = requests.get("https://uk.wikipedia.org/w/api.php", params=params, timeout=8)
            res.raise_for_status()
            hits = res.json().get("query", {}).get("search", [])
            if not hits:
                return []
            return [{"body": re.sub(r'<[^>]+>', '', h["snippet"]) + "..."} for h in hits]
        except Exception as e:
            logger.error(f"Wikipedia fallback помилка: {e}")
            return []

    # ==================================================================
    # AI CHAT
    # ==================================================================
    def cmd_ai_chat(self, cmd: str) -> str:
        logger.info(f"cmd_ai_chat: {cmd!r}")
        self.llm.process_chat(cmd)
        return "CONTINUE"
