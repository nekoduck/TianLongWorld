"""
[INPUT]: 依赖 app.config 的 Settings / LLMRole，依赖 domain 的端口与 WorldBlueprint，依赖 application 的总线 / 处理器 / 解析器 / 地下城主 / 选项 / 叙事 / 投影，
         依赖 infrastructure 的事件账本、图谱、记忆与大模型工厂的全部实现
[OUTPUT]: 对外提供 Container（总线 + 流水线 + 投影协调者 + 播种器 + 关闭钩子）、build_container()（按配置装配整个引擎）
[POS]: 引擎唯一的组合根（依赖注入）：只有这里知道"端口背后是谁"。四类后端各自二选一（memory / 生产实现），大模型缺席时
       换上离线解析器、规则裁决与白描说书人；意图解析、地下城主与叙事渲染按职责各取一套（模型, 思考档位）；其余模块只依赖抽象，互不 new 对方。
       测试经 blueprint / llm / resolver 参数注入替身，与生产走同一条装配路径
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app.application.bus import CommandBus
from app.application.handlers import TurnPipeline, register_handlers
from app.application.intent_parser import HeuristicIntentParser, IntentParser, LLMIntentParser
from app.application.narrator import FallbackNarrator, LLMNarrator, Narrator, TemplateNarrator
from app.application.options import OptionGenerator
from app.application.ports import LLMClient
from app.application.projections import ProjectionCoordinator
from app.application.resolution_agent import CanonicalResolver, LLMResolutionAgent, Resolver
from app.config import LLMRole, Settings
from app.domain.models import WorldBlueprint
from app.domain.ports import EventStore, WorldProjector, WorldReader, WorldSeeder
from app.infrastructure.llm.factory import build_llm
from app.infrastructure.persistence.memory_event_store import InMemoryEventStore
from app.infrastructure.persistence.memory_graph import InMemoryWorldGraph
from app.infrastructure.persistence.neo4j_graph import Neo4jWorldGraph
from app.infrastructure.persistence.postgres_event_store import PostgresEventStore
from app.infrastructure.persistence.qdrant_memory import QdrantNarrativeMemory

logger = logging.getLogger(__name__)

type _Graph = InMemoryWorldGraph | Neo4jWorldGraph


@dataclass
class Container:
    bus: CommandBus
    pipeline: TurnPipeline
    coordinator: ProjectionCoordinator
    store: EventStore
    reader: WorldReader
    projector: WorldProjector
    seeder: WorldSeeder
    closers: list[Callable[[], Awaitable[None]]] = field(default_factory=list)

    async def aclose(self) -> None:
        for close in reversed(self.closers):
            try:
                await close()
            except Exception:  # 关闭阶段尽力而为，一个后端失败不妨碍其余后端释放
                logger.exception("关闭后端失败")


async def build_container(
    settings: Settings,
    *,
    blueprint: WorldBlueprint | None = None,
    llm: LLMClient | None = None,
    resolver: Resolver | None = None,
) -> Container:
    """
    llm 是测试替身：同时顶替意图、地下城主与叙事三种在线职责（剧本按调用顺序编排）。
    resolver 显式指定地下城主，优先于 llm——测试借此注入一个越界的地下城主，证明钳位与速写作废由领域与流水线守住，
    而不依赖 LLMResolutionAgent 自己守规矩。
    """
    closers: list[Callable[[], Awaitable[None]]] = []

    store: EventStore
    if settings.event_store == "postgres":
        pg = await PostgresEventStore.connect(settings.postgres_dsn)
        closers.append(pg.close)
        store = pg
    else:
        store = InMemoryEventStore()

    graph: _Graph
    if settings.graph_backend == "neo4j":
        graph = await Neo4jWorldGraph.connect(
            settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password, database=settings.neo4j_database
        )
        closers.append(graph.close)
    else:
        graph = InMemoryWorldGraph()
    seed = blueprint
    if seed is None and settings.graph_backend == "memory" and settings.blueprint_path.exists():
        seed = WorldBlueprint.model_validate_json(settings.blueprint_path.read_text(encoding="utf-8"))
    if seed is not None:
        await graph.seed(seed)
    if not await graph.is_seeded():
        logger.warning("图谱尚未播种：玩家投胎会被拒绝，请先运行 python -m app.seed")

    memory = await QdrantNarrativeMemory.connect(
        settings.qdrant_url, api_key=settings.qdrant_api_key, collection=settings.qdrant_collection
    )
    closers.append(memory.close)

    # 意图、地下城主与叙事各用各的模型：解析要快，裁决要快且守区间（命令侧同步等它），叙事要忠于快照
    reader_llm = llm if llm is not None else build_llm(settings, LLMRole.INTENT)
    writer_llm = llm if llm is not None else build_llm(settings, LLMRole.NARRATION)
    parser: IntentParser = LLMIntentParser(reader_llm) if reader_llm else HeuristicIntentParser()
    if resolver is None:
        judge_llm = llm if llm is not None else build_llm(settings, LLMRole.RESOLUTION)
        resolver = LLMResolutionAgent(judge_llm) if judge_llm else CanonicalResolver()
    narrator: Narrator = (
        FallbackNarrator(LLMNarrator(writer_llm), TemplateNarrator()) if writer_llm else TemplateNarrator()
    )

    coordinator = ProjectionCoordinator(store=store, projector=graph, reader=graph, memory=memory)
    pipeline = TurnPipeline(
        store=store,
        reader=graph,
        coordinator=coordinator,
        memory=memory,
        parser=parser,
        resolver=resolver,
        options=OptionGenerator(),
        narrator=narrator,
        recall_k=settings.memory_recall_k,
    )
    bus = register_handlers(CommandBus(), pipeline)
    return Container(
        bus=bus, pipeline=pipeline, coordinator=coordinator, store=store,
        reader=graph, projector=graph, seeder=graph, closers=closers,
    )
