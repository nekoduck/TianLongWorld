"""
[INPUT]: 依赖 pytest 的 MonkeyPatch / raises，依赖 app.llm 的 gemini / openai_compat / anthropic 模块与 schema.strict_json_schema，
         依赖 app.schemas 的 DIRECTOR_SCHEMA，依赖 app.errors 的 LLMError；缺凭证用例惰性导入 app.config / app.llm.factory
[OUTPUT]: 厂商层结构化输出单测：三家请求报文形状（API 层 schema 约束）、响应封套解析与截断/拒答/拦截的 LLMError 收敛、
          严格模式 schema 整形的递归性质，以及缺凭证启动即失败
[POS]: tests 中守护 llm 包对外协议的用例集：替换各客户端模块内的 post_json 捕获请求、返回伪造响应，从不触网；
       factory 惰性导入，使其传递依赖（mock → director）的改造不影响厂商层用例的收集
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import asyncio
import copy
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any

import pytest
from pydantic import BaseModel, Field

from app.errors import LLMError
from app.llm import anthropic, gemini, openai_compat
from app.llm.schema import strict_json_schema
from app.schemas import DIRECTOR_SCHEMA

_DROPPED = ("default", "title", "minLength", "maxLength", "minItems", "maxItems", "pattern", "format")
_REPLY = {"scene_description": "雁门关外，朔风如刀。", "game_over": False}


# ============================================================
#  装置 —— 以伪造的 post_json 截获请求
# ============================================================
@dataclass
class Sent:
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)


def _capture(monkeypatch: pytest.MonkeyPatch, module: ModuleType, response: object) -> Sent:
    sent = Sent()

    async def fake_post_json(url: str, *, headers: dict[str, str], payload: dict[str, Any], timeout: float) -> object:
        sent.url, sent.headers, sent.payload = url, headers, payload
        return response

    monkeypatch.setattr(module, "post_json", fake_post_json)
    return sent


def _nodes(value: object) -> Iterator[dict[str, Any]]:
    """深度优先遍历 JSON 中的每个对象节点。"""
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _nodes(child)


def _assert_strict(schema: dict[str, Any]) -> None:
    for node in _nodes(schema):
        assert not set(_DROPPED) & set(node), node
        if node.get("type") == "object":
            assert node["additionalProperties"] is False, node
            assert node["required"] == list(node["properties"]), node
        if "$ref" in node:
            assert len(node) == 1, f"严格模式拒收带兄弟关键字的 $ref：{node}"


def _gemini(*, thinking_level: str | None = "low", temperature: float | None = None) -> gemini.GeminiClient:
    return gemini.GeminiClient(
        api_key="k",
        model="gemini-flash-latest",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        temperature=temperature,
        timeout=5,
        max_tokens=99,
        thinking_level=thinking_level,
    )


def _openai(*, strict_schema: bool = True, temperature: float | None = None) -> openai_compat.OpenAICompatClient:
    return openai_compat.OpenAICompatClient(
        api_key="k",
        model="gpt-x",
        base_url="https://api.openai.com/v1/",
        temperature=temperature,
        timeout=5,
        strict_schema=strict_schema,
    )


def _anthropic() -> anthropic.AnthropicClient:
    return anthropic.AnthropicClient(
        api_key="k", model="m", base_url="https://api.anthropic.com/", temperature=None, timeout=5, max_tokens=99
    )


# ============================================================
#  Gemini —— responseJsonSchema + 思考档位
# ============================================================
def test_gemini_payload_enforces_schema_and_filters_thoughts(monkeypatch: pytest.MonkeyPatch) -> None:
    reply = {"candidates": [{"content": {"parts": [{"text": "想想", "thought": True}, {"text": "{}"}]}}]}
    sent = _capture(monkeypatch, gemini, reply)
    assert asyncio.run(_gemini().complete("SYS", "USER", DIRECTOR_SCHEMA)) == "{}"
    assert sent.url == "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent"
    assert sent.headers == {"x-goog-api-key": "k"}
    config = sent.payload["generationConfig"]
    assert config["responseJsonSchema"] == DIRECTOR_SCHEMA
    assert config["responseMimeType"] == "application/json"
    assert config["thinkingConfig"] == {"thinkingLevel": "low"}
    assert config["maxOutputTokens"] == 99
    assert sent.payload["systemInstruction"] == {"parts": [{"text": "SYS"}]}
    assert sent.payload["contents"] == [{"role": "user", "parts": [{"text": "USER"}]}]


def test_gemini_omits_unset_knobs(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _capture(monkeypatch, gemini, {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]})
    asyncio.run(_gemini(thinking_level=None).complete("SYS", "USER", DIRECTOR_SCHEMA))
    config = sent.payload["generationConfig"]
    assert "thinkingConfig" not in config and "temperature" not in config
    assert config["responseJsonSchema"] == DIRECTOR_SCHEMA


def test_gemini_max_tokens_raises_truncation(monkeypatch: pytest.MonkeyPatch) -> None:
    reply = {"candidates": [{"content": {"parts": [{"text": '{"scene_'}]}, "finishReason": "MAX_TOKENS"}]}
    _capture(monkeypatch, gemini, reply)
    with pytest.raises(LLMError, match="截断"):
        asyncio.run(_gemini().complete("SYS", "USER", DIRECTOR_SCHEMA))


def test_gemini_blocked_prompt_raises_with_block_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    _capture(monkeypatch, gemini, {"promptFeedback": {"blockReason": "SAFETY"}})
    with pytest.raises(LLMError, match="SAFETY"):
        asyncio.run(_gemini().complete("SYS", "USER", DIRECTOR_SCHEMA))


def test_gemini_blocked_candidate_without_text_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _capture(monkeypatch, gemini, {"candidates": [{"finishReason": "SAFETY"}]})
    with pytest.raises(LLMError, match="finishReason=SAFETY"):
        asyncio.run(_gemini().complete("SYS", "USER", DIRECTOR_SCHEMA))


# ============================================================
#  OpenAI 兼容 —— json_schema 严格模式 / json_object 退路
# ============================================================
def test_openai_strict_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _capture(monkeypatch, openai_compat, {"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]})
    assert asyncio.run(_openai(temperature=0.9).complete("SYS", "USER", DIRECTOR_SCHEMA)) == "{}"
    assert sent.url == "https://api.openai.com/v1/chat/completions"
    assert sent.headers == {"Authorization": "Bearer k"}
    assert sent.payload["messages"] == [{"role": "system", "content": "SYS"}, {"role": "user", "content": "USER"}]
    assert sent.payload["temperature"] == 0.9
    assert "max_tokens" not in sent.payload

    fmt = sent.payload["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["name"] == "director_output"
    assert fmt["json_schema"]["strict"] is True
    schema = fmt["json_schema"]["schema"]
    _assert_strict(schema)
    assert "maxLength" not in json.dumps(schema)


def test_openai_lenient_endpoint_falls_back_to_json_object(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _capture(monkeypatch, openai_compat, {"choices": [{"message": {"content": "{}"}}]})
    assert asyncio.run(_openai(strict_schema=False).complete("SYS", "USER", DIRECTOR_SCHEMA)) == "{}"
    assert sent.payload["response_format"] == {"type": "json_object"}
    assert "temperature" not in sent.payload


@pytest.mark.parametrize(
    "response",
    [
        {"choices": [{"message": {"content": None}, "finish_reason": "stop"}]},
        {"choices": [{"message": {"content": None, "refusal": "抱歉，无法协助"}, "finish_reason": "stop"}]},
    ],
)
def test_openai_missing_content_or_refusal_raises(monkeypatch: pytest.MonkeyPatch, response: dict[str, Any]) -> None:
    _capture(monkeypatch, openai_compat, response)
    with pytest.raises(LLMError, match="拒答"):
        asyncio.run(_openai().complete("SYS", "USER", DIRECTOR_SCHEMA))


def test_openai_length_finish_raises_truncation(monkeypatch: pytest.MonkeyPatch) -> None:
    _capture(monkeypatch, openai_compat, {"choices": [{"message": {"content": '{"scene'}, "finish_reason": "length"}]})
    with pytest.raises(LLMError, match="截断"):
        asyncio.run(_openai().complete("SYS", "USER", DIRECTOR_SCHEMA))


@pytest.mark.parametrize("response", [{"error": "boom"}, {"choices": []}, {"choices": "x"}, [], "oops", None])
def test_openai_malformed_envelope_raises(monkeypatch: pytest.MonkeyPatch, response: object) -> None:
    _capture(monkeypatch, openai_compat, response)
    with pytest.raises(LLMError):
        asyncio.run(_openai().complete("SYS", "USER", DIRECTOR_SCHEMA))


# ============================================================
#  Anthropic —— 强制工具调用
# ============================================================
def test_anthropic_forces_tool_and_returns_its_input(monkeypatch: pytest.MonkeyPatch) -> None:
    reply = {
        "content": [
            {"type": "text", "text": "好的，以下是裁决："},
            {"type": "tool_use", "id": "toolu_1", "name": "submit_director_output", "input": _REPLY},
        ],
        "stop_reason": "tool_use",
    }
    sent = _capture(monkeypatch, anthropic, reply)
    text = asyncio.run(_anthropic().complete("SYS", "USER", DIRECTOR_SCHEMA))
    assert json.loads(text) == _REPLY
    assert "雁门关" in text  # ensure_ascii=False：中文原样回传

    assert sent.url == "https://api.anthropic.com/v1/messages"
    assert sent.headers == {"x-api-key": "k", "anthropic-version": "2023-06-01"}
    assert sent.payload["system"] == "SYS" and sent.payload["max_tokens"] == 99
    assert "temperature" not in sent.payload
    (tool,) = sent.payload["tools"]
    assert tool["name"] == "submit_director_output" and tool["description"]
    assert tool["input_schema"] == DIRECTOR_SCHEMA
    assert sent.payload["tool_choice"] == {"type": "tool", "name": "submit_director_output"}


def test_anthropic_max_tokens_raises_truncation(monkeypatch: pytest.MonkeyPatch) -> None:
    reply = {
        "content": [{"type": "tool_use", "id": "t", "name": "submit_director_output", "input": {}}],
        "stop_reason": "max_tokens",
    }
    _capture(monkeypatch, anthropic, reply)
    with pytest.raises(LLMError, match="截断"):
        asyncio.run(_anthropic().complete("SYS", "USER", DIRECTOR_SCHEMA))


@pytest.mark.parametrize(
    "content",
    [
        [{"type": "text", "text": "{}"}],
        [{"type": "tool_use", "id": "t", "name": "other_tool", "input": {}}],
        [],
    ],
)
def test_anthropic_without_matching_tool_use_raises(monkeypatch: pytest.MonkeyPatch, content: list[object]) -> None:
    _capture(monkeypatch, anthropic, {"content": content, "stop_reason": "end_turn"})
    with pytest.raises(LLMError, match="未提交裁决"):
        asyncio.run(_anthropic().complete("SYS", "USER", DIRECTOR_SCHEMA))


# ============================================================
#  strict_json_schema —— 严格模式整形
# ============================================================
def test_strict_schema_shapes_director_contract_without_mutating_it() -> None:
    before = copy.deepcopy(DIRECTOR_SCHEMA)
    strict = strict_json_schema(DIRECTOR_SCHEMA)
    assert DIRECTOR_SCHEMA == before

    _assert_strict(strict)
    props = strict["properties"]
    assert strict["required"] == list(DIRECTOR_SCHEMA["properties"])
    # 带 description 的 $ref 被展开：字段语义保留，嵌套对象同样封闭
    assert props["next_state"]["description"] == DIRECTOR_SCHEMA["properties"]["next_state"]["description"]
    assert props["next_state"]["required"] == ["location", "time", "weather", "health_status"]
    # anyOf / 裸 $ref / $defs 原样保留，被引用的定义同样封闭
    assert props["player_delta"] == {"$ref": "#/$defs/PlayerDelta"}
    assert props["options"]["anyOf"] == [{"$ref": "#/$defs/Options"}, {"type": "null"}]
    assert set(strict["$defs"]) == set(DIRECTOR_SCHEMA["$defs"])
    assert strict["$defs"]["PlayerDelta"]["required"] == ["buffs_debuffs", "social_traits", "inventory", "martial_arts"]
    assert strict["$defs"]["PlayerDelta"]["properties"]["inventory"]["required"] == ["add", "remove"]
    assert props["world_events"]["items"] == {"$ref": "#/$defs/WorldEvent"}

    strict["$defs"]["Options"]["required"].append("X")  # 结果与入参无别名
    assert DIRECTOR_SCHEMA == before


def test_strict_schema_keeps_field_names_that_collide_with_keywords() -> None:
    schema = {
        "type": "object",
        "properties": {"title": {"type": "string", "title": "Title", "maxLength": 5}, "format": {"type": "integer"}},
        "required": ["title"],
    }
    assert strict_json_schema(schema) == {
        "type": "object",
        "properties": {"title": {"type": "string"}, "format": {"type": "integer"}},
        "required": ["title", "format"],
        "additionalProperties": False,
    }


class _Node(BaseModel):
    label: str
    parent: "_Node" = Field(description="自引用且带 description：展开必须在环上止步")


def test_strict_schema_terminates_on_self_reference() -> None:
    strict = strict_json_schema(_Node.model_json_schema())  # 根节点是 {$ref, $defs}：先展开为对象，环上退回裸引用
    _assert_strict(strict)
    assert strict["type"] == "object"
    assert strict["properties"]["parent"] == {"$ref": "#/$defs/_Node"}
    assert strict["$defs"]["_Node"]["properties"]["parent"] == {"$ref": "#/$defs/_Node"}


def test_strict_schema_rejects_open_mapping() -> None:
    with pytest.raises(ValueError, match="开放映射"):
        strict_json_schema({"type": "object", "additionalProperties": {"type": "string"}})


# ============================================================
#  装配 —— 真实 provider 缺凭证启动即失败
# ============================================================
def test_real_provider_without_credentials_fails_fast() -> None:
    # 惰性导入：factory 会经 mock.py 牵出 director，其改造不应阻断上面厂商层用例的收集
    from app.config import Settings
    from app.llm.factory import build_llm

    # model_construct 跳过 .env 与进程环境变量：开发机上导出的真实密钥不会让用例失真
    with pytest.raises(RuntimeError, match="LLM_API_KEY"):
        build_llm(Settings.model_construct(llm_provider="anthropic"))
