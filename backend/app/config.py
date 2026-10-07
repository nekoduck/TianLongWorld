"""
[INPUT]: 依赖 pydantic-settings 的 BaseSettings，读取进程环境变量与 backend/.env
[OUTPUT]: 对外提供 Settings 配置模型、get_settings() 进程级单例
[POS]: app 的唯一配置入口，被 main.py（事件库路径、滑动窗口、JIT 检索上限、CORS）与 llm/factory.py（模型选型）消费；
       窗口与检索上限以 Field 约束钉死范围——上下文规模是架构红线，不是可随意调大的旋钮
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
    llm_max_tokens: int = 4096  # Gemini 的思考 token 也计入此上限，留足余量以免截断
    llm_thinking_level: str = "low"  # 仅 gemini：思考档位，low 把回合延迟压到约 5s；留空则用模型默认
    llm_strict_schema: bool = True  # 仅 openai 兼容：json_schema 严格模式；端点不支持（如 DeepSeek）时设 false 退回 json_object

    # ------------------------------------------------------------------
    #  持久化与记忆 —— 事件日志是唯一事实来源；注入大模型的上下文严格受限
    # ------------------------------------------------------------------
    database_path: str = "data/tianlong.db"  # 相对 backend/；":memory:" 为进程内库（测试用）
    history_turns: int = Field(default=4, ge=3, le=5)  # 滑动窗口：强制保留最新 3~5 回合
    memory_limit: int = Field(default=8, ge=1, le=20)  # JIT 检索最多注入的相关世界大事条数

    # ------------------------------------------------------------------
    #  HTTP
    # ------------------------------------------------------------------
    cors_origins: list[str] = ["http://localhost:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
