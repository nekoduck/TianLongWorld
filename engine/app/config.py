"""
[INPUT]: 依赖 pydantic 的 Field、pydantic-settings 的 BaseSettings，读取进程环境变量与 engine/.env
[OUTPUT]: 对外提供 Settings 配置模型（含 llm_profile 按职责取模型与思考档位）、LLMRole 三种职责、Thinking 档位、ENGINE_ROOT 工程根路径、get_settings() 进程级单例
[POS]: 引擎的唯一配置入口，被 container.py（装配四类后端与大模型）、infrastructure/llm/factory.py（厂商选型）与 seed.py（语料与产物路径）消费；
       每一类存储都有 memory 实现：零依赖即可跑通整条管线，生产环境逐项切到 postgres / neo4j / qdrant
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ENGINE_ROOT = Path(__file__).resolve().parent.parent  # engine/：.env 与 data/ 锚定于此，与启动时的 cwd 无关

type Thinking = Literal["", "minimal", "low", "medium", "high"]


class LLMRole(StrEnum):
    """大模型在引擎里的三种无状态职责，各有各的取舍：解析要快、叙事要忠于快照且文笔好、抽取要准（离线、一次成型）。"""

    INTENT = "intent"
    NARRATION = "narration"
    EXTRACTION = "extraction"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENGINE_ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    # ------------------------------------------------------------------
    #  大模型 —— mock 为默认值：意图解析与叙事退化为确定性的离线实现，原著解析不可用
    # ------------------------------------------------------------------
    llm_provider: Literal["mock", "anthropic", "gemini", "openai"] = "mock"
    llm_api_key: str = ""
    llm_base_url: str = ""  # 留空则取 provider 官方地址
    llm_temperature: float | None = None  # None = 不下发（Claude 新模型拒收采样参数）
    llm_timeout: float = 120.0
    llm_max_tokens: int = 16000
    # 缺省档：三种职责未单独配置时共用。思考档位映射为 Gemini 的 thinkingLevel / Anthropic 的 effort，openai 兼容端忽略
    llm_model: str = ""
    llm_thinking: Thinking = ""
    # 按职责覆盖：留空即退回缺省档
    llm_intent_model: str = ""
    llm_intent_thinking: Thinking = ""
    llm_narration_model: str = ""
    llm_narration_thinking: Thinking = ""
    llm_extraction_model: str = ""
    llm_extraction_thinking: Thinking = ""

    # ------------------------------------------------------------------
    #  事件账本（PostgreSQL JSONB）
    # ------------------------------------------------------------------
    event_store: Literal["memory", "postgres"] = "memory"
    postgres_dsn: str = "postgresql://tlbb:tlbb@localhost:5432/tlbb"

    # ------------------------------------------------------------------
    #  图谱快照（Neo4j）
    # ------------------------------------------------------------------
    graph_backend: Literal["memory", "neo4j"] = "memory"
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""
    neo4j_database: str = "neo4j"

    # ------------------------------------------------------------------
    #  长线记忆（Qdrant）—— ":memory:" 为客户端本地模式，零服务即可运行
    # ------------------------------------------------------------------
    qdrant_url: str = ":memory:"
    qdrant_api_key: str = ""
    qdrant_collection: str = "tlbb_memories"
    memory_recall_k: int = Field(default=4, ge=1, le=10)  # 每回合注入叙事 Prompt 的记忆条数，封顶使载荷恒定

    # ------------------------------------------------------------------
    #  原著播种
    # ------------------------------------------------------------------
    source_text_dir: Path = ENGINE_ROOT / "data" / "source_text"
    world_dir: Path = ENGINE_ROOT / "data" / "world"  # blueprint.json / seed.cypher / 抽取缓存
    extraction_chunk_chars: int = Field(default=6000, ge=1000, le=30000)
    extraction_concurrency: int = Field(default=4, ge=1, le=32)

    @property
    def blueprint_path(self) -> Path:
        return self.world_dir / "blueprint.json"

    def llm_profile(self, role: LLMRole) -> tuple[str, str]:
        """某职责实际使用的（模型, 思考档位）：职责专属配置优先，留空退回缺省档。"""
        model: str = getattr(self, f"llm_{role}_model") or self.llm_model
        thinking: str = getattr(self, f"llm_{role}_thinking") or self.llm_thinking
        return model, thinking


@lru_cache
def get_settings() -> Settings:
    return Settings()
