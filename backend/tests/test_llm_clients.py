"""
[INPUT]: 依赖 pytest 的 monkeypatch，依赖 app.llm 的 GeminiClient / AnthropicClient / OpenAICompatClient / factory.build_llm，依赖 app.errors 的 LLMError
[OUTPUT]: 厂商客户端的报文形状与配置校验单测（替换 post_json，不触网）
[POS]: tests 中守护 llm 包对外协议的用例集，确保换厂商不必改 director
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import asyncio

import pytest

from app.config import Settings
from app.errors import LLMError
from app.llm import anthropic, gemini, openai_compat
from app.llm.factory import build_llm


def _capture(monkeypatch, module, response: dict) -> dict:
    sent: dict = {}

    async def fake_post_json(url, *, headers, payload, timeout):
        sent.update(url=url, headers=headers, payload=payload)
        return response

    monkeypatch.setattr(module, "post_json", fake_post_json)
    return sent


def test_anthropic_payload_and_text_extraction(monkeypatch):
    sent = _capture(monkeypatch, anthropic, {"content": [{"type": "text", "text": "{}"}], "stop_reason": "end_turn"})
    client = anthropic.AnthropicClient(
        api_key="k", model="m", base_url="https://api.anthropic.com/", temperature=None, timeout=5, max_tokens=99
    )
    assert asyncio.run(client.complete("SYS", "USER")) == "{}"
    assert sent["url"] == "https://api.anthropic.com/v1/messages"
    assert sent["headers"]["x-api-key"] == "k"
    assert sent["payload"]["system"] == "SYS" and sent["payload"]["max_tokens"] == 99
    assert "temperature" not in sent["payload"]


def test_openai_compat_payload_and_text_extraction(monkeypatch):
    sent = _capture(monkeypatch, openai_compat, {"choices": [{"message": {"content": "{}"}}]})
    client = openai_compat.OpenAICompatClient(
        api_key="k", model="deepseek-chat", base_url="https://api.deepseek.com/v1", temperature=0.9, timeout=5
    )
    assert asyncio.run(client.complete("SYS", "USER")) == "{}"
    assert sent["url"] == "https://api.deepseek.com/v1/chat/completions"
    assert sent["payload"]["response_format"] == {"type": "json_object"}
    assert sent["payload"]["temperature"] == 0.9


def _gemini(**overrides) -> gemini.GeminiClient:
    kwargs = dict(
        api_key="k", model="gemini-flash-latest", base_url="https://generativelanguage.googleapis.com/v1beta",
        temperature=None, timeout=5, max_tokens=99, thinking_level="low",
    )
    return gemini.GeminiClient(**(kwargs | overrides))


def test_gemini_payload_schema_and_thought_filtering(monkeypatch):
    reply = {"candidates": [{"content": {"parts": [{"text": "想想", "thought": True}, {"text": "{}"}]}}]}
    sent = _capture(monkeypatch, gemini, reply)
    assert asyncio.run(_gemini().complete("SYS", "USER", {"type": "object"})) == "{}"
    assert sent["url"].endswith("/v1beta/models/gemini-flash-latest:generateContent")
    assert sent["headers"] == {"x-goog-api-key": "k"}
    config = sent["payload"]["generationConfig"]
    assert config["responseJsonSchema"] == {"type": "object"}
    assert config["responseMimeType"] == "application/json"
    assert config["thinkingConfig"] == {"thinkingLevel": "low"}
    assert sent["payload"]["systemInstruction"]["parts"][0]["text"] == "SYS"


def test_gemini_omits_unset_knobs(monkeypatch):
    sent = _capture(monkeypatch, gemini, {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]})
    asyncio.run(_gemini(thinking_level=None).complete("SYS", "USER"))
    config = sent["payload"]["generationConfig"]
    assert "thinkingConfig" not in config and "responseJsonSchema" not in config and "temperature" not in config


def test_gemini_blocked_prompt_raises_llm_error(monkeypatch):
    _capture(monkeypatch, gemini, {"promptFeedback": {"blockReason": "SAFETY"}})
    with pytest.raises(LLMError, match="SAFETY"):
        asyncio.run(_gemini().complete("SYS", "USER"))


def test_malformed_vendor_response_raises_llm_error(monkeypatch):
    _capture(monkeypatch, openai_compat, {"error": "boom"})
    client = openai_compat.OpenAICompatClient(api_key="k", model="m", base_url="u", temperature=None, timeout=5)
    with pytest.raises(LLMError):
        asyncio.run(client.complete("SYS", "USER"))


def test_real_provider_without_credentials_fails_fast():
    with pytest.raises(RuntimeError, match="LLM_API_KEY"):
        build_llm(Settings(_env_file=None, llm_provider="anthropic"))
