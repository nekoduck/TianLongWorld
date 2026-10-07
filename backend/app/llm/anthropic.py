"""
[INPUT]: 依赖 llm/_http.py 的 post_json，依赖 app.errors 的 LLMError
[OUTPUT]: 对外提供 AnthropicClient —— 实现 LLMClient 协议
[POS]: llm 包的 Anthropic Messages API 客户端，与 openai_compat.py 并列，由 factory.py 按配置择一
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from typing import Any

from app.errors import LLMError
from app.llm._http import post_json

_API_VERSION = "2023-06-01"


class AnthropicClient:
    def __init__(
        self, *, api_key: str, model: str, base_url: str, temperature: float | None, timeout: float, max_tokens: int
    ):
        self._url = f"{base_url.rstrip('/')}/v1/messages"
        self._headers = {"x-api-key": api_key, "anthropic-version": _API_VERSION}
        self._model = model
        self._temperature = temperature
        self._timeout = timeout
        self._max_tokens = max_tokens

    async def complete(self, system: str, user: str) -> str:
        payload: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if self._temperature is not None:
            payload["temperature"] = self._temperature

        data = await post_json(self._url, headers=self._headers, payload=payload, timeout=self._timeout)
        text = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
        if not text:
            raise LLMError(f"天机紊乱：大模型未返回文本（stop_reason={data.get('stop_reason')}）")
        return text
