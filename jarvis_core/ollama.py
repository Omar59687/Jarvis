"""Small, local-only adapter for the Ollama HTTP API.

This module deliberately does not download models or choose a default model.
"""

from __future__ import annotations

import ipaddress
import json
import re
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol, Sequence
from urllib.parse import urlsplit


class OllamaError(RuntimeError):
    """Base class for adapter errors safe to show without response bodies."""


class OllamaConfigurationError(OllamaError, ValueError):
    pass


class OllamaConnectionError(OllamaError):
    pass


class OllamaTimeout(OllamaConnectionError):
    pass


class OllamaHTTPError(OllamaError):
    pass


class OllamaProtocolError(OllamaError):
    pass


class OllamaResponseTooLarge(OllamaProtocolError):
    pass


class OllamaModelUnavailable(OllamaError):
    pass


@dataclass(frozen=True)
class HTTPResponse:
    status: int
    headers: dict[str, str]
    body: bytes


class OllamaTransport(Protocol):
    def request(self, method: str, url: str, body: bytes | None, timeout: float,
                max_response_bytes: int) -> HTTPResponse: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class UrllibTransport:
    """Bounded stdlib HTTP transport. Redirects are returned, never followed."""

    def request(self, method: str, url: str, body: bytes | None, timeout: float,
                max_response_bytes: int) -> HTTPResponse:
        req = urllib.request.Request(url, data=body, method=method,
                                     headers={"Accept": "application/json"})
        opener = urllib.request.build_opener(_NoRedirect())
        try:
            response = opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            response = exc
        except (TimeoutError, socket.timeout) as exc:
            raise OllamaTimeout("Timed out connecting to the local Ollama service") from exc
        except (urllib.error.URLError, OSError) as exc:
            if isinstance(getattr(exc, "reason", None), (TimeoutError, socket.timeout)):
                raise OllamaTimeout("Timed out connecting to the local Ollama service") from exc
            raise OllamaConnectionError("Could not connect to the local Ollama service") from exc
        try:
            data = response.read(max_response_bytes + 1)
            if len(data) > max_response_bytes:
                raise OllamaResponseTooLarge("Ollama response exceeded the configured limit")
            return HTTPResponse(int(response.status), dict(response.headers.items()), data)
        finally:
            response.close()


@dataclass(frozen=True)
class GenerationSettings:
    temperature: float = 0.0
    seed: int = 0
    num_ctx: int = 4096
    num_predict: int = 384

    def __post_init__(self) -> None:
        if not 0 <= self.temperature <= 2:
            raise OllamaConfigurationError("temperature must be between 0 and 2")
        if not 0 <= self.seed <= 2_147_483_647:
            raise OllamaConfigurationError("seed is out of range")
        if not 1 <= self.num_ctx <= 4096:
            raise OllamaConfigurationError("num_ctx must be between 1 and 4096")
        if not 1 <= self.num_predict <= 512:
            raise OllamaConfigurationError("num_predict must be between 1 and 512")


@dataclass(frozen=True)
class GenerationResult:
    model: str
    text: str
    done: bool


_MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


class OllamaClient:
    """Explicitly configured client for an Ollama server on numeric loopback."""

    MAX_PROMPT_CHARS = 12_000
    MAX_SYSTEM_CHARS = 4_000
    MAX_RESPONSE_BYTES = 2_000_000
    MAX_OUTPUT_CHARS = 100_000

    def __init__(self, base_url: str, allowed_models: Sequence[str], *,
                 transport: OllamaTransport | None = None, timeout: float = 120.0):
        self._origin = self._validate_url(base_url)
        if not allowed_models or isinstance(allowed_models, (str, bytes)):
            raise OllamaConfigurationError("An explicit non-empty model allowlist is required")
        models = tuple(allowed_models)
        if any(not isinstance(m, str) or not _MODEL_RE.fullmatch(m) or m.lower().endswith(":cloud")
               for m in models):
            raise OllamaConfigurationError("Model allowlist contains an unsupported model identifier")
        if len(set(models)) != len(models):
            raise OllamaConfigurationError("Model allowlist contains duplicates")
        if not 0.1 <= timeout <= 300:
            raise OllamaConfigurationError("timeout must be between 0.1 and 300 seconds")
        self.allowed_models = frozenset(models)
        self.timeout = timeout
        self._transport = transport or UrllibTransport()

    @staticmethod
    def _validate_url(value: str) -> str:
        try:
            parsed = urlsplit(value)
            address = ipaddress.ip_address(parsed.hostname or "")
            port = parsed.port
        except (ValueError, TypeError) as exc:
            raise OllamaConfigurationError("Use an explicit numeric loopback URL, such as http://127.0.0.1:11434") from exc
        if (parsed.scheme != "http" or not address.is_loopback or port is None
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            raise OllamaConfigurationError("Ollama URL must be plain HTTP on an explicit loopback address and port")
        host = f"[{address.compressed}]" if address.version == 6 else address.compressed
        return f"http://{host}:{port}"

    def _call(self, method: str, path: str, payload: dict | None = None) -> dict:
        body = None if payload is None else json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        try:
            response = self._transport.request(method, self._origin + path, body,
                                               self.timeout, self.MAX_RESPONSE_BYTES)
        except OllamaError:
            raise
        except (TimeoutError, socket.timeout) as exc:
            raise OllamaTimeout("Timed out connecting to the local Ollama service") from exc
        except OSError as exc:
            raise OllamaConnectionError("Could not connect to the local Ollama service") from exc
        if 300 <= response.status < 400:
            raise OllamaHTTPError("Ollama returned a redirect; redirects are not followed")
        if response.status < 200 or response.status >= 300:
            raise OllamaHTTPError(f"Ollama returned HTTP {response.status}")
        try:
            value = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OllamaProtocolError("Ollama returned malformed JSON") from exc
        if not isinstance(value, dict):
            raise OllamaProtocolError("Ollama returned an unexpected response")
        return value

    def installed_models(self) -> tuple[str, ...]:
        value = self._call("GET", "/api/tags")
        entries = value.get("models")
        if not isinstance(entries, list):
            raise OllamaProtocolError("Ollama model list has an unexpected format")
        names = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise OllamaProtocolError("Ollama model list has an unexpected format")
            name = entry.get("name")
            if isinstance(name, str) and _MODEL_RE.fullmatch(name):
                names.append(name)
        return tuple(names)

    def generate(self, prompt: str, model: str, *, system_prompt: str | None = None,
                 settings: GenerationSettings = GenerationSettings()) -> GenerationResult:
        if not isinstance(model, str):
            raise OllamaConfigurationError("Requested model must be an allowlisted model identifier")
        if model not in self.allowed_models:
            raise OllamaConfigurationError("Requested model is not in the explicit allowlist")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > self.MAX_PROMPT_CHARS or "\x00" in prompt:
            raise OllamaConfigurationError("prompt must be non-empty and within the input limit")
        if system_prompt is not None and (not isinstance(system_prompt, str)
                or len(system_prompt) > self.MAX_SYSTEM_CHARS or "\x00" in system_prompt):
            raise OllamaConfigurationError("system prompt exceeds the input limit or contains invalid content")
        if not isinstance(settings, GenerationSettings):
            raise OllamaConfigurationError("settings must be a GenerationSettings value")
        try:
            prompt.encode("utf-8")
            if system_prompt is not None:
                system_prompt.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise OllamaConfigurationError("prompt text must contain valid Unicode") from exc
        if model not in self.installed_models():
            raise OllamaModelUnavailable("The explicitly requested model is not installed locally")
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        value = self._call("POST", "/api/chat", {
            "model": model, "messages": messages, "stream": False,
            "options": {"temperature": settings.temperature, "seed": settings.seed,
                        "num_ctx": settings.num_ctx, "num_predict": settings.num_predict},
        })
        message = value.get("message")
        text = message.get("content") if isinstance(message, dict) else None
        done = value.get("done")
        if not isinstance(text, str) or len(text) > self.MAX_OUTPUT_CHARS or not isinstance(done, bool):
            raise OllamaProtocolError("Ollama chat response has an unexpected format")
        if "tool_calls" in message:
            raise OllamaProtocolError("Tool-call responses are unsupported by this adapter")
        return GenerationResult(model=model, text=text, done=done)
