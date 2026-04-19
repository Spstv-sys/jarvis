import logging
from typing import Iterator

from openai import OpenAI

from .base import BaseLLMProvider

logger = logging.getLogger(__name__)


class OllamaProvider(BaseLLMProvider):
    _DEFAULT_MODEL = "qwen2.5:latest"
    _BASE_URL = "http://localhost:11434/v1"

    def __init__(self, model: str = _DEFAULT_MODEL) -> None:
        self._client = OpenAI(
            base_url=self._BASE_URL,
            api_key="ollama",
            timeout=60,
        )
        self._model = model

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

    def generate(self, prompt: str) -> str | None:
        try:
            res = self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
            )
            return res.choices[0].message.content
        except Exception as e:
            logger.error(f"Ollama generate error: {e}")
            return None

    def stream(self, prompt: str) -> Iterator[str]:
        try:
            s = self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                stream=True,
            )
            for chunk in s:
                yield chunk.choices[0].delta.content or ""
        except Exception as e:
            logger.error(f"Ollama stream error: {e}")
