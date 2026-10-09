"""
[INPUT]: 依赖 tests/conftest 的 wire（MockTransport 传输层），依赖 app.infrastructure.llm 的三家客户端、build_llm、CallBudget 与 portable_schema，
         依赖 app.domain.intent 的 PlayerIntent
[OUTPUT]: 厂商客户端单测：报文形状（结构化输出 / 不下发采样参数 / JSON 模式只在需要时开）、SSE 流式解析、拒答与截断收敛为 LLMError、
          schema 规整（内联引用、剥离约束、对象封闭、字段名不被误删、枚举保留）、缺凭证启动即失败、四职责各取各的模型与思考档位、Claude 无 minimal 档、
          限流可重试而欠费不可重试、调用次数保险丝（各职责共用、熔断的请求发不出去且不可重试）
[POS]: tests 的厂商边界：替换 httpx2 的传输层，不触网即可钉死三家协议的细节
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from typing import Any

import httpx2
import pytest

from app.config import LLMRole, Settings
from app.domain.intent import PlayerIntent
from app.errors import LLMError
from app.infrastructure.llm.anthropic import AnthropicClient
from app.infrastructure.llm.budget import CallBudget
from app.infrastructure.llm.factory import build_llm
from app.infrastructure.llm.gemini import GeminiClient
from app.infrastructure.llm.openai_compat import OpenAICompatClient
from app.infrastructure.llm.schema import portable_schema
from tests.conftest import sse

SCHEMA = PlayerIntent.model_json_schema()


def anthropic() -> AnthropicClient:
    return AnthropicClient(api_key="k", model="claude-opus-5-5", base_url="https://api.test", max_tokens=99, timeout=5)


async def test_anthropic_structured_output_without_sampling_params(wire: Any) -> None:
    seen = wire(lambda r: httpx2.Response(200, json={"content": [{"type": "text", "text": "{}"}],
                                                     "stop_reason": "end_turn"}))
    assert await anthropic().complete("系统", "用户", SCHEMA) == "{}"
    sent = seen[0]
    assert sent["url"] == "https://api.test/v1/messages" and sent["headers"]["x-api-key"] == "k"
    assert sent["json"]["output_config"]["format"]["type"] == "json_schema"
    assert "temperature" not in sent["json"] and sent["json"]["max_tokens"] == 99


async def test_anthropic_stream_and_refusal(wire: Any) -> None:
    wire(lambda r: sse(
        {"type": "message_start"},
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "剑光"}},
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "如雪"}},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
    ))
    assert [c async for c in anthropic().stream("s", "u")] == ["剑光", "如雪"]
    wire(lambda r: httpx2.Response(200, json={"content": [], "stop_reason": "refusal"}))
    with pytest.raises(LLMError, match="拒绝"):
        await anthropic().complete("s", "u")


async def test_gemini_filters_thoughts_and_streams_over_sse(wire: Any) -> None:
    seen = wire(lambda r: sse({"candidates": [{"content": {"parts": [{"text": "想", "thought": True},
                                                                       {"text": "山风"}]}}]}))
    client = GeminiClient(api_key="g", model="m", base_url="https://g.test", max_tokens=9, timeout=5, temperature=None)
    assert [c async for c in client.stream("s", "u")] == ["山风"]
    assert seen[0]["url"].endswith("/models/m:streamGenerateContent?alt=sse")
    assert "responseJsonSchema" not in seen[0]["json"]["generationConfig"]


async def test_openai_json_mode_only_when_a_schema_is_given(wire: Any) -> None:
    seen = wire(lambda r: httpx2.Response(200, json={"choices": [{"message": {"content": "文"}}]}))
    client = OpenAICompatClient(api_key="o", model="m", base_url="https://o.test/v1", timeout=5, temperature=0.3)
    await client.complete("s", "u")
    await client.complete("s", "u", SCHEMA)
    assert "response_format" not in seen[0]["json"] and seen[1]["json"]["response_format"] == {"type": "json_object"}
    assert seen[0]["json"]["temperature"] == 0.3


async def test_openai_stream_ignores_done_sentinel(wire: Any) -> None:
    wire(lambda r: sse({"choices": [{"delta": {"content": "甲"}}]}, {"choices": [{"delta": {}}]}, done=True))
    client = OpenAICompatClient(api_key="o", model="m", base_url="https://o.test/v1", timeout=5, temperature=None)
    assert [c async for c in client.stream("s", "u")] == ["甲"]


async def test_http_errors_become_llm_errors(wire: Any) -> None:
    wire(lambda r: httpx2.Response(429, text="slow down"))
    with pytest.raises(LLMError, match="429") as limited:
        await anthropic().complete("s", "u")
    assert limited.value.retryable  # 限流：退避后可再试
    with pytest.raises(LLMError, match="429"):
        [c async for c in anthropic().stream("s", "u")]
    wire(lambda r: httpx2.Response(402, json={"error": {"message": "prepayment credits are depleted"}}))
    with pytest.raises(LLMError, match="402") as broke:
        await anthropic().complete("s", "u")
    assert not broke.value.retryable  # 欠费：重试只会再失败一次
    daily = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": (  # 2026-10 实测原文（节选）
        "Quota exceeded for metric: generativelanguage.googleapis.com/generate_requests_per_model_per_day, "
        "limit: 250, model: gemini-3.1-pro\nPlease retry in 8h36m27s.")}}
    wire(lambda r: httpx2.Response(429, json=daily))
    with pytest.raises(LLMError, match="当日配额已耗尽") as exhausted:
        await anthropic().complete("s", "u")
    assert not exhausted.value.retryable  # 按天计的配额：几秒后重试只会再撞一次墙
    with pytest.raises(LLMError, match="当日配额已耗尽"):
        [c async for c in anthropic().stream("s", "u")]


def test_portable_schema() -> None:
    schema = {
        "$defs": {"Inner": {"type": "object", "title": "Inner", "properties": {"title": {"type": "string", "maxLength": 3}}}},
        "type": "object",
        "properties": {"inner": {"$ref": "#/$defs/Inner"}, "n": {"type": "integer", "minimum": 0, "default": 1}},
    }
    assert portable_schema(schema) == {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "inner": {"type": "object", "additionalProperties": False, "properties": {"title": {"type": "string"}}},
            "n": {"type": "integer"},
        },
    }
    enum = {"type": "object", "properties": {"outcome_type": {"type": "string", "enum": ["MINOR_WOUND", "SEVERE_WOUND"]}}}
    assert portable_schema(enum)["properties"]["outcome_type"]["enum"] == ["MINOR_WOUND", "SEVERE_WOUND"]  # 地下城主的区间靠它
    with pytest.raises(ValueError, match="递归"):
        portable_schema({"$defs": {"A": {"$ref": "#/$defs/A"}}, "$ref": "#/$defs/A"})


def test_factory_fails_fast_without_credentials() -> None:
    assert build_llm(Settings(_env_file=None), LLMRole.INTENT) is None  # type: ignore[call-arg]
    with pytest.raises(RuntimeError, match="LLM_EXTRACTION_MODEL"):
        build_llm(Settings(_env_file=None, llm_provider="anthropic", llm_api_key="k"), LLMRole.EXTRACTION)  # type: ignore[call-arg]
    with pytest.raises(RuntimeError, match="LLM_RESOLUTION_MODEL"):
        build_llm(Settings(_env_file=None, llm_provider="gemini", llm_api_key="k"), LLMRole.RESOLUTION)  # type: ignore[call-arg]


async def test_each_role_gets_its_own_model_and_thinking(wire: Any) -> None:
    seen = wire(lambda r: httpx2.Response(200, json={"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}))
    s = Settings(_env_file=None, llm_provider="gemini", llm_api_key="g", llm_model="flash", llm_thinking="low",  # type: ignore[call-arg]
                 llm_intent_model="flash-lite", llm_intent_thinking="minimal", llm_extraction_model="pro",
                 llm_resolution_thinking="medium")
    assert list(LLMRole) == [LLMRole.INTENT, LLMRole.NARRATION, LLMRole.EXTRACTION, LLMRole.RESOLUTION, LLMRole.AGENDA]
    for role in LLMRole:
        client = build_llm(s, role)
        assert client is not None
        await client.complete("s", "u")
    urls = [x["url"].rsplit("/", 1)[-1] for x in seen]
    levels = [x["json"]["generationConfig"]["thinkingConfig"]["thinkingLevel"] for x in seen]
    assert urls == ["flash-lite:generateContent", "flash:generateContent", "pro:generateContent", "flash:generateContent",
                    "flash:generateContent"]
    assert levels == ["minimal", "low", "low", "medium", "low"]  # 职责专属优先，留空退回缺省档（地下城主只配了思考档位，议程全留空）


async def test_the_call_fuse_blows_before_a_request_leaves(wire: Any) -> None:
    """保险丝在各职责之间共用、一次流式也算一次；熔断的那次请求根本发不出去，且不可重试——没有谁会对着它退避重试。"""
    seen = wire(lambda r: httpx2.Response(200, json={"candidates": [{"content": {"parts": [{"text": "{}"}]}}]})
                if "alt=sse" not in str(r.url) else sse({"candidates": [{"content": {"parts": [{"text": "山风"}]}}]}))
    s = Settings(_env_file=None, llm_provider="gemini", llm_api_key="g", llm_model="flash")  # type: ignore[call-arg]
    fuse = CallBudget(2)
    intent, narration = build_llm(s, LLMRole.INTENT, fuse), build_llm(s, LLMRole.NARRATION, fuse)
    assert intent is not None and narration is not None
    await intent.complete("s", "u")
    assert "".join([c async for c in narration.stream("s", "u")]) == "山风"
    with pytest.raises(LLMError, match="LLM_CALL_LIMIT=2") as blown:
        await intent.complete("s", "u")
    assert not blown.value.retryable and len(seen) == 2 and fuse.spent == 2
    unlimited = CallBudget(0)
    for _ in range(5):
        await build_llm(s, LLMRole.INTENT, unlimited).complete("s", "u")  # type: ignore[union-attr]
    assert unlimited.spent == 5 and Settings(_env_file=None).llm_call_limit == 500  # type: ignore[call-arg]


async def test_anthropic_has_no_minimal_effort(wire: Any) -> None:
    seen = wire(lambda r: httpx2.Response(200, json={"content": [{"type": "text", "text": "{}"}], "stop_reason": "end_turn"}))
    s = Settings(_env_file=None, llm_provider="anthropic", llm_api_key="k", llm_model="claude-opus-5-5",  # type: ignore[call-arg]
                 llm_thinking="minimal")
    await build_llm(s, LLMRole.INTENT).complete("s", "u")  # type: ignore[union-attr]
    assert seen[0]["json"]["output_config"] == {"effort": "low"}
