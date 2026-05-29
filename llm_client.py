"""Simple ModelScope/OpenAI-compatible LLM client shared by Coordinator and worker Agents.

This version keeps the old llm.chat(prompt) API, and also adds short structured
JSON calls for the A2A/MCP travel workflow.  The structured calls are intended
for small decisions such as task parsing, weather constraints, attraction
selection and traffic option selection, rather than long free-form answers.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
import json
import os
import re
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI


load_dotenv()


DEFAULT_BASE_URL = "https://api-inference.modelscope.cn/v1"
DEFAULT_MODEL = "Qwen/Qwen3.5-35B-A3B"


class LLMClientError(RuntimeError):
    """Raised when the LLM client is misconfigured or the provider call fails."""


@dataclass
class LLMClient:
    base_url: str = field(default_factory=lambda: os.getenv("A2A_LLM_BASE_URL", DEFAULT_BASE_URL))
    model: str = field(default_factory=lambda: os.getenv("A2A_LLM_MODEL", DEFAULT_MODEL))
    api_key: str | None = field(default_factory=lambda: os.getenv("MODELSCOPE_API_KEY"))
    timeout_seconds: float = field(default_factory=lambda: float(os.getenv("A2A_LLM_TIMEOUT_SECONDS", "60")))

    def __post_init__(self) -> None:
        self.base_url = self.base_url.strip().rstrip("/")
        self.model = self.model.strip()

    def chat(
        self,
        prompt: str,
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        timeout_seconds: float | None = None,
    ) -> str:
        return self.chat_messages(
            [{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=temperature,
            timeout_seconds=timeout_seconds,
        )

    def chat_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        timeout_seconds: float | None = None,
        stream: bool = True,
    ) -> str:
        if not stream:
            return self._chat_messages_non_stream(
                messages,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout_seconds=timeout_seconds,
            )
        return "".join(
            self.stream_chat_messages(
                messages,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout_seconds=timeout_seconds,
            )
        ).strip()

    def chat_json(
        self,
        prompt: str,
        *,
        max_tokens: int = 600,
        temperature: float = 0.0,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Call the model and parse the first JSON object from its response."""
        response = self.chat_messages(
            [{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=temperature,
            timeout_seconds=timeout_seconds,
            stream=False,
        )
        return _extract_json_object(response)

    def _chat_messages_non_stream(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        timeout_seconds: float | None = None,
    ) -> str:
        _validate_messages(messages)
        if not self.api_key:
            raise LLMClientError("MODELSCOPE_API_KEY is required")

        client = OpenAI(base_url=self.base_url, api_key=self.api_key, max_retries=0)
        request_kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "timeout": timeout_seconds or self.timeout_seconds,
        }
        if max_tokens is not None:
            request_kwargs["max_tokens"] = max_tokens
        if temperature is not None:
            request_kwargs["temperature"] = temperature

        try:
            response = client.chat.completions.create(**request_kwargs)
            choices = getattr(response, "choices", None) or []
            if not choices:
                return ""
            message = getattr(choices[0], "message", None)
            if isinstance(message, dict):
                return str(message.get("content") or "").strip()
            return str(getattr(message, "content", None) or "").strip()
        except Exception as e:
            raise LLMClientError(f"OpenAI API error: {str(e)}") from e

    def stream_chat(self, prompt: str) -> Iterator[str]:
        yield from self.stream_chat_messages([{"role": "user", "content": prompt}])

    def stream_chat_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        timeout_seconds: float | None = None,
    ) -> Iterator[str]:
        _validate_messages(messages)
        if not self.api_key:
            raise LLMClientError("MODELSCOPE_API_KEY is required")

        client = OpenAI(base_url=self.base_url, api_key=self.api_key, max_retries=0)
        request_kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "timeout": timeout_seconds or self.timeout_seconds,
        }
        if max_tokens is not None:
            request_kwargs["max_tokens"] = max_tokens
        if temperature is not None:
            request_kwargs["temperature"] = temperature

        try:
            response = client.chat.completions.create(**request_kwargs)
            for chunk in response:
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                delta = getattr(choices[0], "delta", None)
                content = _read_delta_content(delta)
                if content:
                    yield content
        except Exception as e:
            raise LLMClientError(f"OpenAI API error: {str(e)}") from e

    def info(self) -> dict[str, str]:
        return {
            "provider": "modelscope",
            "model": self.model,
            "base_url": self.base_url,
        }


def _validate_messages(messages: list[dict[str, Any]]) -> None:
    if not messages:
        raise LLMClientError("messages are required")
    for index, message in enumerate(messages):
        if message.get("role") not in {"system", "user", "assistant"}:
            raise LLMClientError(f"message {index} has invalid role")
        content = message.get("content")
        if isinstance(content, str):
            if not content.strip():
                raise LLMClientError(f"message {index} content is required")
        elif isinstance(content, list):
            if not content:
                raise LLMClientError(f"message {index} content is required")
        else:
            raise LLMClientError(f"message {index} content must be a string or content-part list")


def _read_delta_content(delta: Any) -> str:
    if delta is None:
        return ""
    if isinstance(delta, dict):
        return delta.get("content") or ""
    return getattr(delta, "content", None) or ""


def _extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    decoder = json.JSONDecoder()
    try:
        value, _ = decoder.raw_decode(cleaned)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    if start == -1:
        raise LLMClientError(f"LLM response is not JSON: {text[:200]}")
    try:
        value, _ = decoder.raw_decode(cleaned[start:])
    except json.JSONDecodeError as exc:
        raise LLMClientError(f"failed to parse JSON from LLM response: {exc}") from exc
    if not isinstance(value, dict):
        raise LLMClientError("LLM JSON response must be an object")
    return value


llm = LLMClient()
