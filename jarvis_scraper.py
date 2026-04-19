import requests
import urllib3
from urllib.parse import quote
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
import logging
import re
import certifi

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)


class JarvisScraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.verify = certifi.where()
        self.session.headers.update({
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/122.0.0.0 Safari/537.36'
            ),
            'Accept-Language': 'uk-UA,uk;q=0.9,en;q=0.8',
            'Accept': (
                'text/html,application/xhtml+xml,application/xml;'
                'q=0.9,image/webp,*/*;q=0.8'
            ),
            'Referer': 'https://www.google.com/',
        })

        retry = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET"],
            raise_on_status=False,
        )
        adapter = requests.adapters.HTTPAdapter(max_retries=retry)
        self.session.mount('http://',  adapter)
        self.session.mount('https://', adapter)

    # -------------------------------------------------------------------------
    # ВНУТРІШНІ ХЕЛПЕРИ
    # -------------------------------------------------------------------------

    def _get(self, url: str, timeout: int = 10, **kwargs) -> requests.Response:
        response = self.session.get(
            url,
            timeout=timeout,
            allow_redirects=True,
            **kwargs
        )
        response.raise_for_status()
        response.encoding = 'utf-8'
        return response

    @staticmethod
    def _parse(html: str) -> BeautifulSoup:
        return BeautifulSoup(html, 'html.parser')

    @staticmethod
    def _clean_text(text: str) -> str:
        return re.sub(r'\s+', ' ', text).strip()

    @staticmethod
    def _voice_temp(temp: str) -> str:
        """Форматує температуру для голосу: +9° → плюс 9°"""
        return temp.replace('+', 'плюс ').replace('−', 'мінус ').replace('-', 'мінус ')

    # -------------------------------------------------------------------------
    # ПОГОДА: СИНОПТИК
    # -------------------------------------------------------------------------

    def get_sinoptik_weather(self, city: str = "суми") -> str:
        # ✅ Правильна структура URL Синоптика
        url = f"https://sinoptik.ua/погода-{city}"
        logger.info(f"Запит до Синоптика: {url}")

        try:
            # ✅ verify=False + НЕ кодуємо кирилицю — сайт сам обробляє
            response = self.session.get(
                url,
                timeout=10,
                #verify=False,
                allow_redirects=True
            )
            response.raise_for_status()
            response.encoding = 'utf-8'
        except requests.exceptions.Timeout:
            return "Синоптик не відповідає (timeout)."
        except requests.exceptions.ConnectionError:
            return "Немає з'єднання з Синоптиком."
        except requests.exceptions.HTTPError as e:
            code = e.response.status_code if e.response is not None else "невідомо"
            return f"Синоптик повернув помилку: {code}."
        except Exception as e:
            logger.error(f"Невідома помилка Синоптика: {e}", exc_info=True)
            return "Метеостанція Синоптик тимчасово недоступна."

        soup = self._parse(response.text)

        # --- Поточна температура ---
        TEMP_SELECTORS = [
            ('div', {'class': 'cur-temp'}),
            ('p',   {'class': 'today-temp'}),
            ('div', {'class': 'temperature'}),
            ('span',{'class': 'temp'}),
            ('div', {'class': 'main-temp'}),
        ]
        temp_block = None
        for tag, attrs in TEMP_SELECTORS:
            temp_block = soup.find(tag, attrs)
            if temp_block:
                break

        if not temp_block:
            match = soup.find(string=re.compile(r'[−\-]?\d{1,2}\s*°'))
            if match:
                temp_block = match.parent

        if not temp_block:
            logger.warning("Синоптик: не знайдено температуру, перемикаюсь на wttr.in")
            return self.get_weather_from_wttr()

        temp_now = self._clean_text(temp_block.get_text())

        # --- Опис ---
        desc_block = soup.find('p', {'class': 'w-KCBNr0'})
        if not desc_block:
            for tag, attrs in [
                ('div', {'class': 'wDescription'}),
                ('div', {'class': 'description'}),
                ('div', {'class': 'weather-description'}),
            ]:
                desc_block = soup.find(tag, attrs)
                if desc_block:
                    break

        description = (
            self._clean_text(desc_block.get_text()).rstrip('.') if desc_block else None
        )

        # --- Мін/Макс ---
        minmax_block = soup.find('div', {'class': 'xQzkHBce'})
        temp_min = temp_max = None
        if minmax_block:
            raw   = self._clean_text(minmax_block.get_text())
            temps = re.findall(r'[+\-−]?\d{1,2}°', raw)
            if len(temps) >= 2:
                temp_min = self._voice_temp(temps[0])
                temp_max = self._voice_temp(temps[1])

        # --- Фінальна фраза ---
        parts = [f"У місті {city} зараз {self._voice_temp(temp_now)}."]
        if description:
            parts.append(f"{description}.")
        if temp_min and temp_max:
            parts.append(f"Протягом дня від {temp_min} до {temp_max}.")

        result = " ".join(parts)
        logger.info(f"Погода отримана: {result}")
        return result

    # -------------------------------------------------------------------------
    # ПОГОДА: WTTR.IN
    # -------------------------------------------------------------------------

    def get_weather_from_wttr(self, city: str = "Sumy") -> str:
        url = f"https://wttr.in/{city}?format=%t|%P|%w|%h|%C&lang=uk"
        logger.info(f"Запит до wttr.in: {url}")

        try:
            response = self._get(url, headers={'User-Agent': 'curl/7.81.0'})
        except requests.exceptions.Timeout:
            return "Служба wttr.in не відповідає (timeout)."
        except requests.exceptions.ConnectionError:
            return "Немає з'єднання з wttr.in."
        except Exception as e:
            logger.error(f"Помилка wttr.in: {e}", exc_info=True)
            return "Служба погоди wttr.in тимчасово недоступна."

        parts = response.text.strip().split('|')
        if len(parts) < 5:
            logger.error(f"wttr.in: неочікувана відповідь: {response.text[:80]}")
            return "Служба погоди повернула некоректні дані."

        temp, pressure_hpa, wind, humidity, condition = [p.strip() for p in parts]

        temp     = self._voice_temp(temp)
        wind     = wind.replace('km/h', ' кілометрів за годину')
        humidity = humidity.replace('%', ' відсотків')

        try:
            p_val        = int(re.sub(r'\D', '', pressure_hpa))
            pressure_str = f"{int(p_val * 0.75006)} міліметрів ртутного стовпчика"
        except ValueError:
            pressure_str = pressure_hpa

        result = (
            f"Погода у {city}: {condition}. "
            f"Температура: {temp}. "
            f"Атмосферний тиск: {pressure_str}. "
            f"Вітер: {wind}. "
            f"Вологість: {humidity}."
        )
        logger.info(f"Погода з wttr.in: {result}")
        return result

    # -------------------------------------------------------------------------
    # НОВИНИ: УКРІНФОРМ
    # -------------------------------------------------------------------------

    def get_ukrinform_news(self, limit: int = 5) -> str:
        url = "https://www.ukrinform.ua/"
        logger.info("Запит до Укрінформу")

        try:
            response = self._get(url)
        except requests.exceptions.Timeout:
            return "Укрінформ не відповідає (timeout)."
        except requests.exceptions.ConnectionError:
            return "Немає з'єднання з Укрінформом."
        except Exception as e:
            logger.error(f"Помилка Укрінформу: {e}", exc_info=True)
            return "Стрічка новин тимчасово недоступна."

        soup = self._parse(response.text)

        # ✅ Нові селектори: посилання з /rubric-* в href
        raw_items = soup.find_all('a', href=re.compile(r'^/rubric-[a-z]+/\d+'))

        if not raw_items:
            logger.warning("Укрінформ: жоден селектор не спрацював.")
            return "Стрічка новин тимчасово недоступна."

        titles = []
        seen   = set()
        for item in raw_items:
            text = self._clean_text(item.get_text())
            if 15 <= len(text) <= 220 and text not in seen:
                seen.add(text)
                titles.append(text)
            if len(titles) >= limit:
                break

        if not titles:
            logger.warning("Укрінформ: заголовки не пройшли фільтрацію.")
            return "Стрічка новин тимчасово недоступна."

        result = "Останні новини. " + ". ".join(
            f"{i + 1}. {t}" for i, t in enumerate(titles)
        )
        logger.info(f"Новини отримані: {len(titles)} позицій.")
        return result
    # -------------------------------------------------------------------------
    # ДІАГНОСТИКА
    # -------------------------------------------------------------------------

    def test_scrapers(self) -> None:
        divider = "-" * 60
        tests = [
            ("Синоптик",         lambda: self.get_sinoptik_weather("суми")),
            ("Укрінформ",        lambda: self.get_ukrinform_news()),
            ("wttr.in (резерв)", lambda: self.get_weather_from_wttr("Sumy")),
        ]
        print("🧪 ТЕСТУВАННЯ SCRAPERS")
        print(divider)
        for i, (name, fn) in enumerate(tests, 1):
            print(f"{i}️⃣  {name}:")
            try:
                print(fn())
            except Exception as e:
                print(f"❌ Критична помилка: {e}")
            print()
        print(divider)
        print("✅ Тестування завершено.")


# =============================================================================
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(levelname)s - [%(funcName)s] - %(message)s'
    )
    JarvisScraper().test_scrapers()