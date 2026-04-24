import logging
from typing import Iterator

from openai import OpenAI

from .base import BaseLLMProvider

logger = logging.getLogger(__name__)


class OpenAIProvider(BaseLLMProvider):
    """OpenAI GPT provider (gpt-4o-mini by default).

    Uses the official OpenAI Python SDK. Compatible with any OpenAI-style endpoint
    via the optional ``base_url`` parameter (e.g. Azure OpenAI, local proxies).
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str | None = None,
        organization: str | None = None,
    ) -> None:
        kwargs: dict = {"api_key": api_key, "timeout": 30}
        if base_url:
            kwargs["base_url"] = base_url
        if organization:
            kwargs["organization"] = organization
        self._client = OpenAI(**kwargs)
        self._model = model

    @property
    def name(self) -> str:
        return "openai"

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
            logger.error(f"OpenAI generate error: {e}")
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
            logger.error(f"OpenAI stream error: {e}")
