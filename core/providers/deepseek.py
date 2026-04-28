import logging
from typing import Iterator

from openai import OpenAI

from .base import BaseLLMProvider

logger = logging.getLogger(__name__)


class DeepSeekProvider(BaseLLMProvider):
    _MODEL = "deepseek-chat"
    _TEMPERATURE = 0.7
    _TOP_P = 0.9
    _MAX_TOKENS = 2048

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
        try:
            res = self._client.chat.completions.create(
                model=self._MODEL,
                messages=self._build_messages(prompt),
                temperature=self._TEMPERATURE,
                top_p=self._TOP_P,
                max_tokens=self._MAX_TOKENS,
            )
            return res.choices[0].message.content
        except Exception as e:
            logger.error(f"DeepSeek generate error: {e}")
            return None

    def stream(self, prompt: str) -> Iterator[str]:
        try:
            s = self._client.chat.completions.create(
                model=self._MODEL,
                messages=self._build_messages(prompt),
                temperature=self._TEMPERATURE,
                top_p=self._TOP_P,
                max_tokens=self._MAX_TOKENS,
                stream=True,
            )
            for chunk in s:
                yield chunk.choices[0].delta.content or ""
        except Exception as e:
            logger.error(f"DeepSeek stream error: {e}")
