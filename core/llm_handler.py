import os
import re
import logging
import threading
import concurrent.futures
from collections import OrderedDict, deque
from colorama import Fore

import config
from core.providers import DeepSeekProvider, GeminiProvider, OllamaProvider, OpenAIProvider
from core.providers.base import BaseLLMProvider

logger = logging.getLogger(__name__)


class LLMHandler:
    MAX_SHORT_TERM = 20
    CACHE_MAX      = 30

    # Sentence splitter:
    #   - splits on . ! ? : ; followed by whitespace, OR on a newline
    #   - lookbehind requires a lowercase/Cyrillic letter (>=2 chars) before the
    #     terminator so common Ukrainian abbreviations like "м.", "вул.", "р.",
    #     "т.зв." do not trigger a split mid-sentence.
    SENTENCE_END = re.compile(
        r'(?<=[а-яіїєґa-z]{2}[.!?:;])\s+|\n+',
        re.IGNORECASE,
    )

    # How much text to buffer at the start of a stream before deciding whether
    # the response is code (and should NOT be spoken sentence-by-sentence).
    _CODE_DETECT_BUFFER = 250

    _CODE_MARKERS  = ('def ', 'class ', 'import ', 'from ', 'return ', '#!/', 'if __name__', '```')
    _CODE_SPOKEN_MSG = "Відповідь містить програмний код. Результат збережено або відображено на екрані."

    # ------------------------------------------------------------------
    # Markdown / emoji sanitizer for TTS.
    # Removes things that Edge-TTS reads literally ("зірочка зірочка", etc.).
    # ------------------------------------------------------------------
    _RE_CODE_FENCE   = re.compile(r"```[\s\S]*?```")
    _RE_INLINE_CODE  = re.compile(r"`([^`]*)`")
    _RE_BOLD_ITAL    = re.compile(r"(\*{1,3}|_{1,3})(.+?)\1")
    _RE_HEADING      = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
    _RE_LIST_BULLET  = re.compile(r"^\s*[-*+•]\s+", re.MULTILINE)
    _RE_LIST_NUM     = re.compile(r"^\s*\d+[.)]\s+", re.MULTILINE)
    _RE_LINK         = re.compile(r"\[([^\]]+)\]\([^)]+\)")
    _RE_EMOJI        = re.compile(
        "["
        "\U0001F300-\U0001FAFF"
        "\U00002600-\U000027BF"
        "\U0001F000-\U0001F2FF"
        "]+",
        flags=re.UNICODE,
    )
    _RE_MULTI_SPACE  = re.compile(r"[ \t]{2,}")

    @classmethod
    def _sanitize_for_tts(cls, text: str) -> str:
        if not text:
            return text
        text = cls._RE_CODE_FENCE.sub(" ", text)
        text = cls._RE_INLINE_CODE.sub(r"\1", text)
        text = cls._RE_LINK.sub(r"\1", text)
        text = cls._RE_BOLD_ITAL.sub(r"\2", text)
        text = cls._RE_HEADING.sub("", text)
        text = cls._RE_LIST_BULLET.sub("", text)
        text = cls._RE_LIST_NUM.sub("", text)
        text = cls._RE_EMOJI.sub("", text)
        text = cls._RE_MULTI_SPACE.sub(" ", text)
        return text.strip()

    def __init__(self, db, brain, audio, working_memory=None):
        self.db    = db
        self.brain = brain
        self.audio = audio
        self.working_memory = working_memory  # core.working_memory.WorkingMemory | None
        self.user_profile = None  # core.user_profile.UserProfile | None — встановлюється з main

        self.personality_mode  = "стандартний"
        self.active_provider   = "deepseek"
        self.short_term_memory = deque(maxlen=self.MAX_SHORT_TERM)
        self._response_cache: "OrderedDict[str, str]" = OrderedDict()
        self._cache_lock       = threading.Lock()

        print(f"{Fore.CYAN}⚙️ Ініціалізація Мовних Моделей (LLM)...")
        self._providers: dict[str, BaseLLMProvider] = {}
        self._ollama_ready = threading.Event()
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
            logger.info("DEEPSEEK_API_KEY не задано — DeepSeek-провайдер пропущено.")

        gemini_key = os.getenv("GEMINI_API_KEY")
        if gemini_key:
            self._providers["gemini"] = GeminiProvider(api_key=gemini_key)
            logger.info("Gemini підключено.")
        else:
            logger.info("GEMINI_API_KEY не задано — Gemini-провайдер пропущено.")

        openai_key = os.getenv("OPENAI_API_KEY")
        if openai_key:
            openai_model    = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
            openai_base_url = os.getenv("OPENAI_BASE_URL") or None
            openai_org      = os.getenv("OPENAI_ORG") or None
            self._providers["openai"] = OpenAIProvider(
                api_key=openai_key,
                model=openai_model,
                base_url=openai_base_url,
                organization=openai_org,
            )
            logger.info(f"OpenAI підключено (модель: {openai_model}).")
        else:
            logger.info("OPENAI_API_KEY не задано — OpenAI-провайдер пропущено.")

        # Ollama probe is a network call — defer to background thread so it
        # never blocks startup (saves ~2s when Ollama is slow/unreachable).
        threading.Thread(
            target=self._init_ollama_async, daemon=True, name="OllamaInit"
        ).start()

    def _init_ollama_async(self) -> None:
        try:
            ollama_provider, _, ollama_notice = self._build_ollama_provider()
            if ollama_provider is not None:
                self._providers["ollama"] = ollama_provider
                logger.info("Ollama підключено (асинхронно).")
                if ollama_notice:
                    logger.warning(ollama_notice)
            else:
                logger.warning(ollama_notice or "Ollama недоступна — пропускаємо ініціалізацію.")
        finally:
            self._ollama_ready.set()

    @staticmethod
    def _normalize_ollama_model_name(model_name: str) -> str:
        return model_name[:-7] if model_name.endswith(":latest") else model_name

    def _build_ollama_provider(self) -> tuple[BaseLLMProvider | None, str | None, str | None]:
        try:
            import requests as _req

            requested_model = os.getenv("OLLAMA_MODEL", "qwen2.5:3b").strip() or "qwen2.5:3b"
            ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11534").rstrip("/")
            api_root = ollama_host[:-3] if ollama_host.endswith("/v1") else ollama_host
            tags_url = f"{api_root}/api/tags"
            response = _req.get(tags_url, timeout=2)
            response.raise_for_status()

            models = response.json().get("models", []) or []
            model_names = [item.get("name") for item in models if item.get("name")]
            if not model_names:
                return None, None, (
                    f"Ollama доступна, але моделей немає. Завантажте модель командою: "
                    f"docker exec -it jarvis-ollama ollama pull {requested_model}"
                )

            normalized_requested = self._normalize_ollama_model_name(requested_model)
            selected_model = next(
                (
                    name for name in model_names
                    if name == requested_model or self._normalize_ollama_model_name(name) == normalized_requested
                ),
                None,
            )

            notice = None
            if selected_model is None:
                selected_model = model_names[0]
                notice = (
                    f"Модель Ollama '{requested_model}' не знайдена. "
                    f"Використовую '{selected_model}'."
                )

            return OllamaProvider(model=selected_model), selected_model, notice
        except Exception as exc:
            return None, None, f"Ollama недоступна — {exc}"

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
    def openai_client(self):
        """Expose the raw OpenAI client (e.g. for direct chat.completions calls)."""
        p = self._providers.get("openai")
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
        elif "openai" in cmd_lower or "gpt" in cmd_lower or "джипіті" in cmd_lower or "чет жпт" in cmd_lower or "четжпт" in cmd_lower:
            if "openai" in self._providers:
                self.active_provider = "openai"
                self.audio.speak("Перемикаю нейромережу на OpenAI GPT.")
            else:
                self.audio.speak("OpenAI недоступний. Перевірте OPENAI_API_KEY у файлі .env.")
        elif "ollama" in cmd_lower or "олама" in cmd_lower or "локальн" in cmd_lower:
            ollama_provider, selected_model, notice = self._build_ollama_provider()
            if ollama_provider is not None:
                self._providers["ollama"] = ollama_provider
                self.active_provider = "ollama"
                if notice:
                    logger.warning(notice)
                    self.audio.speak(f"Перемикаю на локальну Олама. {notice}")
                else:
                    self.audio.speak(
                        f"Перемикаю нейромережу на локальну базу. Активна модель: {selected_model}."
                    )
            else:
                self._providers.pop("ollama", None)
                self.audio.speak(notice or "Локальна Олама недоступна. Сервер не запущено.")
        else:
            self.audio.speak("Невідомий провайдер. Доступні: DeepSeek, Gemini, OpenAI, Олама.")

    def get_status(self) -> str:
        return (
            f"Активний провайдер: {self.active_provider}. "
            f"Режим характеру: {self.personality_mode}."
        )

    # -------------------------------------------------------------------------
    # КЕШ
    # -------------------------------------------------------------------------
    def _get_cached(self, prompt: str) -> str | None:
        # Uses prompt directly as key — avoids MD5 cost on every call.
        # OrderedDict.move_to_end() makes this an O(1) LRU.
        with self._cache_lock:
            value = self._response_cache.get(prompt)
            if value is not None:
                self._response_cache.move_to_end(prompt)
            return value

    def _set_cached(self, prompt: str, response: str) -> None:
        with self._cache_lock:
            if prompt in self._response_cache:
                self._response_cache.move_to_end(prompt)
            self._response_cache[prompt] = response
            while len(self._response_cache) > self.CACHE_MAX:
                self._response_cache.popitem(last=False)

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
            if is_system_call:
                # Детермінований системний промпт (skill-builder, NLU, code-checker,
                # planner, ...) — його результат можна безпечно кешувати.
                # Розмовні промпти НЕ кешуються тут (вони унікальні через WM/RAG/час).
                self._set_cached(prompt, ans)
            else:
                self.short_term_memory.append(f"AI: {ans}")
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
        # Triple-backtick fence is a definitive signal regardless of length.
        if "```" in text:
            return True
        lines = text.splitlines()
        code_lines = sum(1 for line in lines if any(m in line for m in self._CODE_MARKERS))
        return code_lines >= 2

    def _stream_and_speak(self, prompt: str) -> str | None:
        """Stream a response from the active provider.

        Strategy:
          1. Buffer the first ``_CODE_DETECT_BUFFER`` characters silently.
          2. Decide ONCE whether the response is code.
             - If yes: keep collecting full text without speaking, then say
               the short ``_CODE_SPOKEN_MSG`` once at the end.
             - If no: flush the buffered prefix sentence-by-sentence and
               continue streaming + speaking the rest in real time.
        This avoids the previous bug where individual sentences were spoken
        out loud first and the "code detected" message was appended after.
        """
        try:
            provider = self._get_active_provider()
            if provider is None:
                logger.error(f"Клієнт {self.active_provider} недоступний.")
                return None

            full_parts: list[str] = []
            buffer = ""           # spoken-pending text
            decided = False       # has code/no-code decision been made yet?
            is_code = False

            for delta in provider.stream(prompt):
                if not delta:
                    continue
                full_parts.append(delta)

                if is_code:
                    continue
                buffer += delta

                if not decided:
                    if len(buffer) < self._CODE_DETECT_BUFFER:
                        continue
                    is_code = self._looks_like_code(buffer)
                    decided = True
                    if is_code:
                        # Stop accumulating speakable buffer — we will not speak it.
                        buffer = ""
                        continue
                    # else: fall through and start speaking the buffered prefix.

                parts = self.SENTENCE_END.split(buffer)
                if len(parts) > 1:
                    for sentence in parts[:-1]:
                        clean = self._sanitize_for_tts(sentence)
                        if clean:
                            self.audio.speak(clean)
                    buffer = parts[-1]

            # End of stream.
            if not decided:
                # Stream was shorter than the detection buffer — decide now.
                is_code = self._looks_like_code(buffer)
                decided = True
                if is_code:
                    buffer = ""

            if not is_code and buffer.strip():
                clean = self._sanitize_for_tts(buffer)
                if clean:
                    self.audio.speak(clean)

            full_text = ''.join(full_parts)
            if is_code:
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

        # --- Working Memory: розв'язуємо кореференції ("він/вона/той") ----
        wm = self.working_memory
        wm_block = ""
        wm_dialog = ""
        search_query = user_text
        if wm is not None:
            try:
                wm.add_user_turn(user_text)
                expanded = wm.resolve_coreferences(user_text)
                if expanded != user_text:
                    logger.info(f"[WM] coref: '{user_text}' → '{expanded}'")
                    search_query = expanded
                wm_block = wm.context_block(max_chars=600)
                wm_dialog = wm.recent_dialog(n=4)
            except Exception as e:
                logger.warning(f"[WM] помилка читання: {e}")

        print(f"{Fore.YELLOW}🔍 Пошук контексту (паралельно)...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            future_context = pool.submit(self.brain.search, search_query, 10)
            future_facts   = pool.submit(self.db.get_recent_facts, 15)
            future_relevant_facts = pool.submit(self.db.search_facts, search_query, 8)
            future_contacts = pool.submit(self.db.search_contacts, search_query, 3)
            knowledge_context = future_context.result()
            recent_facts      = future_facts.result()
            relevant_facts    = future_relevant_facts.result()
            matched_contacts  = future_contacts.result()

        if not knowledge_context.strip():
            knowledge_context = "У пам'яті немає прямої інформації щодо цього запиту."

        history     = "\n".join(list(self.short_term_memory)[-6:])
        role_prompt = config.AI_PROMPTS.get(
            self.personality_mode,
            config.AI_PROMPTS["стандартний"]
        )

        wm_section = (
            f"СВІДОМИЙ КОНТЕКСТ (пам'ятай!): {wm_block}\n"
            if wm_block else ""
        )
        wm_dialog_section = (
            f"ОСТАННІ РЕПЛІКИ (структуровано):\n{wm_dialog}\n\n"
            if wm_dialog else ""
        )

        profile_section = ""
        if self.user_profile is not None:
            try:
                pb = self.user_profile.as_prompt_block()
                if pb:
                    profile_section = f"ПРОФІЛЬ КОРИСТУВАЧА: {pb}\n\n"
            except Exception as e:
                logger.debug(f"[UserProfile] prompt block помилка: {e}")

        affect_section = ""
        try:
            from core.affect import get_affect
            ab = get_affect().as_prompt_block()
            if ab:
                affect_section = f"АФЕКТ: {ab}\n\n"
        except Exception as e:
            logger.debug(f"[Affect] prompt block помилка: {e}")

        full_prompt = (
            f"Системна роль: {role_prompt}\n\n"
            f"{profile_section}"
            f"{affect_section}"
            f"{wm_section}"
            f"КОНТЕКСТ З ПАМ'ЯТІ:\n\"\"\"{knowledge_context}\"\"\"\n\n"
            f"КОНТАКТИ: {matched_contacts}\n"
            f"РЕЛЕВАНТНІ ФАКТИ: {relevant_facts}\n"
            f"ДОДАТКОВІ ФАКТИ: {recent_facts}\n"
            f"{wm_dialog_section}"
            f"ІСТОРІЯ ДІАЛОГУ:\n{history}\n\n"
            f"ЗАПИТ КОРИСТУВАЧА: {user_text}"
        )

        full_answer = self._stream_and_speak(full_prompt)

        if full_answer:
            self.short_term_memory.append(f"AI: {full_answer}")
            if wm is not None:
                try:
                    wm.add_ai_turn(full_answer, skill="chat")
                except Exception as e:
                    logger.warning(f"[WM] помилка запису AI: {e}")
            # NOTE: response cache intentionally not populated here — full_prompt
            # contains time-stamped recent_facts, RAG search output, and dialogue
            # history, so it is effectively unique per call. Caching would only
            # waste memory. The cache stays useful for generate_response()
            # (system calls with stable prompts).
            threading.Thread(
                target=self._save_to_memory,
                args=(user_text, full_answer),
                daemon=True
            ).start()
        else:
            self.audio.speak("Виникла помилка при зверненні до нейромережі.")

    def _save_to_memory(self, user_text: str, ans: str) -> None:
        try:
            assistant_name = getattr(config, "ASSISTANT_NAME", "Нео")
            memory_block = (
                f"Користувач спитав: {user_text}. "
                f"{assistant_name} відповів: {ans}"
            )
            self.brain.memorize(memory_block, source="auto_dialogue")
        except Exception as e:
            logger.error(f"Не вдалося зберегти репліку в RAG: {e}")
