"""
[INPUT]: 依赖 pydantic v2 的 BaseModel / TypeAdapter / Field(discriminator)，依赖 domain/models 的 Attitude / Remedy，
         依赖 domain/intent 的 ActionType / Approach / Aim，依赖 domain/combat 的 CombatOutcome，依赖 domain/outcomes 的 SocialOutcome / CovertOutcome
[OUTPUT]: 对外提供 不可变领域事件 DomainEvent 基类及 PlayerSpawned / Moved（fleeing 标明夺路而逃）/ ItemTransferred / SkillPracticed /
          SkillExecuted（approach 手段）/ HealthChanged（source：blow 伤人 / rest 调息 / item 服药）/ Conversed（topic_id 话题）/
          RelationChanged（basis 关系称谓或缘由类别）/ ActionFailed（unlock 怎样才行、aim / approach 当时的所图与手段）/ PlayerDied /
          Parleyed 交涉 / FactLearned 得知见闻 / ItemConsumed 用掉随身之物 / Maneuvered 暗中取物、AnyEvent 判别联合、EVENT_ADAPTER（JSONB 编码）、
          decode_event()（JSONB 解码：先经上抛器把旧账升级为现行词汇）、EventEnvelope（流内版本 + 事件 id + 记录时间）
[POS]: domain 的事实词汇：世界此刻的一切都由这些事件经纯函数折叠而来；事件一经写入永不修改，
       新增事件类型只需在此加一个类并挂进 AnyEvent（开闭），时间戳只在信封上，事件本体保持确定性以便裁决可单测。
       词汇演进靠上抛（upcast）而不是改历史：废弃的 SkillLearned（"获得即学会"）在读出时折算为一次 SkillPracticed，
       旧的「受挫」折算为「轻伤」，旧账里 cause=="调息疗伤" 而没有 source 的气血回升读作 source="rest"——账本里的字节永远是当年写下的样子。
       一切新字段都有缺省值，旧账照读；唯一需要上抛的是 HealthChanged.source
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import json
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.domain.combat import CombatOutcome
from app.domain.intent import ActionType, Aim, Approach
from app.domain.models import Attitude, Remedy
from app.domain.outcomes import CovertOutcome, SocialOutcome


class DomainEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PlayerSpawned(DomainEvent):
    type: Literal["PlayerSpawned"] = "PlayerSpawned"
    player_id: str
    name: str
    location_id: str
    aptitude: float = Field(default=1.0, ge=0.5, le=1.5)  # 悟性系数：根骨天定，折算一切修习所得


class Moved(DomainEvent):
    type: Literal["Moved"] = "Moved"
    from_location_id: str
    to_location_id: str
    exit_label: str
    fleeing: bool = False  # 重伤后夺路而逃（而非自行离去）：出发地从此是逃离过的险地；旧账缺省即自行离去


class ItemTransferred(DomainEvent):
    """物品易手。持有者 id 自带种类前缀（loc: / chr: / ply:），物品在任一时刻只有一个持有者。"""

    type: Literal["ItemTransferred"] = "ItemTransferred"
    item_id: str
    from_holder: str
    to_holder: str


class SkillPracticed(DomainEvent):
    """
    修习一次：入门与精进是同一种事实。武学的火候不是一个布尔值，而是历次修习所得熟练度之和（reduce）经悟性折算的结果；
    获得秘籍（ItemTransferred 入行囊）只是多了一件东西，一分熟练度也不给。
    """

    type: Literal["SkillPracticed"] = "SkillPracticed"
    skill_id: str
    proficiency_gained: int = Field(ge=1)
    source_id: str | None = None  # 传功点拨之人（chr:）、所凭典籍（itm:），闭门苦练为 None


class SkillExecuted(DomainEvent):
    """出手。skill_id 为空即徒手；item_id 只是叙事素材——兵器不改境界，世上没有天降神兵。"""

    type: Literal["SkillExecuted"] = "SkillExecuted"
    skill_id: str | None
    target_id: str
    item_id: str | None = None
    outcome: CombatOutcome
    approach: Approach = Approach.PLAIN  # 武力 / 计谋……旧账缺省为寻常


class HealthChanged(DomainEvent):
    """
    气血涨落：负为受伤、正为复原。折叠时钳在 [0, MAX_HP]；归零不等于死——死亡永远是一条明写的 PlayerDied。
    source 说的是"哪一类涨落"：blow 交手或险物所伤、rest 调息、item 服药敷药；source_id 是伤人者（chr:）或所用之物（itm:）。
    """

    type: Literal["HealthChanged"] = "HealthChanged"
    delta: int
    cause: str
    source_id: str | None = None  # 伤人者（chr:）或所用之物；自行调息为 None
    source: Literal["blow", "rest", "item"] = "blow"


class Conversed(DomainEvent):
    type: Literal["Conversed"] = "Conversed"
    npc_id: str
    topic_id: str | None = None  # 落了地的话题（实体或见闻 id）


class RelationChanged(DomainEvent):
    type: Literal["RelationChanged"] = "RelationChanged"
    character_id: str
    attitude: Attitude
    cause: str
    basis: str = ""  # 关系称谓（「师徒」）或缘由类别（「物归原主」）：供人情阶梯的例外与恩怨的写法辨认


class ActionFailed(DomainEvent):
    """失败同样是历史：被规则驳回的意图入账，叙事与记忆都据此知道"你试过、没成"。"""

    type: Literal["ActionFailed"] = "ActionFailed"
    action: ActionType
    target: str | None
    reason_code: str
    reason: str
    unlock: str = ""  # 怎样才行（「信赖」「略有小成」……）：供心事线索与选项提示
    aim: Aim | None = None
    approach: Approach = Approach.PLAIN


class PlayerDied(DomainEvent):
    type: Literal["PlayerDied"] = "PlayerDied"
    cause: str
    killer_id: str | None = None


class Parleyed(DomainEvent):
    """交涉一场：对谁、图什么、凭什么手段、结局如何。leverage_ids 是领域从已知见闻里按 unlock 边确定性选出的筹码，大模型不提议。"""

    type: Literal["Parleyed"] = "Parleyed"
    npc_id: str
    aim: Aim
    approach: Approach
    outcome: SocialOutcome
    leverage_ids: tuple[str, ...] = ()


class FactLearned(DomainEvent):
    """得知一件见闻（fact:）：从谁那里听来（chr:）。"""

    type: Literal["FactLearned"] = "FactLearned"
    fact_id: str
    source_id: str


class ItemConsumed(DomainEvent):
    """用掉一件随身之物（服药、敷药）：它从此不在任何地方。气血的回升另由 HealthChanged(source="item") 明写。"""

    type: Literal["ItemConsumed"] = "ItemConsumed"
    item_id: str
    effect: Remedy


class Maneuvered(DomainEvent):
    """暗中取物（计谋 / 潜行）：向谁下手、得手与否、察觉与否。易手另由 ItemTransferred 明写。"""

    type: Literal["Maneuvered"] = "Maneuvered"
    item_id: str
    target_id: str
    approach: Approach
    outcome: CovertOutcome


AnyEvent = Annotated[
    PlayerSpawned
    | Moved
    | ItemTransferred
    | SkillPracticed
    | SkillExecuted
    | HealthChanged
    | Conversed
    | RelationChanged
    | ActionFailed
    | PlayerDied
    | Parleyed
    | FactLearned
    | ItemConsumed
    | Maneuvered,
    Field(discriminator="type"),
]

EVENT_ADAPTER: TypeAdapter[AnyEvent] = TypeAdapter(AnyEvent)


# ============================================================
#  上抛器 —— 旧词汇 → 现行词汇。只在读出时生效，账本本身一字不改
# ============================================================
LEGACY_MASTERY_POINTS = 45  # 旧账的"学会"按融会贯通（悟性 1.0）的熟练度折算：当年学会了，今天就不该变成初窥门径


def _skill_learned(raw: dict[str, Any]) -> dict[str, Any]:
    return {"type": "SkillPracticed", "skill_id": raw["skill_id"], "proficiency_gained": LEGACY_MASTERY_POINTS,
            "source_id": raw.get("source_id")}


def _skill_executed(raw: dict[str, Any]) -> dict[str, Any]:
    return {**raw, "outcome": "轻伤"} if raw.get("outcome") == "受挫" else raw


def _health_changed(raw: dict[str, Any]) -> dict[str, Any]:
    """source 之前的旧账：调息写的是 cause="调息疗伤"、没有来源——读作 rest，其余一律是 blow（缺省）。"""
    return {**raw, "source": "rest"} if "source" not in raw and raw.get("cause") == "调息疗伤" else raw


UPCASTERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "SkillLearned": _skill_learned,
    "SkillExecuted": _skill_executed,
    "HealthChanged": _health_changed,
}


def decode_event(payload: str | bytes | dict[str, Any]) -> AnyEvent:
    """账本 → 事件：先上抛、再校验。两种事件账本实现都只经这一个入口读出事件。"""
    raw = payload if isinstance(payload, dict) else json.loads(payload)
    upcast = UPCASTERS.get(raw.get("type", ""))
    return EVENT_ADAPTER.validate_python(upcast(raw) if upcast else raw)


class EventEnvelope(BaseModel):
    """事件在流中的位置。version 从 1 起严格连续；乐观并发、投影检查点、记忆主键都以它为准。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stream_id: str
    version: int = Field(ge=1)
    event_id: UUID
    recorded_at: datetime
    event: AnyEvent
