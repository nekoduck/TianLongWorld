"""
[INPUT]: 依赖 pytest 的 MonkeyPatch / raises，依赖 app.llm 的 gemini / openai_compat / anthropic 模块与 schema.strict_json_schema，
         依赖 app.schemas 的 DIRECTOR_SCHEMA，依赖 app.errors 的 LLMError；缺凭证用例惰性导入 app.config / app.llm.factory
[OUTPUT]: 厂商层结构化输出单测：三家请求报文形状（API 层 schema 约束：Gemini responseJsonSchema、OpenAI json_schema strict、
          Anthropic output_config.format）、响应封套解析与截断/拒答/拦截/一切非正常结束的 LLMError 收敛、
          严格模式 schema 整形的递归性质（组合分支与 $defs 内的对象、无 type 仅带 properties 的节点一律闭合，
          不支持的关键字逐层删除，开放映射——true、子 schema 与等价于 true 的空子 schema {}——一律拒绝），
          以及缺凭证启动即失败
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


@pytest.mark.parametrize("finish_reason", ["SAFETY", "RECITATION", "PROHIBITED_CONTENT", "OTHER"])
def test_gemini_abnormal_finish_with_partial_text_raises(monkeypatch: pytest.MonkeyPatch, finish_reason: str) -> None:
    # 被截停的候选可能带着半截正文：只有 STOP 才交给解析闸门
    reply = {"candidates": [{"content": {"parts": [{"text": '{"scene'}]}, "finishReason": finish_reason}]}
    _capture(monkeypatch, gemini, reply)
    with pytest.raises(LLMError, match=f"finishReason={finish_reason}"):
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


@pytest.mark.parametrize("finish_reason", ["content_filter", "tool_calls", "function_call"])
def test_openai_abnormal_finish_raises(monkeypatch: pytest.MonkeyPatch, finish_reason: str) -> None:
    # 半截正文不是幻觉：不能交给解析闸门被当作格式错误重采样
    reply = {"choices": [{"message": {"content": '{"scene'}, "finish_reason": finish_reason}]}
    _capture(monkeypatch, openai_compat, reply)
    with pytest.raises(LLMError, match="非正常结束"):
        asyncio.run(_openai().complete("SYS", "USER", DIRECTOR_SCHEMA))


@pytest.mark.parametrize("response", [{"error": "boom"}, {"choices": []}, {"choices": "x"}, [], "oops", None])
def test_openai_malformed_envelope_raises(monkeypatch: pytest.MonkeyPatch, response: object) -> None:
    _capture(monkeypatch, openai_compat, response)
    with pytest.raises(LLMError):
        asyncio.run(_openai().complete("SYS", "USER", DIRECTOR_SCHEMA))


# ============================================================
#  Anthropic —— 原生结构化输出（output_config.format）
# ============================================================
def test_anthropic_constrains_output_with_strict_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    reply = {
        "content": [
            {"type": "thinking", "thinking": "先想一想", "signature": "s"},
            {"type": "text", "text": json.dumps(_REPLY, ensure_ascii=False)},
        ],
        "stop_reason": "end_turn",
    }
    sent = _capture(monkeypatch, anthropic, reply)
    text = asyncio.run(_anthropic().complete("SYS", "USER", DIRECTOR_SCHEMA))
    assert json.loads(text) == _REPLY  # 思考块被丢弃，只取 text 正文

    assert sent.url == "https://api.anthropic.com/v1/messages"
    assert sent.headers == {"x-api-key": "k", "anthropic-version": "2023-06-01"}
    assert sent.payload["system"] == "SYS" and sent.payload["max_tokens"] == 99
    assert "temperature" not in sent.payload
    assert "tools" not in sent.payload and "tool_choice" not in sent.payload  # Opus/Sonnet 5.5 对强制工具调用报 400
    fmt = sent.payload["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["schema"] == strict_json_schema(DIRECTOR_SCHEMA)
    _assert_strict(fmt["schema"])


@pytest.mark.parametrize(
    ("stop_reason", "message"),
    [("max_tokens", "截断"), ("refusal", "非正常结束"), ("pause_turn", "非正常结束")],
)
def test_anthropic_abnormal_stop_raises(monkeypatch: pytest.MonkeyPatch, stop_reason: str, message: str) -> None:
    # 拒答或截断时正文不受 schema 保证：哪怕带着半截 JSON 也必须收敛为 LLMError
    _capture(monkeypatch, anthropic, {"content": [{"type": "text", "text": '{"scene'}], "stop_reason": stop_reason})
    with pytest.raises(LLMError, match=message):
        asyncio.run(_anthropic().complete("SYS", "USER", DIRECTOR_SCHEMA))


@pytest.mark.parametrize(
    "content",
    [[], [{"type": "thinking", "thinking": "……", "signature": "s"}], [{"type": "text", "text": ""}]],
)
def test_anthropic_without_text_raises(monkeypatch: pytest.MonkeyPatch, content: list[object]) -> None:
    _capture(monkeypatch, anthropic, {"content": content, "stop_reason": "end_turn"})
    with pytest.raises(LLMError, match="未返回正文"):
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


def test_strict_schema_closes_untyped_node_with_properties() -> None:
    # 手写 schema 常省略 type：只要带 properties 就是对象节点，同样闭合、全部必填
    assert strict_json_schema({"properties": {"a": {"type": "string"}}}) == {
        "properties": {"a": {"type": "string"}},
        "additionalProperties": False,
        "required": ["a"],
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


@pytest.mark.parametrize("combinator", ["anyOf", "allOf", "oneOf"])
def test_strict_schema_closes_inline_objects_inside_combinator_branches(combinator: str) -> None:
    # 内联对象（无 $ref）只有经组合关键字递归才会被闭合：一旦不递归，分支里的 required 与 additionalProperties 原样漏出
    branch = {"type": "object", "properties": {"name": {"type": "string"}, "count": {"type": "integer"}}, "required": ["name"]}
    schema = {"type": "object", "properties": {"loot": {combinator: [branch, {"type": "null"}]}}}
    assert strict_json_schema(schema)["properties"]["loot"] == {
        combinator: [
            {
                "type": "object",
                "properties": {"name": {"type": "string"}, "count": {"type": "integer"}},
                "required": ["name", "count"],
                "additionalProperties": False,
            },
            {"type": "null"},
        ]
    }


class _Relic(BaseModel):
    name: str
    origin: str = "无名"


class _Satchel(BaseModel):
    relic: _Relic


def test_strict_schema_closes_objects_under_defs() -> None:
    # Pydantic 默认 extra=ignore：$defs 中的定义既不封闭、也只把无默认值的字段列为必填
    strict = strict_json_schema(_Satchel.model_json_schema())
    assert strict["properties"]["relic"] == {"$ref": "#/$defs/_Relic"}
    assert strict["$defs"]["_Relic"] == {
        "type": "object",
        "properties": {"name": {"type": "string"}, "origin": {"type": "string"}},
        "required": ["name", "origin"],
        "additionalProperties": False,
    }


@pytest.mark.parametrize(("keyword", "value"), [("default", "甲"), ("pattern", "^[甲乙丙]$"), ("format", "date-time")])
def test_strict_schema_drops_unsupported_keyword_at_every_depth(keyword: str, value: str) -> None:
    # 关键字埋在属性、数组元素、组合分支与 $defs 四个深度：漏删任何一层，或任何一层不递归，都会留下它
    def leaf() -> dict[str, Any]:
        return {"type": "string", "description": "印记", keyword: value}

    schema = {
        "type": "object",
        "properties": {
            "flat": leaf(),
            "many": {"type": "array", "items": leaf()},
            "maybe": {"anyOf": [leaf(), {"type": "null"}]},
            "nested": {"$ref": "#/$defs/Seal"},
        },
        "$defs": {"Seal": {"type": "object", "properties": {"deep": leaf()}}},
    }
    bare = {"type": "string", "description": "印记"}
    strict = strict_json_schema(schema)
    assert strict["properties"] == {
        "flat": bare,
        "many": {"type": "array", "items": bare},
        "maybe": {"anyOf": [bare, {"type": "null"}]},
        "nested": {"$ref": "#/$defs/Seal"},
    }
    assert strict["$defs"]["Seal"]["properties"] == {"deep": bare}


class _LooseMap(BaseModel):
    notes: dict[str, Any]  # 裸 dict：additionalProperties 为 true


class _TypedMap(BaseModel):
    counts: dict[str, int]  # 有类型的映射：additionalProperties 为子 schema


@pytest.mark.parametrize(
    "schema",
    [
        _LooseMap.model_json_schema(),
        _TypedMap.model_json_schema(),
        {"type": "object", "additionalProperties": {}},  # 空子 schema 在 JSON Schema 里与 true 等价，且是假值
    ],
    ids=["additional_true", "additional_schema", "additional_empty_schema"],
)
def test_strict_schema_rejects_open_mapping(schema: dict[str, Any]) -> None:
    # 三种开放映射同样无从表达：静默改成 false 会让受限解码只允许输出 {}，必须在下发前显式失败
    with pytest.raises(ValueError, match="开放映射"):
        strict_json_schema(schema)


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
