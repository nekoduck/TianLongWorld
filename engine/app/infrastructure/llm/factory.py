"""
[INPUT]: 依赖 app.config 的 Settings，依赖 llm 包内 AnthropicClient / GeminiClient / OpenAICompatClient，依赖 application/ports 的 LLMClient
[OUTPUT]: 对外提供 build_llm() —— 按 LLM_PROVIDER 装配 LLMClient；mock 返回 None（由组合根换上离线的解析器与叙事者）
[POS]: llm 包的唯一装配点，被 container.py 与 seed.py 调用；配置缺失在启动期就失败，而非等到第一位玩家出招
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from app.application.ports import LLMClient
from app.config import Settings
from app.infrastructure.llm.anthropic import AnthropicClient
from app.infrastructure.llm.gemini import GeminiClient
from app.infrastructure.llm.openai_compat import OpenAICompatClient


def build_llm(s: Settings) -> LLMClient | None:
    if s.llm_provider == "mock":
        return None
    if not (s.llm_api_key and s.llm_model):
        raise RuntimeError(f"LLM_PROVIDER={s.llm_provider} 需要同时配置 LLM_API_KEY 与 LLM_MODEL（参见 engine/.env.example）")
    if s.llm_provider == "anthropic":
        return AnthropicClient(
            api_key=s.llm_api_key,
            model=s.llm_model,
            base_url=s.llm_base_url or "https://api.anthropic.com",
            max_tokens=s.llm_max_tokens,
            timeout=s.llm_timeout,
            temperature=s.llm_temperature,
            effort=s.llm_effort,
        )
    if s.llm_provider == "gemini":
        return GeminiClient(
            api_key=s.llm_api_key,
            model=s.llm_model,
            base_url=s.llm_base_url or "https://generativelanguage.googleapis.com/v1beta",
            max_tokens=s.llm_max_tokens,
            timeout=s.llm_timeout,
            temperature=s.llm_temperature,
        )
    return OpenAICompatClient(
        api_key=s.llm_api_key,
        model=s.llm_model,
        base_url=s.llm_base_url or "https://api.openai.com/v1",
        timeout=s.llm_timeout,
        temperature=s.llm_temperature,
    )
