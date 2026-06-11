"""Simple ModelScope/OpenAI-compatible LLM client shared by Coordinator and worker Agents.

This version keeps the old llm.chat(prompt) API, and also adds short structured
JSON calls for the A2A/MCP travel workflow.  The structured calls are intended
for small decisions such as task parsing, weather constraints, attraction
selection and traffic option selection, rather than long free-form answers.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import json
import os
import re
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI
from common.runtime import llm_enabled, runtime_mode_name


load_dotenv()


DEFAULT_BASE_URL = "https://api-inference.modelscope.cn/v1"
DEFAULT_MODEL = "Qwen/Qwen3.5-35B-A3B"
_REQUEST_ENABLE_THINKING: ContextVar[bool | None] = ContextVar("llm_request_enable_thinking", default=None)


class LLMClientError(RuntimeError):
    """Raised when the LLM client is misconfigured or the provider call fails."""


@dataclass
class LLMClient:
    base_url: str = field(default_factory=lambda: os.getenv("A2A_LLM_BASE_URL", DEFAULT_BASE_URL))
    model: str = field(default_factory=lambda: os.getenv("A2A_LLM_MODEL", DEFAULT_MODEL))
    api_key: str | None = field(default_factory=lambda: _llm_api_key())
    timeout_seconds: float = field(default_factory=lambda: float(os.getenv("A2A_LLM_TIMEOUT_SECONDS", "60")))

    def __post_init__(self) -> None:
        # 初始化后处理，清理URL与模型名
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
        # 发送单轮提示并获取文本回复
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
        # 发送多轮消息并获取文本回复
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
        # 非流式发送消息并获取完整回复
        _validate_messages(messages)
        if not llm_enabled():
            raise LLMClientError("LLM is disabled by A2A_USE_LLM=0")
        if not self.api_key:
            raise LLMClientError("A2A_LLM_API_KEY, DEEPSEEK_API_KEY, or MODELSCOPE_API_KEY is required")

        client = OpenAI(base_url=self.base_url, api_key=self.api_key, max_retries=0)
        request_kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "timeout": timeout_seconds or self.timeout_seconds,
            "extra_body": {"enable_thinking": _effective_enable_thinking()},
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
        # 流式发送单轮提示
        yield from self.stream_chat_messages([{"role": "user", "content": prompt}])

    def stream_chat_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        timeout_seconds: float | None = None,
    ) -> Iterator[str]:
        # 流式发送多轮消息
        _validate_messages(messages)
        if not llm_enabled():
            raise LLMClientError("LLM is disabled by A2A_USE_LLM=0")
        if not self.api_key:
            raise LLMClientError("A2A_LLM_API_KEY, DEEPSEEK_API_KEY, or MODELSCOPE_API_KEY is required")

        client = OpenAI(base_url=self.base_url, api_key=self.api_key, max_retries=0)
        request_kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "timeout": timeout_seconds or self.timeout_seconds,
            "extra_body": {"enable_thinking": _effective_enable_thinking()},
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
        # 获取LLM客户端配置信息
        return {
            "provider": _infer_provider(self.base_url),
            "model": self.model,
            "base_url": self.base_url,
            "enabled": "true" if llm_enabled() else "false",
            "mode": runtime_mode_name(),
        }


def _llm_api_key() -> str | None:
    for name in ("A2A_LLM_API_KEY", "DEEPSEEK_API_KEY", "MODELSCOPE_API_KEY"):
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    return None


@contextmanager
def llm_request_options(*, enable_thinking: bool | None = None) -> Iterator[None]:
    token = _REQUEST_ENABLE_THINKING.set(enable_thinking)
    try:
        yield
    finally:
        _REQUEST_ENABLE_THINKING.reset(token)


def _effective_enable_thinking() -> bool:
    override = _REQUEST_ENABLE_THINKING.get()
    if override is not None:
        return bool(override)
    return _env_flag("A2A_LLM_ENABLE_THINKING", False)


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _infer_provider(base_url: str) -> str:
    normalized = base_url.lower()
    if "deepseek" in normalized:
        return "deepseek"
    if "modelscope" in normalized:
        return "modelscope"
    return "openai-compatible"


def _validate_messages(messages: list[dict[str, Any]]) -> None:
    # 验证消息格式是否正确
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
    # 从流式响应块中读取内容
    if delta is None:
        return ""
    if isinstance(delta, dict):
        return delta.get("content") or ""
    return getattr(delta, "content", None) or ""


def _extract_json_object(text: str) -> dict[str, Any]:
    # 从文本中提取第一个JSON对象
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
llm_small = LLMClient()
