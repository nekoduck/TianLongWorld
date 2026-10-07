"""
[INPUT]: 依赖 llm/_http.py 的 post_json / VendorEnvelope / parse_envelope，依赖 llm/schema.py 的 strict_json_schema，
         依赖 llm/base.py 的 JsonSchema，依赖 app.errors 的 LLMError
[OUTPUT]: 对外提供 AnthropicClient —— 实现 LLMClient 协议
[POS]: llm 包的 Anthropic Messages API 客户端，与 gemini.py / openai_compat.py 并列，由 factory.py 按配置择一。
       以原生结构化输出（output_config.format = json_schema，GA、无 beta 头）在 API 层约束采样：
       schema 先经 schema.py 整形为严格子集（删 min/max 长度、对象全闭合），正文以 text 块返回。
       不用强制工具调用——Opus 5.5 / Sonnet 5.5 对 tool_choice=tool 直接 400，且非 strict 的工具参数并不受 schema 约束。
       refusal / max_tokens 等非正常结束的输出不保证合法度，在此收敛为 LLMError，不让它们伪装成解析失败
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from typing import Any

from app.errors import LLMError
from app.llm._http import VendorEnvelope, parse_envelope, post_json
from app.llm.base import JsonSchema
from app.llm.schema import strict_json_schema

_API_VERSION = "2023-06-01"
_COMPLETE = (None, "end_turn")  # 只有正常结束的正文才交给解析闸门


# ============================================================
#  厂商响应封套 —— content 是异构块列表：text / thinking……只认 text
# ============================================================
class _Block(VendorEnvelope):
    type: str
    text: str = ""


class _MessageResponse(VendorEnvelope):
    content: tuple[_Block, ...] = ()
    stop_reason: str | None = None


class AnthropicClient:
    def __init__(
        self, *, api_key: str, model: str, base_url: str, temperature: float | None, timeout: float, max_tokens: int
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/v1/messages"
        self._headers = {"x-api-key": api_key, "anthropic-version": _API_VERSION}
        self._model = model
        self._temperature = temperature
        self._timeout = timeout
        self._max_tokens = max_tokens

    async def complete(self, system: str, user: str, schema: JsonSchema) -> str:
        payload: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            # 原生结构化输出：受限解码只认严格子集，长度等校验型约束交还 director 的解析闸门
            "output_config": {"format": {"type": "json_schema", "schema": strict_json_schema(schema)}},
        }
        if self._temperature is not None:
            payload["temperature"] = self._temperature

        data = await post_json(self._url, headers=self._headers, payload=payload, timeout=self._timeout)
        return _extract_text(parse_envelope(_MessageResponse, data))


def _extract_text(resp: _MessageResponse) -> str:
    if resp.stop_reason == "max_tokens":
        # 触顶时 JSON 必然残缺：在此点名真因，而不是让它伪装成格式错误
        raise LLMError("天机中断：大模型输出被截断（stop_reason=max_tokens）")
    if resp.stop_reason not in _COMPLETE:
        # refusal 时正文可能为空或半截，且不受 schema 约束
        raise LLMError(f"天机遮蔽：大模型非正常结束（stop_reason={resp.stop_reason}）")
    text = "".join(block.text for block in resp.content if block.type == "text")
    if not text:
        raise LLMError(f"天机遮蔽：大模型未返回正文（stop_reason={resp.stop_reason}）")
    return text
