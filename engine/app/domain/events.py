"""
[INPUT]: 依赖 pydantic v2 的 BaseModel / TypeAdapter / Field(discriminator)，依赖 domain/models 的 Attitude，依赖 domain/intent 的 ActionType
[OUTPUT]: 对外提供 不可变领域事件 DomainEvent 基类及 PlayerSpawned / Moved / ItemTransferred / SkillLearned / SkillExecuted /
          Conversed / RelationChanged / ActionFailed / PlayerDied、CombatOutcome、AnyEvent 判别联合、EVENT_ADAPTER（JSONB 编解码）、
          EventEnvelope（流内版本 + 事件 id + 记录时间）
[POS]: domain 的事实词汇：世界此刻的一切都由这些事件经纯函数折叠而来；事件一经写入永不修改，
       新增事件类型只需在此加一个类并挂进 AnyEvent（开闭），时间戳只在信封上，事件本体保持确定性以便裁决可单测
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.domain.intent import ActionType
from app.domain.models import Attitude


class CombatOutcome(StrEnum):
    """从玩家视角看的一招结果。"""

    PREVAILED = "得手"  # 对手被制住
    STALEMATE = "相持"
    REPELLED = "受挫"  # 被击退，对方留了手
    FATAL = "毙命"  # 玩家身死（随后必有 PlayerDied）


class DomainEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PlayerSpawned(DomainEvent):
    type: Literal["PlayerSpawned"] = "PlayerSpawned"
    player_id: str
    name: str
    location_id: str


class Moved(DomainEvent):
    type: Literal["Moved"] = "Moved"
    from_location_id: str
    to_location_id: str
    exit_label: str


class ItemTransferred(DomainEvent):
    """物品易手。持有者 id 自带种类前缀（loc: / chr: / ply:），物品在任一时刻只有一个持有者。"""

    type: Literal["ItemTransferred"] = "ItemTransferred"
    item_id: str
    from_holder: str
    to_holder: str


class SkillLearned(DomainEvent):
    type: Literal["SkillLearned"] = "SkillLearned"
    skill_id: str
    source_id: str  # 传功之人（chr:）或所凭典籍（itm:）


class SkillExecuted(DomainEvent):
    """出手。skill_id 为空即徒手；item_id 只是叙事素材——兵器不改境界，世上没有天降神兵。"""

    type: Literal["SkillExecuted"] = "SkillExecuted"
    skill_id: str | None
    target_id: str
    item_id: str | None = None
    outcome: CombatOutcome


class Conversed(DomainEvent):
    type: Literal["Conversed"] = "Conversed"
    npc_id: str


class RelationChanged(DomainEvent):
    type: Literal["RelationChanged"] = "RelationChanged"
    character_id: str
    attitude: Attitude
    cause: str


class ActionFailed(DomainEvent):
    """失败同样是历史：被规则驳回的意图入账，叙事与记忆都据此知道"你试过、没成"。"""

    type: Literal["ActionFailed"] = "ActionFailed"
    action: ActionType
    target: str | None
    reason_code: str
    reason: str


class PlayerDied(DomainEvent):
    type: Literal["PlayerDied"] = "PlayerDied"
    cause: str
    killer_id: str | None = None


AnyEvent = Annotated[
    PlayerSpawned
    | Moved
    | ItemTransferred
    | SkillLearned
    | SkillExecuted
    | Conversed
    | RelationChanged
    | ActionFailed
    | PlayerDied,
    Field(discriminator="type"),
]

EVENT_ADAPTER: TypeAdapter[AnyEvent] = TypeAdapter(AnyEvent)


class EventEnvelope(BaseModel):
    """事件在流中的位置。version 从 1 起严格连续；乐观并发、投影检查点、记忆主键都以它为准。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stream_id: str
    version: int = Field(ge=1)
    event_id: UUID
    recorded_at: datetime
    event: AnyEvent
