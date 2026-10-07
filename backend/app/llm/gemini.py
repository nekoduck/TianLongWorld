"""
[INPUT]: 依赖 llm/_http.py 的 post_json，依赖 llm/base.py 的 JsonSchema，依赖 app.errors 的 LLMError
[OUTPUT]: 对外提供 GeminiClient —— 实现 LLMClient 协议
[POS]: llm 包的 Gemini 原生 generateContent 客户端：以 responseJsonSchema 结构化输出从采样层面约束导演契约，以 thinkingLevel 换取回合延迟
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from typing import Any

from app.errors import LLMError
from app.llm._http import post_json
from app.llm.base import JsonSchema


class GeminiClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        temperature: float | None,
        timeout: float,
        max_tokens: int,
        thinking_level: str | None,
    ):
        self._url = f"{base_url.rstrip('/')}/models/{model}:generateContent"
        self._headers = {"x-goog-api-key": api_key}  # 走请求头而非 ?key=，密钥不会出现在任何 URL 日志里
        self._temperature = temperature
        self._timeout = timeout
        self._max_tokens = max_tokens
        self._thinking_level = thinking_level

    async def complete(self, system: str, user: str, schema: JsonSchema | None = None) -> str:
        config: dict[str, Any] = {"responseMimeType": "application/json", "maxOutputTokens": self._max_tokens}
        if schema is not None:
            config["responseJsonSchema"] = schema
        if self._temperature is not None:
            config["temperature"] = self._temperature
        if self._thinking_level:
            config["thinkingConfig"] = {"thinkingLevel": self._thinking_level}

        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": config,
        }
        data = await post_json(self._url, headers=self._headers, payload=payload, timeout=self._timeout)
        return _extract_text(data)


def _extract_text(data: dict[str, Any]) -> str:
    candidates = data.get("candidates") or []
    if not candidates:
        reason = (data.get("promptFeedback") or {}).get("blockReason", "无候选")
        raise LLMError(f"天机遮蔽：大模型拒绝作答（{reason}）")
    candidate = candidates[0]
    parts = (candidate.get("content") or {}).get("parts") or []
    # 思考片段（thought=true）不属于裁决正文
    text = "".join(part.get("text", "") for part in parts if not part.get("thought"))
    if not text:
        raise LLMError(f"天机遮蔽：大模型未返回正文（finishReason={candidate.get('finishReason')}）")
    return text
