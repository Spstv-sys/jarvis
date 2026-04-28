import logging
from typing import Iterator

from google import genai
from google.genai import types as genai_types

from .base import BaseLLMProvider

logger = logging.getLogger(__name__)


class GeminiProvider(BaseLLMProvider):
    _MODEL = "gemini-2.5-flash"

    # Keep generation behaviour close to DeepSeek/OpenAI defaults so that
    # switching providers does not produce a drastically different style.
    _TEMPERATURE = 0.7
    _TOP_P = 0.9
    _MAX_OUTPUT_TOKENS = 2048

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

    # -------------------------------------------------------------------------
    # Prompt shaping
    # -------------------------------------------------------------------------
    def _build_config(self, system_instruction: str | None) -> genai_types.GenerateContentConfig:
        # Gemini takes the system role via a dedicated `system_instruction`
        # field rather than inline text. Reuse the shared TTS-friendly
        # formatting nudge from BaseLLMProvider so all providers behave the same.
        return genai_types.GenerateContentConfig(
            system_instruction=self.build_system_message(system_instruction),
            temperature=self._TEMPERATURE,
            top_p=self._TOP_P,
            max_output_tokens=self._MAX_OUTPUT_TOKENS,
        )

    # -------------------------------------------------------------------------
    # API
    # -------------------------------------------------------------------------
    def generate(self, prompt: str) -> str | None:
        try:
            system_text, user_text = self.split_system_and_user(prompt)
            res = self._client.models.generate_content(
                model=self._MODEL,
                contents=user_text,
                config=self._build_config(system_text),
            )
            return res.text
        except Exception as e:
            logger.error(f"Gemini generate error: {e}")
            return None

    def stream(self, prompt: str) -> Iterator[str]:
        try:
            system_text, user_text = self.split_system_and_user(prompt)
            stream = self._client.models.generate_content_stream(
                model=self._MODEL,
                contents=user_text,
                config=self._build_config(system_text),
            )
            for chunk in stream:
                text = getattr(chunk, "text", None)
                if text:
                    yield text
        except Exception as e:
            logger.error(f"Gemini stream error: {e}")
