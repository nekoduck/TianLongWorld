"""
[INPUT]: 依赖 llm/_http.py 的 post_json / VendorEnvelope / parse_envelope，依赖 llm/schema.py 的 strict_json_schema，
         依赖 llm/base.py 的 JsonSchema，依赖 app.errors 的 LLMError
[OUTPUT]: 对外提供 OpenAICompatClient —— 实现 LLMClient 协议
[POS]: llm 包的 Chat Completions 协议客户端，一份实现覆盖 OpenAI / DeepSeek / 通义 / Ollama 等兼容端点。
       strict_schema 为真时以 json_schema 严格模式在 API 层强制导演契约（schema 先经 schema.py 整形）；
       端点不支持时退回 json_object，只保证合法 JSON，形状交给 director 的解析闸门
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import logging
from typing import Any

from app.errors import LLMError
from app.llm._http import VendorEnvelope, parse_envelope, post_json
from app.llm.base import JsonSchema
from app.llm.schema import strict_json_schema

logger = logging.getLogger(__name__)

_SCHEMA_NAME = "director_output"  # 严格模式要求为 schema 命名（^[a-zA-Z0-9_-]+$）


# ============================================================
#  厂商响应封套 —— 兼容端点五花八门地追加字段，只声明读取的部分
# ============================================================
class _Message(VendorEnvelope):
    content: str | None = None
    refusal: str | None = None  # 严格模式下的安全拒答：出现时 content 为空


class _Choice(VendorEnvelope):
    message: _Message
    finish_reason: str | None = None


class _ChatCompletion(VendorEnvelope):
    choices: tuple[_Choice, ...] = ()  # 缺省为空而非必填：错误报文（{"error": ...}）落到"缺少 choices"的明确文案


class OpenAICompatClient:
    def __init__(
        self, *, api_key: str, model: str, base_url: str, temperature: float | None, timeout: float, strict_schema: bool
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._model = model
        self._temperature = temperature
        self._timeout = timeout
        self._strict_schema = strict_schema

    async def complete(self, system: str, user: str, schema: JsonSchema) -> str:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": self._response_format(schema),
        }
        # 不下发 max_tokens：各家对 max_tokens / max_completion_tokens 的取舍不一，篇幅由 Prompt 约束
        if self._temperature is not None:
            payload["temperature"] = self._temperature

        data = await post_json(self._url, headers=self._headers, payload=payload, timeout=self._timeout)
        return _extract_content(parse_envelope(_ChatCompletion, data))

    def _response_format(self, schema: JsonSchema) -> dict[str, Any]:
        if not self._strict_schema:
            # 退路：DeepSeek 等端点只认 JSON 模式 —— 保证合法 JSON，但不保证形状
            return {"type": "json_object"}
        # 严格模式：受限解码逐 token 套用 schema，形状由采样层面保证；整形开销微秒级，相对秒级延迟可忽略，不做缓存
        return {
            "type": "json_schema",
            "json_schema": {"name": _SCHEMA_NAME, "strict": True, "schema": strict_json_schema(schema)},
        }


def _extract_content(resp: _ChatCompletion) -> str:
    if not resp.choices:
        raise LLMError("天机紊乱：大模型响应缺少 choices")
    choice = resp.choices[0]
    if choice.finish_reason == "length":
        # 截断的 JSON 注定通不过解析闸门：在此点名真因，而不是让它伪装成格式错误
        raise LLMError("天机中断：大模型输出被截断（finish_reason=length）")
    if choice.message.refusal:
        # 拒答原文可能复述敏感内容：只进日志，玩家只看到定性
        logger.warning("大模型拒答：%.200s", choice.message.refusal)
        raise LLMError("天机遮蔽：大模型拒答")
    if not choice.message.content:
        raise LLMError(f"天机遮蔽：大模型拒答（未返回正文，finish_reason={choice.finish_reason}）")
    return choice.message.content
