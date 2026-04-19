import logging
from typing import Iterator

from google import genai

from .base import BaseLLMProvider

logger = logging.getLogger(__name__)


class GeminiProvider(BaseLLMProvider):
    _MODEL = "gemini-2.0-flash"

    def __init__(self, api_key: str) -> None:
        self._client = genai.Client(api_key=api_key)

    @property
    def name(self) -> str:
        return "gemini"

    @property
    def available(self) -> bool:
        return self._client is not None

    @property
    def raw_client(self):
        """Expose the underlying genai.Client for direct Vision API calls."""
        return self._client

    def generate(self, prompt: str) -> str | None:
        try:
            res = self._client.models.generate_content(
                model=self._MODEL,
                contents=prompt,
            )
            return res.text
        except Exception as e:
            logger.error(f"Gemini generate error: {e}")
            return None

    def stream(self, prompt: str) -> Iterator[str]:
        # Streaming via generate_content_stream() is intentionally not used here;
        # yielding the complete response as a single chunk provides a uniform
        # Iterator[str] interface across all providers without adding Gemini-specific
        # streaming state management to the caller.
        result = self.generate(prompt)
        if result:
            yield result
