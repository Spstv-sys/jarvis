import os
import logging
from typing import Iterator

from openai import OpenAI
import requests

from .base import BaseLLMProvider

logger = logging.getLogger(__name__)


class OllamaProvider(BaseLLMProvider):
    _DEFAULT_MODEL = "qwen2.5:3b"
    _TEMPERATURE = 0.7
    _TOP_P = 0.9
    _MAX_TOKENS = 2048

    def __init__(self, model: str | None = None) -> None:
        ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11534").rstrip("/")
        self._api_root = ollama_host[:-3] if ollama_host.endswith("/v1") else ollama_host
        base_url = ollama_host if ollama_host.endswith("/v1") else f"{ollama_host}/v1"
        self._client = OpenAI(
            base_url=base_url,
            api_key="ollama",
            timeout=60,
        )
        self._model = model or os.getenv("OLLAMA_MODEL", self._DEFAULT_MODEL)

    @staticmethod
    def _normalize_model_name(model_name: str) -> str:
        return model_name[:-7] if model_name.endswith(":latest") else model_name

    def _list_local_models(self) -> list[str]:
        try:
            response = requests.get(f"{self._api_root}/api/tags", timeout=2)
            response.raise_for_status()
            models = response.json().get("models", []) or []
            return [item.get("name") for item in models if item.get("name")]
        except Exception:
            return []

    def _recover_missing_model(self) -> bool:
        model_names = self._list_local_models()
        if not model_names:
            return False

        requested_normalized = self._normalize_model_name(self._model)
        selected_model = next(
            (
                name for name in model_names
                if name == self._model or self._normalize_model_name(name) == requested_normalized
            ),
            model_names[0],
        )

        if selected_model == self._model:
            return False

        logger.warning(
            "Ollama model '%s' not available. Falling back to '%s'.",
            self._model,
            selected_model,
        )
        self._model = selected_model
        return True

    @staticmethod
    def _is_missing_model_error(exc: Exception) -> bool:
        text = str(exc).lower()
        return "model" in text and "not found" in text

    @property
    def name(self) -> str:
        return "ollama"

    @property
    def available(self) -> bool:
        return self._client is not None

    @property
    def raw_client(self):
        """Expose the underlying OpenAI client for direct API access."""
        return self._client

    def _build_messages(self, prompt: str) -> list[dict]:
        system_text, user_text = self.split_system_and_user(prompt)
        return [
            {"role": "system", "content": self.build_system_message(system_text)},
            {"role": "user", "content": user_text},
        ]

    def generate(self, prompt: str) -> str | None:
        for attempt in range(2):
            try:
                res = self._client.chat.completions.create(
                    model=self._model,
                    messages=self._build_messages(prompt),
                    temperature=self._TEMPERATURE,
                    top_p=self._TOP_P,
                    max_tokens=self._MAX_TOKENS,
                )
                return res.choices[0].message.content
            except Exception as e:
                if attempt == 0 and self._is_missing_model_error(e) and self._recover_missing_model():
                    continue
                logger.error(f"Ollama generate error: {e}")
                return None

    def stream(self, prompt: str) -> Iterator[str]:
        for attempt in range(2):
            try:
                s = self._client.chat.completions.create(
                    model=self._model,
                    messages=self._build_messages(prompt),
                    temperature=self._TEMPERATURE,
                    top_p=self._TOP_P,
                    max_tokens=self._MAX_TOKENS,
                    stream=True,
                )
                for chunk in s:
                    yield chunk.choices[0].delta.content or ""
                return
            except Exception as e:
                if attempt == 0 and self._is_missing_model_error(e) and self._recover_missing_model():
                    continue
                logger.error(f"Ollama stream error: {e}")
                return
