import base64
import logging
import mimetypes
import os
from typing import Iterator

from openai import OpenAI

from .base import BaseLLMProvider

logger = logging.getLogger(__name__)


class OpenAIProvider(BaseLLMProvider):
    """OpenAI GPT provider (gpt-4o-mini by default).

    Uses the official OpenAI Python SDK. Compatible with any OpenAI-style endpoint
    via the optional ``base_url`` parameter (e.g. Azure OpenAI, local proxies).
    """

    _TEMPERATURE = 0.7
    _TOP_P = 0.9
    _MAX_TOKENS = 2048

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

    def _build_messages(self, prompt: str) -> list[dict]:
        system_text, user_text = self.split_system_and_user(prompt)
        return [
            {"role": "system", "content": self.build_system_message(system_text)},
            {"role": "user", "content": user_text},
        ]

    def generate(self, prompt: str) -> str | None:
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
            logger.error(f"OpenAI generate error: {e}")
            return None

    def stream(self, prompt: str) -> Iterator[str]:
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
        except Exception as e:
            logger.error(f"OpenAI stream error: {e}")

    # ------------------------------------------------------------------
    # Vision: pass an image directly to GPT (gpt-4o family) for analysis.
    # ------------------------------------------------------------------
    _VISION_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".gif"}
    _MAX_IMAGE_BYTES = 18 * 1024 * 1024  # 18 MB safety cap (OpenAI limit ~20 MB)

    def generate_with_image(
        self,
        prompt: str,
        image_path: str,
        detail: str = "high",
    ) -> str | None:
        """Send an image + text prompt to the model via Vision (base64 inline).

        Works only with vision-capable models (gpt-4o, gpt-4o-mini, gpt-4.1, ...).
        Returns the assistant text or None on failure.
        """
        ext = os.path.splitext(image_path)[1].lower()
        if ext not in self._VISION_EXTS:
            logger.error(f"OpenAI vision: unsupported extension {ext}")
            return None
        try:
            size = os.path.getsize(image_path)
        except OSError as e:
            logger.error(f"OpenAI vision: cannot stat {image_path}: {e}")
            return None
        if size > self._MAX_IMAGE_BYTES:
            logger.error(
                f"OpenAI vision: image too large ({size} bytes > {self._MAX_IMAGE_BYTES})"
            )
            return None

        try:
            with open(image_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
        except OSError as e:
            logger.error(f"OpenAI vision: read error: {e}")
            return None

        mime, _ = mimetypes.guess_type(image_path)
        if not mime:
            mime = "image/jpeg" if ext in {".jpg", ".jpeg"} else f"image/{ext.lstrip('.')}"
        data_url = f"data:{mime};base64,{b64}"

        try:
            res = self._client.chat.completions.create(
                model=self._model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url",
                         "image_url": {"url": data_url, "detail": detail}},
                    ],
                }],
            )
            return res.choices[0].message.content
        except Exception as e:
            logger.error(f"OpenAI vision error: {e}")
            return None
