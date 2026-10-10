"""
[INPUT]: 依赖 abc 的 ABC / abstractmethod，依赖 domain/aggregates 的 PlayerState，依赖 domain/events 的 DomainEvent，依赖 domain/snapshot 的 LocalSnapshot
[OUTPUT]: 对外提供 LLMClient 抽象（complete 一次性补全 + stream 流式补全）、JsonSchema 别名；
          回合切面 TurnRecord（一回合入账的事件与前后两份状态、两张快照）与 TurnAspect 抽象（after_turn：订阅一回合入账的事件，与叙事并行运作，返回待第三批入账的事件）；
          议程拦截 AgendaContext（注入一位 NPC 议程简报的上下文：几行动机、可引用的因果线编号 → id）与 AgendaInterceptor 抽象（inject）
[POS]: application 拥有的端口：意图解析器、地下城主、叙事渲染器、原著解析管道只认 LLMClient，不感知厂商；
       大模型在引擎里只有无状态职责，没有一种能写状态——端口上也就没有任何写世界的方法，一切提议都要经领域闸门才成为事件。
       TurnAspect 是事件总线上的异步切面（编剧代理即其一）：它在查询侧与叙事并行，只读入账之后的世界、只提议事件，
       回合编排在玩家锁里复核后作第三批追加；AgendaInterceptor 在为 NPC 立议程之前注入上下文（编剧的因果线），不改议程闸门
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from app.domain.aggregates import PlayerState
from app.domain.events import DomainEvent
from app.domain.snapshot import LocalSnapshot

type JsonSchema = dict[str, Any]


class LLMClient(ABC):
    @abstractmethod
    async def complete(self, system: str, user: str, schema: JsonSchema | None = None) -> str:
        """纯文本进、纯文本出。schema 是契约：支持结构化输出的厂商据此约束采样，最终校验永远由调用方的 Pydantic 完成。"""

    async def stream(self, system: str, user: str) -> AsyncIterator[str]:
        """流式补全。默认把一次性补全当作单个分片吐出——不支持流式的实现无需改写即可替换（里氏替换）。"""
        yield await self.complete(system, user)


# ============================================================
#  回合切面 —— 订阅一回合入账的事件（事件总线上的异步切面），只提议、不落账
# ============================================================
@dataclass(frozen=True, slots=True)
class TurnRecord:
    """一回合入账之后的样子：events 是这一回合第一、二批入账的全部事件，before / after 是入账前后的玩家状态，scene_before / scene 是出招时与入账后的快照。"""

    player_id: str
    before: PlayerState
    after: PlayerState
    events: tuple[DomainEvent, ...]
    scene_before: LocalSnapshot
    scene: LocalSnapshot


class TurnAspect(ABC):
    @abstractmethod
    async def after_turn(self, turn: TurnRecord) -> list[DomainEvent]:
        """
        与叙事并行运作：读一回合入账之后的世界，返回待第三批入账的事件（只记伏笔与潜台词之类、不改物理事实的事件）。
        失灵一律返回 []、不抛错——切面绝不能拖垮回合。
        """


# ============================================================
#  议程拦截 —— 在为 NPC 立议程之前注入上下文（编剧的因果线）
# ============================================================
@dataclass(frozen=True, slots=True)
class AgendaContext:
    """注入一位 NPC 议程简报的上下文：lines 是几行动机（进简报的 <karma>），karma 是可引用的因果线编号 → id（进议程契约的枚举，闸门据此落地）。"""

    lines: tuple[str, ...] = ()
    karma: Mapping[str, str] = field(default_factory=dict)


class AgendaInterceptor(ABC):
    @abstractmethod
    def inject(self, state: PlayerState, npc_id: str) -> AgendaContext | None:
        """这位 NPC 的行止与哪些未了的因果线有物理交集（亲历、传闻、途经）：没有即 None。纯函数，不调大模型。"""
