"""
[INPUT]: 依赖 llm/_http 的 post_json / stream_sse，依赖 llm/schema 的 portable_schema，依赖 application/ports 的 LLMClient / JsonSchema，
         依赖 app.errors 的 LLMError
[OUTPUT]: 对外提供 AnthropicClient —— 实现 LLMClient（Messages API；output_config.format 原生结构化输出 + SSE 流式）
[POS]: llm 包的 Anthropic 客户端，与 gemini.py / openai_compat.py 并列，由 factory.py 按配置择一；
       Claude 新模型拒收采样参数，temperature 未配置时一律不下发；拒答（stop_reason=refusal）与截断收敛为 LLMError
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from collections.abc import AsyncIterator
from typing import Any

from app.application.ports import JsonSchema, LLMClient
from app.errors import LLMError
from app.infrastructure.llm._http import post_json, stream_sse
from app.infrastructure.llm.schema import portable_schema

_API_VERSION = "2023-06-01"


class AnthropicClient(LLMClient):
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        max_tokens: int,
        timeout: float,
        temperature: float | None = None,
        effort: str = "",
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/v1/messages"
        self._headers = {"x-api-key": api_key, "anthropic-version": _API_VERSION}
        self._model = model
        self._max_tokens = max_tokens
        self._timeout = timeout
        self._temperature = temperature
        self._effort = effort

    def _payload(self, system: str, user: str, schema: JsonSchema | None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        output_config: dict[str, Any] = {}
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": portable_schema(schema)}
        if self._effort:
            output_config["effort"] = self._effort
        if output_config:
            payload["output_config"] = output_config
        if self._temperature is not None:
            payload["temperature"] = self._temperature
        return payload

    async def complete(self, system: str, user: str, schema: JsonSchema | None = None) -> str:
        data = await post_json(
            self._url, headers=self._headers, payload=self._payload(system, user, schema), timeout=self._timeout
        )
        _check_stop(data.get("stop_reason"))
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        if not text:
            raise LLMError(f"天机紊乱：大模型未返回文本（stop_reason={data.get('stop_reason')}）")
        return text

    async def stream(self, system: str, user: str) -> AsyncIterator[str]:
        payload = self._payload(system, user, None) | {"stream": True}
        async for event in stream_sse(self._url, headers=self._headers, payload=payload, timeout=self._timeout):
            kind = event.get("type")
            if kind == "content_block_delta" and event.get("delta", {}).get("type") == "text_delta":
                yield event["delta"].get("text", "")
            elif kind == "message_delta":
                _check_stop(event.get("delta", {}).get("stop_reason"))
            elif kind == "error":
                raise LLMError(f"天机紊乱：{event.get('error', {}).get('type', '流式错误')}")


def _check_stop(reason: str | None) -> None:
    if reason == "refusal":
        raise LLMError("天机遮蔽：大模型拒绝作答")
    if reason == "max_tokens":
        raise LLMError("天机未尽：大模型输出被截断，请调大 LLM_MAX_TOKENS")
