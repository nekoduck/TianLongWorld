"""
[INPUT]: 依赖 json 的 dumps，依赖 pydantic 的 JsonValue，依赖 llm/_http.py 的 post_json / VendorEnvelope / parse_envelope，
         依赖 llm/base.py 的 JsonSchema，依赖 app.errors 的 LLMError
[OUTPUT]: 对外提供 AnthropicClient —— 实现 LLMClient 协议
[POS]: llm 包的 Anthropic Messages API 客户端，与 gemini.py / openai_compat.py 并列，由 factory.py 按配置择一。
       以强制工具调用实现结构化输出：唯一工具的 input_schema 即导演契约，tool_choice 钉死该工具，
       模型只能以 JSON 参数作答；取回的 tool_use.input 再序列化为文本，回到 LLMClient 的纯文本契约
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import json
from typing import Any

from pydantic import JsonValue

from app.errors import LLMError
from app.llm._http import VendorEnvelope, parse_envelope, post_json
from app.llm.base import JsonSchema

_API_VERSION = "2023-06-01"
_TOOL_NAME = "submit_director_output"
_TOOL_DESCRIPTION = "提交本回合的导演裁决（场景叙事、生死、选项、快照与增减）。这是作答的唯一方式，必须且只调用一次。"


# ============================================================
#  厂商响应封套 —— content 是异构块列表：text / tool_use / thinking……只认 tool_use
# ============================================================
class _Block(VendorEnvelope):
    type: str
    name: str | None = None
    input: dict[str, JsonValue] | None = None  # tool_use 块的参数：天然是 JSON 对象


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
            # 强制工具调用即结构化输出：Pydantic 原生 schema（含 $defs/$ref）直接作 input_schema，无需整形
            "tools": [{"name": _TOOL_NAME, "description": _TOOL_DESCRIPTION, "input_schema": schema}],
            "tool_choice": {"type": "tool", "name": _TOOL_NAME},
        }
        if self._temperature is not None:
            payload["temperature"] = self._temperature

        data = await post_json(self._url, headers=self._headers, payload=payload, timeout=self._timeout)
        return _extract_tool_input(parse_envelope(_MessageResponse, data))


def _extract_tool_input(resp: _MessageResponse) -> str:
    if resp.stop_reason == "max_tokens":
        # 触顶时工具参数可能残缺：在此点名真因，而不是让残缺 JSON 伪装成格式错误
        raise LLMError("天机中断：大模型输出被截断（stop_reason=max_tokens）")
    for block in resp.content:
        if block.type == "tool_use" and block.name == _TOOL_NAME and block.input is not None:
            # ensure_ascii=False：中文原样回传，日志与解析闸门所见即模型所写
            return json.dumps(block.input, ensure_ascii=False)
    raise LLMError(f"天机紊乱：大模型未提交裁决（stop_reason={resp.stop_reason}）")
