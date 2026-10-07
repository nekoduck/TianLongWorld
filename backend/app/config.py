"""
[INPUT]: 依赖 pydantic-settings 的 BaseSettings，读取进程环境变量与 backend/.env
[OUTPUT]: 对外提供 Settings 配置模型、get_settings() 进程级单例
[POS]: app 的唯一配置入口，被 main.py（装配/CORS）与 llm/factory.py（模型选型）消费
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

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
    #  会话 —— 纯内存，容量封顶防止长跑进程无界增长
    # ------------------------------------------------------------------
    session_capacity: int = 1000
    history_turns: int = 4

    # ------------------------------------------------------------------
    #  HTTP
    # ------------------------------------------------------------------
    cors_origins: list[str] = ["http://localhost:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
