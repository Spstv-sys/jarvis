"""
Abstract base class for all LLM providers (Strategy pattern).
New providers only need to implement this interface — no changes to LLMHandler required.
"""
from abc import ABC, abstractmethod
from typing import Iterator


class BaseLLMProvider(ABC):
    """Contract that every LLM backend must satisfy."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier, e.g. 'deepseek', 'gemini', 'ollama'."""

    @property
    @abstractmethod
    def available(self) -> bool:
        """True when the provider is initialised and ready to serve requests."""

    @abstractmethod
    def generate(self, prompt: str) -> str | None:
        """Return a complete response for *prompt*, or None on failure."""

    @abstractmethod
    def stream(self, prompt: str) -> Iterator[str]:
        """Yield response tokens incrementally.

        Implementations that do not support native streaming SHOULD yield the
        full response as a single chunk so callers can treat all providers
        uniformly.
        """

    # ------------------------------------------------------------------
    # Shared prompt helpers
    # ------------------------------------------------------------------
    # LLMHandler builds prompts that begin with 'Системна роль: ...\n\n'.
    # Most chat APIs expect the system role in a dedicated message/field
    # rather than inline text, so providers extract it via this helper.
    _SYSTEM_MARKER = "Системна роль:"

    # Common formatting nudge appended to the system role for every provider.
    # Keeps TTS-friendly, markdown-free Ukrainian output regardless of model.
    TTS_FORMAT_HINT = (
        "Відповідай простим суцільним текстом українською, без markdown, "
        "без зірочок, без заголовків і без списків. Розділяй думки реченнями "
        "з крапками — це потрібно для синтезу мовлення."
    )

    @classmethod
    def split_system_and_user(cls, prompt: str) -> tuple[str | None, str]:
        if prompt.startswith(cls._SYSTEM_MARKER):
            head, sep, tail = prompt.partition("\n\n")
            if sep:
                system_text = head[len(cls._SYSTEM_MARKER):].strip()
                return (system_text or None), tail
        return None, prompt

    @classmethod
    def build_system_message(cls, system_instruction: str | None) -> str:
        if system_instruction:
            return f"{system_instruction}\n\n{cls.TTS_FORMAT_HINT}"
        return cls.TTS_FORMAT_HINT
