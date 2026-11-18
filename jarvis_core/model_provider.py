"""Shared, vendor-neutral local model generation contract."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class ProviderError(RuntimeError):
    """Base error with no provider response body or secret-bearing detail."""


class ProviderConfigurationError(ProviderError, ValueError):
    pass


class ProviderConnectionError(ProviderError):
    pass


class ProviderTimeout(ProviderConnectionError):
    pass


class ProviderHTTPError(ProviderError):
    pass


class ProviderProtocolError(ProviderError):
    pass


class ProviderResponseTooLarge(ProviderProtocolError):
    pass


class ProviderModelUnavailable(ProviderError):
    pass


@dataclass(frozen=True)
class GenerationResult:
    model: str
    text: str
    done: bool


class ModelProvider(Protocol):
    """Single-turn text generation API used by local Jarvis services."""

    timeout: float

    def generate(self, prompt: str, model: str, *, system_prompt: str | None = None,
                 max_output_tokens: int = 192) -> GenerationResult: ...
