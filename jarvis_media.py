import logging
import urllib.parse
import requests
import re
import json
import webbrowser

logger = logging.getLogger(__name__)


class JarvisYouTube:
    _BASE_URL  = "https://www.youtube.com/results?search_query={}"
    _WATCH_URL = "https://www.youtube.com/watch?v={}"

    def __init__(self):
        # ✅ Session — одне TCP з'єднання для всіх запитів
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/122.0.0.0 Safari/537.36'
            ),
            'Accept-Language': 'uk-UA,uk;q=0.9,en-US;q=0.8,en;q=0.7',
        })

    # -------------------------------------------------------------------------
    # ВНУТРІШНІЙ ПАРСЕР
    # -------------------------------------------------------------------------

    def _fetch_youtube_data(self, query: str) -> list | None:
        """Витягує список відео з ytInitialData."""
        try:
            url      = self._BASE_URL.format(urllib.parse.quote(query))
            response = self.session.get(url, timeout=10)
            response.raise_for_status()

            # ✅ re.DOTALL — матчить JSON що містить переноси рядків
            match = re.search(
                r'ytInitialData\s*=\s*({.*?});\s*</script>',
                response.text,
                re.DOTALL
            )
            if not match:
                logger.error("ytInitialData не знайдено у відповіді YouTube.")
                return None

            data = json.loads(match.group(1))

            # ✅ Безпечний доступ через .get() — не падає при зміні структури
            contents = (
                data
                .get('contents', {})
                .get('twoColumnSearchResultsRenderer', {})
                .get('primaryContents', {})
                .get('sectionListRenderer', {})
                .get('contents', [{}])[0]
                .get('itemSectionRenderer', {})
                .get('contents', [])
            )

            if not contents:
                logger.error("YouTube: порожня структура contents.")
                return None

            return contents

        except json.JSONDecodeError as e:
            logger.error(f"YouTube: не вдалося розпарсити JSON: {e}")
            return None
        except requests.exceptions.Timeout:
            logger.error("YouTube: timeout запиту.")
            return None
        except requests.exceptions.ConnectionError:
            logger.error("YouTube: немає з'єднання.")
            return None
        except Exception as e:
            logger.error(f"YouTube: невідома помилка: {e}")
            return None

    def _extract_videos(self, contents: list, limit: int) -> list[dict]:
        """✅ Окремий метод — витягує відео з contents."""
        videos = []
        for item in contents:
            renderer = item.get('videoRenderer')
            if not renderer:
                continue

            video_id = renderer.get('videoId', '')
            title    = (
                renderer.get('title', {})
                        .get('runs', [{}])[0]
                        .get('text', 'Без назви')
            )
            channel  = (
                renderer.get('longBylineText', {})
                        .get('runs', [{}])[0]
                        .get('text', 'Невідомий канал')
            )
            duration = (
                renderer.get('lengthText', {})
                        .get('simpleText', 'Невідомо')
            )

            if video_id:
                videos.append({
                    'id':       video_id,
                    'title':    title,
                    'channel':  channel,
                    'duration': duration,
                    'url':      self._WATCH_URL.format(video_id),
                })

            if len(videos) >= limit:
                break

        return videos

    # -------------------------------------------------------------------------
    # ПУБЛІЧНІ МЕ��ОДИ
    # -------------------------------------------------------------------------

    def play_video(self, query: str) -> bool:
        """Відкриває перше відео в браузері."""
        contents = self._fetch_youtube_data(query)
        if not contents:
            return False

        videos = self._extract_videos(contents, limit=1)
        if not videos:
            logger.warning("YouTube: play_video — відео не знайдено.")
            return False

        webbrowser.open(videos[0]['url'])
        logger.info(f"YouTube: відкрито '{videos[0]['title']}'")
        return True

    def search_videos(self, query: str, limit: int = 3) -> str:
        """Повертає текст для голосового озвучення результатів пошуку."""
        contents = self._fetch_youtube_data(query)
        if not contents:
            return "Не вдалося отримати результати з YouTube."

        videos = self._extract_videos(contents, limit=limit)
        if not videos:
            return "Відео за цим запитом не знайдено."

        # ✅ Формат без \n — speak() не озвучує переноси буквально
        parts = [f"Знайдено {len(videos)} результати на YouTube за запитом {query}."]
        for i, v in enumerate(videos, 1):
            # Тривалість: "4:32" → "4 хвилини 32 секунди" для голосу
            duration_voice = self._format_duration(v['duration'])
            parts.append(
                f"{i}. «{v['title']}». "
                f"Канал: {v['channel']}. "
                f"Тривалість: {duration_voice}."
            )

        result = " ".join(parts)
        logger.info(f"YouTube пошук '{query}': знайдено {len(videos)} відео.")
        return result

    # -------------------------------------------------------------------------
    # ХЕЛПЕРИ
    # -------------------------------------------------------------------------

    @staticmethod
    def _format_duration(duration: str) -> str:
        """✅ '4:32' → '4 хвилини 32 секунди' для природного озвучення"""
        try:
            parts = duration.strip().split(':')
            if len(parts) == 2:
                m, s = int(parts[0]), int(parts[1])
                return f"{m} хвилин {s} секунд"
            elif len(parts) == 3:
                h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
                return f"{h} годин {m} хвилин {s} секунд"
        except Exception:
            pass
        return duration  # Якщо не вдалось — повертаємо як є