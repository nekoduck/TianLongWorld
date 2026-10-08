"""
[INPUT]: 依赖 httpx2 的 MockTransport / AsyncClient，依赖 app.infrastructure.llm 的三家客户端、_http 与 portable_schema，依赖 app.domain.intent 的 PlayerIntent
[OUTPUT]: 厂商客户端单测：报文形状（结构化输出 / 不下发采样参数 / JSON 模式只在需要时开）、SSE 流式解析、拒答与截断收敛为 LLMError、
          schema 规整（内联引用、剥离约束、对象封闭、字段名不被误删）、缺凭证启动即失败
[POS]: tests 的厂商边界：替换 httpx2 的传输层，不触网即可钉死三家协议的细节
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import json
from collections.abc import Callable
from typing import Any

import httpx2
import pytest

from app.config import Settings
from app.domain.intent import PlayerIntent
from app.errors import LLMError
from app.infrastructure.llm import _http
from app.infrastructure.llm.anthropic import AnthropicClient
from app.infrastructure.llm.factory import build_llm
from app.infrastructure.llm.gemini import GeminiClient
from app.infrastructure.llm.openai_compat import OpenAICompatClient
from app.infrastructure.llm.schema import portable_schema

SCHEMA = PlayerIntent.model_json_schema()
_REAL_CLIENT = httpx2.AsyncClient


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> Callable[[Callable[[httpx2.Request], httpx2.Response]], list[Any]]:
    """把 httpx2.AsyncClient 换成走 MockTransport 的真客户端：传输层之上的一切照常执行。"""

    def install(handler: Callable[[httpx2.Request], httpx2.Response]) -> list[Any]:
        seen: list[Any] = []

        def recording(request: httpx2.Request) -> httpx2.Response:
            seen.append({"url": str(request.url), "headers": request.headers, "json": json.loads(request.content)})
            return handler(request)

        monkeypatch.setattr(_http.httpx2, "AsyncClient",
                            lambda **kw: _REAL_CLIENT(transport=httpx2.MockTransport(recording), **kw))
        return seen

    return install


def sse(*events: dict[str, Any], done: bool = False) -> httpx2.Response:
    body = "".join(f"event: x\ndata: {json.dumps(e, ensure_ascii=False)}\n\n" for e in events)
    return httpx2.Response(200, text=body + ("data: [DONE]\n\n" if done else ""),
                           headers={"content-type": "text/event-stream"})


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
    with pytest.raises(LLMError, match="429"):
        await anthropic().complete("s", "u")
    with pytest.raises(LLMError, match="429"):
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
    with pytest.raises(ValueError, match="递归"):
        portable_schema({"$defs": {"A": {"$ref": "#/$defs/A"}}, "$ref": "#/$defs/A"})


def test_factory_fails_fast_without_credentials() -> None:
    assert build_llm(Settings(_env_file=None)) is None  # type: ignore[call-arg]
    with pytest.raises(RuntimeError, match="LLM_API_KEY"):
        build_llm(Settings(_env_file=None, llm_provider="anthropic"))  # type: ignore[call-arg]
