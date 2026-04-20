import os
import re
import logging
import hashlib
import threading
import concurrent.futures
from collections import deque
from colorama import Fore

import config
from core.providers import DeepSeekProvider, GeminiProvider, OllamaProvider
from core.providers.base import BaseLLMProvider

logger = logging.getLogger(__name__)


class LLMHandler:
    MAX_SHORT_TERM = 20
    CACHE_MAX      = 30
    SENTENCE_END   = re.compile(r'(?<=[.!?])\s+')
    _CODE_MARKERS  = ('def ', 'class ', 'import ', 'from ', 'return ', '#!/', 'if __name__')
    _CODE_SPOKEN_MSG = "Відповідь містить програмний код. Результат збережено або відображено на екрані."

    def __init__(self, db, brain, audio):
        self.db    = db
        self.brain = brain
        self.audio = audio

        self.personality_mode  = "стандартний"
        self.active_provider   = "deepseek"
        self.short_term_memory = deque(maxlen=self.MAX_SHORT_TERM)
        self._response_cache: dict = {}
        self._cache_lock       = threading.Lock()

        print(f"{Fore.CYAN}⚙️ Ініціалізація Мовних Моделей (LLM)...")
        self._providers: dict[str, BaseLLMProvider] = {}
        self._init_providers()

    # -------------------------------------------------------------------------
    # ІНІЦІАЛІЗАЦІЯ ПРОВАЙДЕРІВ
    # -------------------------------------------------------------------------
    def _init_providers(self) -> None:
        deepseek_key = os.getenv("DEEPSEEK_API_KEY")
        if deepseek_key:
            self._providers["deepseek"] = DeepSeekProvider(api_key=deepseek_key)
            logger.info("DeepSeek підключено.")
        else:
            logger.error("Не знайдено DEEPSEEK_API_KEY")

        gemini_key = os.getenv("GEMINI_API_KEY")
        if gemini_key:
            self._providers["gemini"] = GeminiProvider(api_key=gemini_key)
            logger.info("Gemini підключено.")
        else:
            logger.error("Не знайдено GEMINI_API_KEY")

        try:
            import requests as _req
            r = _req.get("http://localhost:11434", timeout=2)
            if r.ok:
                self._providers["ollama"] = OllamaProvider()
                logger.info("Ollama підключено.")
        except Exception:
            logger.warning("Ollama недоступна — пропускаємо ініціалізацію.")

    def _get_active_provider(self) -> BaseLLMProvider | None:
        return self._providers.get(self.active_provider)

    # -------------------------------------------------------------------------
    # Backward-compatibility properties
    # -------------------------------------------------------------------------
    @property
    def gemini_client(self):
        """Expose the raw genai.Client for Vision API calls."""
        p = self._providers.get("gemini")
        if p is not None and p.available:
            return p.raw_client
        return None

    @property
    def deepseek_client(self):
        p = self._providers.get("deepseek")
        return p.raw_client if p is not None else None

    @property
    def ollama_client(self):
        p = self._providers.get("ollama")
        return p.raw_client if p is not None else None

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
            if "deepseek" in self._providers:
                self.active_provider = "deepseek"
                self.audio.speak("Перемикаю нейромережу на хмарний DeepSeek.")
            else:
                self.audio.speak("DeepSeek недоступний. Перевірте DEEPSEEK_API_KEY у файлі .env.")
        elif "gemini" in cmd_lower or "джеміні" in cmd_lower:
            if "gemini" in self._providers:
                self.active_provider = "gemini"
                self.audio.speak("Перемикаю нейромережу на Gemini.")
            else:
                self.audio.speak("Gemini недоступний. Перевірте GEMINI_API_KEY у файлі .env.")
        elif "ollama" in cmd_lower or "олама" in cmd_lower or "локальн" in cmd_lower:
            if "ollama" in self._providers:
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
        with self._cache_lock:
            return self._response_cache.get(key)

    def _set_cached(self, prompt: str, response: str) -> None:
        key = hashlib.md5(prompt.encode()).hexdigest()
        with self._cache_lock:
            if len(self._response_cache) >= self.CACHE_MAX:
                oldest = next(iter(self._response_cache))
                del self._response_cache[oldest]
            self._response_cache[key] = response

    # -------------------------------------------------------------------------
    # ГЕНЕРАЦІЯ
    # -------------------------------------------------------------------------
    def generate_response(self, prompt: str, is_system_call: bool = False) -> str | None:
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
        provider = self._get_active_provider()
        if provider is None:
            logger.error(f"Провайдер '{self.active_provider}' недоступний.")
            return None
        return provider.generate(prompt)

    # -------------------------------------------------------------------------
    # СТРИМІНГ + TTS
    # -------------------------------------------------------------------------
    def _looks_like_code(self, text: str) -> bool:
        lines = text.splitlines()
        code_lines = sum(1 for line in lines if any(m in line for m in self._CODE_MARKERS))
        return code_lines >= 2

    def _stream_and_speak(self, prompt: str) -> str | None:
        try:
            provider = self._get_active_provider()
            if provider is None:
                logger.error(f"Клієнт {self.active_provider} недоступний.")
                return None

            full_text = ""
            buffer    = ""

            for delta in provider.stream(prompt):
                buffer    += delta
                full_text += delta

                parts = self.SENTENCE_END.split(buffer)
                if len(parts) > 1:
                    for sentence in parts[:-1]:
                        sentence = sentence.strip()
                        if sentence and not self._looks_like_code(sentence):
                            self.audio.speak(sentence)
                    buffer = parts[-1]

            if buffer.strip() and not self._looks_like_code(buffer.strip()):
                self.audio.speak(buffer.strip())

            if self._looks_like_code(full_text):
                self.audio.speak(self._CODE_SPOKEN_MSG)

            return full_text.strip() if full_text else None

        except Exception as e:
            logger.error(f"Помилка стримінгу [{self.active_provider}]: {e}")
            return None

    # -------------------------------------------------------------------------
    # ДІАЛОГ З ПАМ'ЯТТЮ
    # -------------------------------------------------------------------------
    def process_chat(self, user_text: str) -> None:
        self.short_term_memory.append(f"User: {user_text}")

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

        full_answer = self._stream_and_speak(full_prompt)

        if full_answer:
            self.short_term_memory.append(f"AI: {full_answer}")
            self._set_cached(full_prompt, full_answer)
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
