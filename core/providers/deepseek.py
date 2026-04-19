import logging
from typing import Iterator

from openai import OpenAI

from .base import BaseLLMProvider

logger = logging.getLogger(__name__)


class DeepSeekProvider(BaseLLMProvider):
    _MODEL = "deepseek-chat"

    def __init__(self, api_key: str) -> None:
        self._client = OpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com/v1",
            timeout=30,
        )

    @property
    def name(self) -> str:
        return "deepseek"

    @property
    def available(self) -> bool:
        return self._client is not None

    def generate(self, prompt: str) -> str | None:
        try:
            res = self._client.chat.completions.create(
                model=self._MODEL,
                messages=[{"role": "user", "content": prompt}],
            )
            return res.choices[0].message.content
        except Exception as e:
            logger.error(f"DeepSeek generate error: {e}")
            return None

    def stream(self, prompt: str) -> Iterator[str]:
        try:
            s = self._client.chat.completions.create(
                model=self._MODEL,
                messages=[{"role": "user", "content": prompt}],
                stream=True,
            )
            for chunk in s:
                yield chunk.choices[0].delta.content or ""
        except Exception as e:
            logger.error(f"DeepSeek stream error: {e}")
