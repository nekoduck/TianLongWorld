"""
[INPUT]: 依赖 pydantic 的 Field、pydantic-settings 的 BaseSettings，读取进程环境变量与 backend/.env
[OUTPUT]: 对外提供 Settings 配置模型、get_settings() 进程级单例
[POS]: app 的唯一配置入口，被 main.py（装配、滑动窗口长度、RAG 检索条数、CORS）与 llm/factory.py（模型选型）消费；
       窗口与两条检索路径的条数以 Field 约束钉死范围——Prompt 载荷恒定是架构红线，不是可随意调大的旋钮
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# .env 锚定在 backend/ 目录，与启动时的 cwd 无关
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    # ------------------------------------------------------------------
    #  大模型 —— mock 为默认值，保证零密钥可跑
    # ------------------------------------------------------------------
    llm_provider: Literal["mock", "gemini", "openai", "anthropic"] = "mock"
    llm_api_key: str = ""
    llm_model: str = ""
    llm_base_url: str = ""  # 留空则取 provider 官方地址
    llm_temperature: float | None = None  # None = 不下发，交给 provider 默认值
    llm_timeout: float = 60.0
    llm_max_tokens: int = 2048
    llm_thinking_level: str = "low"  # 仅 gemini：思考档位，low 把回合延迟压到约 5s；留空则用模型默认

    # ------------------------------------------------------------------
    #  会话与上下文 —— 纯内存，容量封顶防止长跑进程无界增长；窗口与检索条数封顶使 Prompt 长度恒定
    # ------------------------------------------------------------------
    session_capacity: int = 1000
    history_turns: int = Field(default=4, ge=3, le=5)  # 滑动窗口：喂给导演的最近回合原文，钉死在 3~5
    graph_limit: int = Field(default=12, ge=1, le=30)  # 关系图检索：每回合至多注入的关系网行数（台账大事 + 原著关系）
    semantic_top_k: int = Field(default=3, ge=1, le=10)  # 语义检索：每回合至多注入的相关往事与江湖常识条数

    # ------------------------------------------------------------------
    #  HTTP
    # ------------------------------------------------------------------
    cors_origins: list[str] = ["http://localhost:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
