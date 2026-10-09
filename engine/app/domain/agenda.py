"""
[INPUT]: 依赖 pydantic v2 的 BaseModel / Field，依赖 enum 的 StrEnum，依赖 hashlib 的 sha1
[OUTPUT]: 对外提供 分层 NPC 生态的名词——NpcAgenda（宏观议程：谁、去哪、为何、轻重、何时立下）、AgendaEnd（议程如何了结：抵达 / 受阻 / 中断 / 败退 / 作罢）、
          EncounterKind（撞见：NPC 与玩家同处一地；狭路相逢：两位有开篇仇怨的核心 NPC 同处一地）、Encounter（一次中断：id = enc:<10hex>、种类、来者、对方、地点、刻）、
          encounter_id()、SkirmishOutcome（狭路相逢的结局：来者胜 / 在此者胜 / 两败俱伤 / 口角 / 相安无事，.fight 是否动了手）、AGENDA_CHARS
[POS]: domain 的 H-Agent 本体（只有名词，没有动词——推进、中断、裁决在 domain/npc.py，以免与 events 成环）：
       宏观战略层由大模型按执念与此地情报为核心 NPC 立下 NpcAgenda（经领域闸门才入账），微观执行层由寻路把他们一刻一刻地推向目标，
       相撞即中断（Encounter），交给裁决层坍缩成结局。议程与相撞都属于玩家的平行世界，由事件折叠进 PlayerState
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import hashlib
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

AGENDA_CHARS = 16


class AgendaEnd(StrEnum):
    ARRIVED = "抵达"
    BLOCKED = "受阻"  # 无路可通、被玩家制住
    INTERRUPTED = "中断"  # 两败俱伤之类，议程不了了之
    ROUTED = "败退"  # 狭路相逢落败而逃
    DROPPED = "作罢"  # 新一轮议程让他留守


class NpcAgenda(BaseModel):
    """宏观议程：一位核心 NPC 要去哪里、为何而去、有多要紧。issued_tick 即启程之刻——此后每一跳都由寻路按道路耗时推进。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    npc_id: str = Field(pattern=r"^chr:.+")
    target_id: str = Field(pattern=r"^loc:.+")
    intent: str = Field(min_length=2, max_length=AGENDA_CHARS)  # 战略意图：「去剑湖宫打探秘奥」
    priority: int = Field(default=2, ge=1, le=3)  # 1 寻常 / 2 要紧 / 3 志在必得
    issued_tick: int = Field(ge=0)


class EncounterKind(StrEnum):
    MEET_PLAYER = "撞见"  # 带议程的 NPC 与玩家同处一地（谁走向谁都算）
    CROSS_PATHS = "狭路相逢"  # 两位有开篇仇怨的核心 NPC 同处一地


class SkirmishOutcome(StrEnum):
    """狭路相逢的结局，来者是走进这处地方的那一位。"""

    COMER_WINS = "来者胜"
    HOLDER_WINS = "在此者胜"
    BOTH_HURT = "两败俱伤"
    QUARREL = "口角"
    PASS = "相安无事"

    @property
    def fight(self) -> bool:
        return self in (SkirmishOutcome.COMER_WINS, SkirmishOutcome.HOLDER_WINS, SkirmishOutcome.BOTH_HURT)


def encounter_id(kind: EncounterKind, npc_id: str, other_id: str, location_id: str, tick: int) -> str:
    return "enc:" + hashlib.sha1(f"{kind}|{npc_id}|{other_id}|{location_id}|{tick}".encode()).hexdigest()[:10]


class Encounter(BaseModel):
    """一次中断：行军被相撞打断，等裁决层把它坍缩成结局（EncounterResolved）。未决之前，两位当事的 NPC 都不再挪步。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^enc:[0-9a-f]{10}$")
    kind: EncounterKind
    npc_id: str = Field(pattern=r"^chr:.+")  # 来者
    other_id: str = Field(pattern=r"^(chr|ply):.+")  # 对方：玩家或另一位核心 NPC
    location_id: str = Field(pattern=r"^loc:.+")
    tick: int = Field(ge=0)
