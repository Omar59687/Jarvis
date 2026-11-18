"""Shared, vendor-neutral local model generation contract."""
from __future__ import annotations

from dataclasses import dataclass
import math
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
                 temperature: float = 0.3,
                 max_output_tokens: int = 192) -> GenerationResult: ...


def validate_generation_options(*, temperature: object, max_output_tokens: object) -> None:
    """Validate options shared by local providers before adapter mapping."""
    if (isinstance(temperature, bool) or not isinstance(temperature, (int, float))
            or not math.isfinite(temperature) or not 0.0 <= temperature <= 2.0):
        raise ProviderConfigurationError("temperature must be between 0 and 2")
    if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 512:
        raise ProviderConfigurationError("max_output_tokens must be between 1 and 512")
