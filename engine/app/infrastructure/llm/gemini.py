"""
[INPUT]: 依赖 llm/_http 的 post_json / stream_sse，依赖 llm/schema 的 portable_schema，依赖 application/ports 的 LLMClient / JsonSchema，
         依赖 app.errors 的 LLMError
[OUTPUT]: 对外提供 GeminiClient —— 实现 LLMClient（generateContent + responseJsonSchema 结构化输出；streamGenerateContent?alt=sse 流式；可选 thinkingLevel）
[POS]: llm 包的 Gemini 原生客户端，与 anthropic.py / openai_compat.py 并列；密钥走 x-goog-api-key 请求头而非 URL，
       思考片段（thought=true）不属于正文，拒答与空正文收敛为 LLMError
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from collections.abc import AsyncIterator
from typing import Any

from app.application.ports import JsonSchema, LLMClient
from app.errors import LLMError
from app.infrastructure.llm._http import post_json, stream_sse
from app.infrastructure.llm.schema import portable_schema


class GeminiClient(LLMClient):
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        max_tokens: int,
        timeout: float,
        temperature: float | None,
        thinking_level: str = "",
    ) -> None:
        root = f"{base_url.rstrip('/')}/models/{model}"
        self._complete_url = f"{root}:generateContent"
        self._stream_url = f"{root}:streamGenerateContent?alt=sse"
        self._headers = {"x-goog-api-key": api_key}
        self._max_tokens = max_tokens
        self._timeout = timeout
        self._temperature = temperature
        self._thinking_level = thinking_level  # Gemini 3 系：minimal / low / medium / high，留空用模型默认

    def _payload(self, system: str, user: str, schema: JsonSchema | None) -> dict[str, Any]:
        config: dict[str, Any] = {"maxOutputTokens": self._max_tokens}
        if schema is not None:
            config["responseMimeType"] = "application/json"
            config["responseJsonSchema"] = portable_schema(schema)
        if self._temperature is not None:
            config["temperature"] = self._temperature
        if self._thinking_level:
            config["thinkingConfig"] = {"thinkingLevel": self._thinking_level}
        return {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": config,
        }

    async def complete(self, system: str, user: str, schema: JsonSchema | None = None) -> str:
        data = await post_json(
            self._complete_url, headers=self._headers, payload=self._payload(system, user, schema), timeout=self._timeout
        )
        text = _text(data)
        if not text:
            raise LLMError("天机遮蔽：大模型未返回正文")
        if ((data.get("candidates") or [{}])[0]).get("finishReason") == "MAX_TOKENS":
            raise LLMError("天机未尽：大模型输出被截断，请调大 LLM_MAX_TOKENS")  # 半截 JSON 不必等到解析才发现
        return text

    async def stream(self, system: str, user: str) -> AsyncIterator[str]:
        payload = self._payload(system, user, None)
        async for chunk in stream_sse(self._stream_url, headers=self._headers, payload=payload, timeout=self._timeout):
            if text := _text(chunk):
                yield text


def _text(data: dict[str, Any]) -> str:
    candidates = data.get("candidates") or []
    if not candidates:
        reason = (data.get("promptFeedback") or {}).get("blockReason")
        if reason:
            raise LLMError(f"天机遮蔽：大模型拒绝作答（{reason}）")
        return ""
    candidate = candidates[0]
    if candidate.get("finishReason") in {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT"}:
        raise LLMError(f"天机遮蔽：大模型拒绝作答（{candidate['finishReason']}）")
    parts = (candidate.get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts if not p.get("thought"))
