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
