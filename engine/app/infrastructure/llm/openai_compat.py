"""
[INPUT]: 依赖 llm/_http 的 post_json / stream_sse，依赖 application/ports 的 LLMClient / JsonSchema，依赖 app.errors 的 LLMError
[OUTPUT]: 对外提供 OpenAICompatClient —— 实现 LLMClient（Chat Completions；有 schema 时开 JSON 模式，SSE 流式）
[POS]: llm 包的兼容协议客户端，一份实现覆盖 OpenAI / DeepSeek / 通义 / Ollama；
       只在需要结构化输出时开 json_object（叙事是散文，强开 JSON 模式会逼模型把小说包成对象）；
       不用严格 json_schema 模式：它要求全字段必填，与"可选指称"的意图契约不兼容
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from collections.abc import AsyncIterator
from typing import Any

from app.application.ports import JsonSchema, LLMClient
from app.errors import LLMError
from app.infrastructure.llm._http import post_json, stream_sse


class OpenAICompatClient(LLMClient):
    def __init__(self, *, api_key: str, model: str, base_url: str, timeout: float, temperature: float | None) -> None:
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._model = model
        self._timeout = timeout
        self._temperature = temperature

    def _payload(self, system: str, user: str, schema: JsonSchema | None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if schema is not None:
            payload["response_format"] = {"type": "json_object"}
        if self._temperature is not None:
            payload["temperature"] = self._temperature
        return payload

    async def complete(self, system: str, user: str, schema: JsonSchema | None = None) -> str:
        data = await post_json(
            self._url, headers=self._headers, payload=self._payload(system, user, schema), timeout=self._timeout
        )
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("天机紊乱：大模型响应缺少 choices[0].message.content") from exc
        if not content:
            raise LLMError("天机遮蔽：大模型未返回正文")
        return str(content)

    async def stream(self, system: str, user: str) -> AsyncIterator[str]:
        payload = self._payload(system, user, None) | {"stream": True}
        async for chunk in stream_sse(self._url, headers=self._headers, payload=payload, timeout=self._timeout):
            choices = chunk.get("choices") or []
            if not choices:
                continue
            if choices[0].get("finish_reason") == "content_filter":
                raise LLMError("天机遮蔽：大模型拒绝作答（content_filter）")
            if text := (choices[0].get("delta") or {}).get("content"):
                yield text
