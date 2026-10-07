"""
[INPUT]: 依赖 app.config 的 Settings，依赖 llm 包内 MockLLM / GeminiClient / OpenAICompatClient / AnthropicClient
[OUTPUT]: 对外提供 build_llm() —— 按 LLM_PROVIDER 装配 LLMClient
[POS]: llm 包的唯一装配点，被 main.py 在启动时调用一次；配置缺失在启动期就失败，而非等到第一位玩家出招。
       Settings → 客户端参数的翻译只在此处发生（如 LLM_STRICT_SCHEMA → OpenAICompatClient.strict_schema），客户端不读配置
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from app.config import Settings
from app.llm.anthropic import AnthropicClient
from app.llm.base import LLMClient
from app.llm.gemini import GeminiClient
from app.llm.mock import MockLLM
from app.llm.openai_compat import OpenAICompatClient

_MOCK_LATENCY = 0.8


def build_llm(s: Settings) -> LLMClient:
    if s.llm_provider == "mock":
        return MockLLM(latency=_MOCK_LATENCY)
    if not (s.llm_api_key and s.llm_model):
        raise RuntimeError(
            f"LLM_PROVIDER={s.llm_provider} 需要同时配置 LLM_API_KEY 与 LLM_MODEL（参见 backend/.env.example）"
        )
    if s.llm_provider == "gemini":
        return GeminiClient(
            api_key=s.llm_api_key,
            model=s.llm_model,
            base_url=s.llm_base_url or "https://generativelanguage.googleapis.com/v1beta",
            temperature=s.llm_temperature,
            timeout=s.llm_timeout,
            max_tokens=s.llm_max_tokens,
            thinking_level=s.llm_thinking_level or None,
        )
    if s.llm_provider == "anthropic":
        return AnthropicClient(
            api_key=s.llm_api_key,
            model=s.llm_model,
            base_url=s.llm_base_url or "https://api.anthropic.com",
            temperature=s.llm_temperature,
            timeout=s.llm_timeout,
            max_tokens=s.llm_max_tokens,
        )
    return OpenAICompatClient(
        api_key=s.llm_api_key,
        model=s.llm_model,
        base_url=s.llm_base_url or "https://api.openai.com/v1",
        temperature=s.llm_temperature,
        timeout=s.llm_timeout,
        strict_schema=s.llm_strict_schema,  # DeepSeek 等只认 json_object 的端点设 LLM_STRICT_SCHEMA=false
    )
