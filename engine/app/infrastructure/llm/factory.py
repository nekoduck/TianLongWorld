"""
[INPUT]: 依赖 app.config 的 Settings / LLMRole，依赖 llm 包内 AnthropicClient / GeminiClient / OpenAICompatClient，依赖 application/ports 的 LLMClient
[OUTPUT]: 对外提供 build_llm(settings, role) —— 按 LLM_PROVIDER 与该职责的（模型, 思考档位）装配 LLMClient；mock 返回 None（由组合根换上离线实现）
[POS]: llm 包的唯一装配点，四种职责各取各的（模型, 思考档位）：container.py 装配意图 / 叙事 / 地下城主三种在线职责，seed.py 装配离线的抽取职责；
       配置缺失在启动期就失败，而非等到第一位玩家出招
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from app.application.ports import LLMClient
from app.config import LLMRole, Settings
from app.infrastructure.llm.anthropic import AnthropicClient
from app.infrastructure.llm.gemini import GeminiClient
from app.infrastructure.llm.openai_compat import OpenAICompatClient


def build_llm(s: Settings, role: LLMRole) -> LLMClient | None:
    if s.llm_provider == "mock":
        return None
    model, thinking = s.llm_profile(role)
    if not (s.llm_api_key and model):
        raise RuntimeError(
            f"LLM_PROVIDER={s.llm_provider} 需要配置 LLM_API_KEY 与 LLM_MODEL（或 LLM_{role.upper()}_MODEL），参见 engine/.env.example"
        )
    if s.llm_provider == "anthropic":
        return AnthropicClient(
            api_key=s.llm_api_key,
            model=model,
            base_url=s.llm_base_url or "https://api.anthropic.com",
            max_tokens=s.llm_max_tokens,
            timeout=s.llm_timeout,
            temperature=s.llm_temperature,
            effort="low" if thinking == "minimal" else thinking,  # Claude 的 effort 没有 minimal 档
        )
    if s.llm_provider == "gemini":
        return GeminiClient(
            api_key=s.llm_api_key,
            model=model,
            base_url=s.llm_base_url or "https://generativelanguage.googleapis.com/v1beta",
            max_tokens=s.llm_max_tokens,
            timeout=s.llm_timeout,
            temperature=s.llm_temperature,
            thinking_level=thinking,
        )
    return OpenAICompatClient(
        api_key=s.llm_api_key,
        model=model,
        base_url=s.llm_base_url or "https://api.openai.com/v1",
        timeout=s.llm_timeout,
        temperature=s.llm_temperature,
    )
