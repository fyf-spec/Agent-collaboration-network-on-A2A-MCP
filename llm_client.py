"""Simple ModelScope LLM client shared by Coordinator and worker Agents."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
import os
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

    def chat(self, prompt: str) -> str:
        return self.chat_messages([{"role": "user", "content": prompt}])

    def chat_messages(self, messages: list[dict[str, Any]]) -> str:
        return "".join(self.stream_chat_messages(messages)).strip()

    def stream_chat(self, prompt: str) -> Iterator[str]:
        yield from self.stream_chat_messages([{"role": "user", "content": prompt}])

    def stream_chat_messages(self, messages: list[dict[str, Any]]) -> Iterator[str]:
        _validate_messages(messages)
        if not self.api_key:
            raise LLMClientError("MODELSCOPE_API_KEY is required")

        client = OpenAI(base_url=self.base_url, api_key=self.api_key)
        response = client.chat.completions.create(
            model=self.model,
            messages=messages,
            stream=True,
            timeout=self.timeout_seconds,
        )
        for chunk in response:
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            content = _read_delta_content(delta)
            if content:
                yield content

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


llm = LLMClient()
