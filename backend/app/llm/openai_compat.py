"""
[INPUT]: 依赖 llm/_http.py 的 post_json，依赖 app.errors 的 LLMError
[OUTPUT]: 对外提供 OpenAICompatClient —— 实现 LLMClient 协议
[POS]: llm 包的 Chat Completions 协议客户端，一份实现覆盖 OpenAI / DeepSeek / 通义 / Gemini / Ollama 等兼容端点
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from typing import Any

from app.errors import LLMError
from app.llm._http import post_json


class OpenAICompatClient:
    def __init__(self, *, api_key: str, model: str, base_url: str, temperature: float | None, timeout: float):
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._model = model
        self._temperature = temperature
        self._timeout = timeout

    async def complete(self, system: str, user: str) -> str:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            # JSON 模式：主流兼容端点均支持，从采样层面杜绝散文输出
            "response_format": {"type": "json_object"},
        }
        # 不下发 max_tokens：各家对 max_tokens / max_completion_tokens 的取舍不一，篇幅由 Prompt 约束
        if self._temperature is not None:
            payload["temperature"] = self._temperature

        data = await post_json(self._url, headers=self._headers, payload=payload, timeout=self._timeout)
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("天机紊乱：大模型响应缺少 choices[0].message.content") from exc
