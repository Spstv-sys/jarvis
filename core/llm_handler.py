import os
import re
import logging
import hashlib
import threading
import concurrent.futures
from collections import deque
from openai import OpenAI
from google import genai
from colorama import Fore

import config

logger = logging.getLogger(__name__)


class LLMHandler:
    MAX_SHORT_TERM = 20
    CACHE_MAX      = 30
    SENTENCE_END   = re.compile(r'(?<=[.!?])\s+')
    _CODE_MARKERS  = ('def ', 'class ', 'import ', 'from ', '    return ', '#!/', 'if __name__')

    def __init__(self, db, brain, audio):
        self.db    = db
        self.brain = brain
        self.audio = audio

        self.personality_mode  = "стандартний"
        self.active_provider   = "deepseek"
        self.short_term_memory = deque(maxlen=self.MAX_SHORT_TERM)
        self._response_cache: dict = {}

        print(f"{Fore.CYAN}⚙️ Ініціалізація Мовних Моделей (LLM)...")
        self._init_providers()

    # -------------------------------------------------------------------------
    # ІНІЦІАЛІЗАЦІЯ ПРОВАЙДЕРІВ
    # -------------------------------------------------------------------------
    def _init_providers(self) -> None:
        # DeepSeek
        deepseek_key = os.getenv("DEEPSEEK_API_KEY")
        if deepseek_key:
            self.deepseek_client = OpenAI(
                api_key=deepseek_key,
                base_url="https://api.deepseek.com/v1",
                timeout=30
            )
            logger.info("DeepSeek підключено.")
        else:
            logger.error("Не знайдено DEEPSEEK_API_KEY")
            self.deepseek_client = None

        # Gemini
        gemini_key = os.getenv("GEMINI_API_KEY")
        if gemini_key:
            self.gemini_client = genai.Client(api_key=gemini_key)
            logger.info("Gemini підключено.")
        else:
            logger.error("Не знайдено GEMINI_API_KEY")
            self.gemini_client = None

        # Ollama — ініціалізуємо тільки якщо сервер доступний
        self.ollama_client = None
        self.ollama_model  = "qwen2.5:latest"
        try:
            import requests
            r = requests.get("http://localhost:11434", timeout=2)
            if r.ok:
                self.ollama_client = OpenAI(
                    base_url="http://localhost:11434/v1",
                    api_key="ollama",
                    timeout=60
                )
                logger.info("Ollama підключено.")
        except Exception:
            logger.warning("Ollama недоступна — пропускаємо ��ніціалізацію.")

    # -------------------------------------------------------------------------
    # ОСОБИСТІСТЬ / ПРОВАЙДЕР
    # -------------------------------------------------------------------------
    def set_personality(self, cmd: str) -> None:
        for mode in config.AI_PROMPTS:
            if mode in cmd:
                self.personality_mode = mode
                self.audio.speak(f"Характер змінено на: {mode}.")
                return
        self.personality_mode = "стандартний"
        self.audio.speak("Характер змінено на стандартний.")

    def switch_provider(self, cmd: str) -> None:
        cmd_lower = cmd.lower()
        
        if "deepseek" in cmd_lower or "діпсік" in cmd_lower:
            if self.deepseek_client:  # ✅ Додана перевірка
                self.active_provider = "deepseek"
                self.audio.speak("Перемикаю нейромережу на хмарний DeepSeek.")
            else:
                self.audio.speak("DeepSeek недоступний. Перевірте DEEPSEEK_API_KEY у файлі .env.")
        
        elif "gemini" in cmd_lower or "джеміні" in cmd_lower:
            if self.gemini_client:  # ✅ ГОЛОВНЕ ВИПРАВЛЕННЯ!
                self.active_provider = "gemini"
                self.audio.speak("Перемикаю нейромережу на Gemini.")
            else:
                self.audio.speak("Gemini недоступний. Перевірте GEMINI_API_KEY у файлі .env.")
        
        elif "ollama" in cmd_lower or "олама" in cmd_lower or "локальн" in cmd_lower:
            if self.ollama_client:
                self.active_provider = "ollama"
                self.audio.speak("Перемикаю нейромережу на локальну базу.")
            else:
                self.audio.speak("Локальна Олама недоступна. Сервер не запущено.")
        else:
            self.audio.speak("Невідомий провайдер. Доступні: DeepSeek, Gemini, Олама.")

    def get_status(self) -> str:
        return (
            f"Активний провайдер: {self.active_provider}. "
            f"Режим характеру: {self.personality_mode}."
        )

    # -------------------------------------------------------------------------
    # КЕШ
    # -------------------------------------------------------------------------
    def _get_cached(self, prompt: str) -> str | None:
        key = hashlib.md5(prompt.encode()).hexdigest()
        return self._response_cache.get(key)

    def _set_cached(self, prompt: str, response: str) -> None:
        key = hashlib.md5(prompt.encode()).hexdigest()
        # ✅ Обрізаємо кеш якщо переповнений — видаляємо найстарший запис
        if len(self._response_cache) >= self.CACHE_MAX:
            oldest = next(iter(self._response_cache))
            del self._response_cache[oldest]
        self._response_cache[key] = response

    # -------------------------------------------------------------------------
    # ГЕНЕРАЦІЯ (одиночний запит — без стримінгу)
    # -------------------------------------------------------------------------
    def generate_response(self, prompt: str, is_system_call: bool = False) -> str | None:
        # ✅ Перевіряємо кеш — миттєва відповідь ��ля повторних запитів
        cached = self._get_cached(prompt)
        if cached:
            logger.info("LLM: відповідь з кешу.")
            return cached

        try:
            ans = self._call_provider(prompt)
            if ans is None:
                return None
            if not is_system_call:
                self.short_term_memory.append(f"AI: {ans}")
                self._set_cached(prompt, ans)
            return ans
        except Exception as e:
            logger.error(f"Помилка генерації [{self.active_provider}]: {e}")
            return None

    def _call_provider(self, prompt: str) -> str | None:
        """Звичайний (не стримінговий) виклик активного провайдера."""
        if self.active_provider == "deepseek" and self.deepseek_client:
            res = self.deepseek_client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}]
            )
            return res.choices[0].message.content

        elif self.active_provider == "ollama" and self.ollama_client:
            res = self.ollama_client.chat.completions.create(
                model=self.ollama_model,
                messages=[{"role": "user", "content": prompt}]
            )
            return res.choices[0].message.content

        elif self.active_provider == "gemini" and self.gemini_client:
            res = self.gemini_client.models.generate_content(
                model='gemini-2.0-flash',
                contents=prompt
            )
            return res.text

        else:
            logger.error(f"Провайдер '{self.active_provider}' недоступний.")
            return None

    # -------------------------------------------------------------------------
    # СТРИМІНГ + TTS ПО РЕЧЕННЯХ
    # -------------------------------------------------------------------------
    def _looks_like_code(self, text: str) -> bool:
        """Повертає True якщо текст схожий на код (містить характерні патерни)."""
        lines = text.splitlines()
        code_lines = sum(1 for l in lines if any(m in l for m in self._CODE_MARKERS))
        return code_lines >= 2

    def _stream_and_speak(self, prompt: str) -> str | None:
        """
        ✅ Стримінг токенів → розбивка на речення → TTS одразу.
        Користувач чує першу відповідь через ~0.5с замість 3-5с.
        """
        try:
            full_text = ""
            buffer    = ""

            if self.active_provider in ("deepseek", "ollama"):
                client = (
                    self.deepseek_client
                    if self.active_provider == "deepseek"
                    else self.ollama_client
                )
                model = (
                    "deepseek-chat"
                    if self.active_provider == "deepseek"
                    else self.ollama_model
                )

                if client is None:
                    logger.error(f"Клієнт {self.active_provider} недоступний.")
                    return None

                stream = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    stream=True  # ✅ Стримінг
                )

                for chunk in stream:
                    delta = chunk.choices[0].delta.content or ""
                    buffer    += delta
                    full_text += delta

                    # ✅ Щойно є повне речення — одразу озвучуємо
                    parts = self.SENTENCE_END.split(buffer)
                    if len(parts) > 1:
                        for sentence in parts[:-1]:
                            sentence = sentence.strip()
                            if sentence and not self._looks_like_code(sentence):
                                self.audio.speak(sentence)
                        buffer = parts[-1]  # залишок — неповне речення

                # ✅ Озвучуємо залишок після завершення стриму
                if buffer.strip() and not self._looks_like_code(buffer.strip()):
                    self.audio.speak(buffer.strip())

                if self._looks_like_code(full_text):
                    self.audio.speak("Відповідь містить програмний код. Результат збережено або відображено на екрані.")

            else:
                # Gemini — стандартний виклик, але озвучення по реченнях
                full_text = self._call_provider(prompt)
                if full_text:
                    sentences = self.SENTENCE_END.split(full_text)
                    for sentence in sentences:
                        if sentence.strip() and not self._looks_like_code(sentence.strip()):
                            self.audio.speak(sentence.strip())
                    if self._looks_like_code(full_text):
                        self.audio.speak("Відповідь містить програмний код. Результат збережено або відображено на екрані.")

            return full_text.strip() if full_text else None

        except Exception as e:
            logger.error(f"Помилка стримінгу [{self.active_provider}]: {e}")
            return None

    # -------------------------------------------------------------------------
    # ДІАЛОГ З ПАМ'ЯТТЮ
    # -------------------------------------------------------------------------
    def process_chat(self, user_text: str) -> None:
        self.short_term_memory.append(f"User: {user_text}")

        # ✅ brain.search() і get_recent_facts() паралельно — економить 300-500мс
        print(f"{Fore.YELLOW}🔍 Пошук контексту (паралельно)...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            future_context = pool.submit(self.brain.search, user_text, 10)
            future_facts   = pool.submit(self.db.get_recent_facts, 5)
            knowledge_context = future_context.result()
            recent_facts      = future_facts.result()

        if not knowledge_context.strip():
            knowledge_context = "У пам'яті немає прямої інформації щодо цього запиту."

        history     = "\n".join(list(self.short_term_memory)[-6:])
        role_prompt = config.AI_PROMPTS.get(
            self.personality_mode,
            config.AI_PROMPTS["стандартний"]
        )

        full_prompt = (
            f"Системна роль: {role_prompt}\n\n"
            f"КОНТЕКСТ З ПАМ'ЯТІ:\n\"\"\"{knowledge_context}\"\"\"\n\n"
            f"ДОДАТКОВІ ФАКТИ: {recent_facts}\n"
            f"ІСТОРІЯ ДІАЛОГУ:\n{history}\n\n"
            f"ЗАПИТ КОРИСТУВАЧА: {user_text}\n"
            f"ВІДПОВІДЬ (без markdown, зручна для озвучення):"
        )

        # ✅ Стримінг + озвучення по реченнях
        full_answer = self._stream_and_speak(full_prompt)

        if full_answer:
            self.short_term_memory.append(f"AI: {full_answer}")
            self._set_cached(full_prompt, full_answer)
            # ✅ Збереження у RAG у фоні — не блокує наступну команду
            threading.Thread(
                target=self._save_to_memory,
                args=(user_text, full_answer),
                daemon=True
            ).start()
        else:
            self.audio.speak("Виникла помилка при зверненні до нейромережі.")

    def _save_to_memory(self, user_text: str, ans: str) -> None:
        try:
            memory_block = (
                f"Користувач спитав: {user_text}. "
                f"Джарвіс відповів: {ans}"
            )
            self.brain.memorize(memory_block, source="auto_dialogue")
        except Exception as e:
            logger.error(f"Не вдалося зберегти репліку в RAG: {e}")