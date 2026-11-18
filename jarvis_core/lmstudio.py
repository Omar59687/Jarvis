"""LM Studio Bionic native chat adapter with strict loopback-only transport."""
from __future__ import annotations

import ipaddress
import json
import socket
import urllib.error
import urllib.request
from typing import Protocol
from urllib.parse import urlsplit

from jarvis_core.model_provider import (
    GenerationResult,
    ProviderConfigurationError,
    ProviderConnectionError,
    ProviderHTTPError,
    ProviderModelUnavailable,
    ProviderProtocolError,
    ProviderResponseTooLarge,
    ProviderTimeout,
    validate_generation_options,
)

MAX_RESPONSE_BYTES = 2_000_000
MAX_PROMPT_CHARS = 12_000
MAX_SYSTEM_CHARS = 4_000
MAX_OUTPUT_CHARS = 100_000


class HTTPTransport(Protocol):
    def request(self, url: str, body: bytes, timeout: float,
                max_response_bytes: int) -> tuple[int, bytes]: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class UrllibTransport:
    """Stdlib HTTP transport that ignores proxies and never follows redirects."""

    def request(self, url: str, body: bytes, timeout: float,
                max_response_bytes: int) -> tuple[int, bytes]:
        request = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        try:
            response = opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            response = exc
        except (TimeoutError, socket.timeout) as exc:
            raise ProviderTimeout("Local model request timed out") from exc
        except (urllib.error.URLError, OSError) as exc:
            if isinstance(getattr(exc, "reason", None), (TimeoutError, socket.timeout)):
                raise ProviderTimeout("Local model request timed out") from exc
            raise ProviderConnectionError("Could not connect to the configured local model service") from exc
        try:
            data = response.read(max_response_bytes + 1)
            if len(data) > max_response_bytes:
                raise ProviderResponseTooLarge("Local model response exceeded its size limit")
            return int(response.status), data
        except (TimeoutError, socket.timeout) as exc:
            raise ProviderTimeout("Local model request timed out") from exc
        except OSError as exc:
            raise ProviderConnectionError("Could not read the local model response") from exc
        finally:
            response.close()


class LMStudioBionicClient:
    """Explicit LM Studio native `/api/v1/chat` client; no discovery or fallback."""

    def __init__(self, base_url: str, *, timeout: float = 120.0,
                 transport: HTTPTransport | None = None, reasoning: str = "off"):
        self._endpoint = self._validate_url(base_url)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0.1 <= timeout <= 300:
            raise ProviderConfigurationError("timeout must be between 0.1 and 300 seconds")
        if reasoning not in {"off", "low", "medium", "high", "on"}:
            raise ProviderConfigurationError("unsupported reasoning setting")
        self.timeout = float(timeout)
        self.reasoning = reasoning
        self._transport = transport or UrllibTransport()

    @staticmethod
    def _validate_url(value: str) -> str:
        try:
            parsed = urlsplit(value)
            address = ipaddress.ip_address(parsed.hostname or "")
            port = parsed.port
        except (ValueError, TypeError) as exc:
            raise ProviderConfigurationError("Use an explicit numeric loopback URL and port") from exc
        if (parsed.scheme != "http" or not address.is_loopback or port is None or not 1 <= port <= 65535
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in ("", "/", "/api/v1/chat") or parsed.query or parsed.fragment):
            raise ProviderConfigurationError("Model service URL must be plain HTTP on numeric loopback")
        host = f"[{address.compressed}]" if address.version == 6 else address.compressed
        return f"http://{host}:{port}/api/v1/chat"

    @staticmethod
    def validate_model(model: str) -> None:
        if (not isinstance(model, str) or not model or len(model) > 128
                or any(ord(char) < 32 or ord(char) == 127 for char in model)):
            raise ProviderConfigurationError("An explicit bounded model identifier is required")

    def generate(self, prompt: str, model: str, *, system_prompt: str | None = None,
                 temperature: float = 0.3,
                 max_output_tokens: int = 192) -> GenerationResult:
        validate_generation_options(temperature=temperature, max_output_tokens=max_output_tokens)
        self.validate_model(model)
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > MAX_PROMPT_CHARS or "\x00" in prompt:
            raise ProviderConfigurationError("prompt must be non-empty and within the input limit")
        if system_prompt is not None and (not isinstance(system_prompt, str)
                or len(system_prompt) > MAX_SYSTEM_CHARS or "\x00" in system_prompt):
            raise ProviderConfigurationError("system prompt is outside the input limit")
        if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 512:
            raise ProviderConfigurationError("max_output_tokens must be between 1 and 512")
        try:
            prompt.encode("utf-8")
            if system_prompt is not None:
                system_prompt.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ProviderConfigurationError("prompt text must contain valid Unicode") from exc
        payload = {
            "model": model,
            "input": prompt,
            "stream": False,
            "reasoning": self.reasoning,
            "store": False,
            "max_output_tokens": max_output_tokens,
            "temperature": float(temperature),
        }
        if system_prompt:
            payload["system_prompt"] = system_prompt
        try:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, UnicodeEncodeError) as exc:
            raise ProviderConfigurationError("request contains invalid text") from exc
        status, response_body = self._transport.request(
            self._endpoint, body, self.timeout, MAX_RESPONSE_BYTES,
        )
        if 300 <= status < 400:
            raise ProviderHTTPError("Local model service returned a redirect")
        if status in (400, 404):
            raise ProviderModelUnavailable("The explicitly configured model is unavailable")
        if not 200 <= status < 300:
            raise ProviderHTTPError("Local model service rejected the request")
        try:
            value = json.loads(response_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderProtocolError("Local model service returned malformed JSON") from exc
        if not isinstance(value, dict) or not isinstance(value.get("output"), list):
            raise ProviderProtocolError("Local model service returned an unexpected response")
        messages: list[str] = []
        for item in value["output"]:
            if not isinstance(item, dict):
                raise ProviderProtocolError("Local model service returned an unexpected response")
            kind = item.get("type")
            if kind in {"tool_call", "invalid_tool_call"}:
                raise ProviderProtocolError("Tool responses are unsupported")
            if kind == "message":
                content = item.get("content")
                if not isinstance(content, str):
                    raise ProviderProtocolError("Local model message has an unexpected format")
                messages.append(content)
            elif kind == "reasoning":
                # Reasoning is never exposed in the ordinary Jarvis chat response.
                continue
            else:
                raise ProviderProtocolError("Local model response contains an unsupported output type")
        text = "".join(messages)
        if not text.strip() or len(text) > MAX_OUTPUT_CHARS or "\x00" in text:
            raise ProviderProtocolError("Local model response text is invalid")
        try:
            text.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ProviderProtocolError("Local model response text is invalid") from exc
        return GenerationResult(model=model, text=text, done=True)
