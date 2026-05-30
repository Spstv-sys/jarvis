"""MediaMixin — погода, новини, YouTube."""
from __future__ import annotations

import re
import logging

import config
from core.utils import strip_triggers

logger = logging.getLogger(__name__)


class MediaMixin:
    # ==================================================================
    # WEATHER
    # ==================================================================
    def cmd_scrape_weather(self, cmd: str) -> str:
        logger.info(f"cmd_scrape_weather: {cmd!r}")
        self.audio.speak("Збираю повне метеозведення...")
        sinoptik = self.scraper.get_sinoptik_weather(config.DEFAULT_CITY)
        wttr     = self.scraper.get_weather_from_wttr(config.DEFAULT_CITY_EN)
        if "недоступна" in sinoptik and "недоступна" in wttr:
            self.audio.speak("Всі метеосервери наразі недоступні. Спробуйте пізніше.")
            return "CONTINUE"
        if "недоступна" in sinoptik or "помилку" in sinoptik:
            self.audio.speak(wttr)
            return "CONTINUE"
        pressure = wind = humidity = None
        if "недоступна" not in wttr:
            p = re.search(r'тиск:\s*(.+?)\.', wttr)
            w = re.search(r'Вітер:\s*(.+?)\.', wttr)
            h = re.search(r'Вологість:\s*(.+?)\.', wttr)
            if p: pressure = p.group(1).strip()
            if w: wind     = w.group(1).strip()
            if h: humidity = h.group(1).strip()
        parts = [sinoptik.rstrip('.')]
        if pressure: parts.append(f"Атмосферний тиск: {pressure}.")
        if wind:     parts.append(f"Швидкість вітру: {wind}.")
        if humidity: parts.append(f"Відносна вологість: {humidity}.")
        self.audio.speak(" ".join(parts))
        return "CONTINUE"

    def cmd_weather(self, cmd: str) -> str:
        logger.info(f"cmd_weather: {cmd!r}")
        self.audio.speak("Запитую метеозведення...")
        report = self.scraper.get_weather_from_wttr("Sumy")
        self.audio.speak(report)
        return "CONTINUE"

    # ==================================================================
    # NEWS
    # ==================================================================
    def cmd_scrape_news(self, cmd: str) -> str:
        logger.info(f"cmd_scrape_news: {cmd!r}")
        self.audio.speak("Сканую стрічку Укрінформу...")
        result = self.scraper.get_ukrinform_news(limit=5)
        self.audio.speak(result)
        return "CONTINUE"

    # ==================================================================
    # YOUTUBE
    # ==================================================================
    def cmd_play_youtube(self, cmd: str) -> str:
        logger.info(f"cmd_play_youtube: {cmd!r}")
        query = strip_triggers(cmd, config.STOP_WORDS_YOUTUBE_PLAY)
        if not query:
            self.audio.speak("Що саме мені увімкнути?")
            return "CONTINUE"
        self.audio.speak(f"Виконую. Запускаю {query} в браузері.")
        return self._open_youtube_video(query)

    def _open_youtube_video(self, query: str) -> str:
        try:
            success = self.youtube.play_video(query)
            if not success:
                self.audio.speak("На жаль, за цим запитом нічого не знайдено.")
        except Exception as e:
            logger.error(f"Помилка доступу до YouTube: {e}")
            self.audio.speak("Виникла технічна помилка доступу до відеоплеєра.")
        return "CONTINUE"

    def cmd_search_youtube(self, cmd: str) -> str:
        logger.info(f"cmd_search_youtube: {cmd!r}")
        query = strip_triggers(cmd, config.STOP_WORDS_YOUTUBE_SEARCH)
        if not query:
            self.audio.speak("Яку тему відео мені знайти?")
            return "CONTINUE"
        self.audio.speak(f"Шукаю на YouTube: {query}...")
        try:
            raw_data = self.youtube.search_videos(query)
            if not raw_data or "помилка" in raw_data.lower():
                self.audio.speak("На жаль, за цим запитом нічого не знайдено.")
            else:
                self.audio.speak(raw_data)
        except Exception as e:
            logger.error(f"Помилка YouTube пошуку: {e}")
            self.audio.speak("Виникла помилка доступу до YouTube.")
        return "CONTINUE"
