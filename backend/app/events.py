"""
[INPUT]: 依赖 pydantic 的 Field / TypeAdapter，依赖 app.schemas 的 Frozen 与 PlayerState / PlayerSnapshot / PlayerDelta / Options /
         WorldEvent / ActionType / Label 等契约类型
[OUTPUT]: 对外提供不可变事件 LifeBegan、TurnResolved、WorldEventRecorded，判别联合 LifeEvent 及其 TypeAdapter（LIFE_EVENTS、WORLD_EVENTS）
[POS]: app 的事件溯源词汇表：一切状态变更都先成为这里的一条事实，经 store.py 追加落库，再由 engine.py 纯函数投影为状态；
       事件记录的是运行时校验之后的确切事实（如解析后的物品原名），而非大模型的原始意图，因此回放与匹配规则的演进无关
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, TypeAdapter

from app.schemas import ActionType, Frozen, Label, Options, PlayerDelta, PlayerSnapshot, PlayerState, WorldEvent


# ============================================================
#  一条命（life 流）：开局 + 逐回合落定
# ============================================================
class LifeBegan(Frozen):
    """投胎：开局状态完全取自种子，大模型只贡献开场叙事、选项与在场者。"""

    kind: Literal["life_began"] = "life_began"
    world_id: UUID
    player: PlayerState
    scene: str
    options: Options
    entities: tuple[Label, ...]


class TurnResolved(Frozen):
    """一回合落定：快照、四本账的确切增减、局部环境、选项与生死，原子地作为一条事实追加。"""

    kind: Literal["turn_resolved"] = "turn_resolved"
    action_type: ActionType
    action: str
    scene: str
    snapshot: PlayerSnapshot
    changes: PlayerDelta  # 已解析为清单中的确切原名，回放时只做集合运算
    entities: tuple[Label, ...]  # 本回合结束时的在场者（换地图清空的裁决已在落库前完成）
    options: Options | None
    died: bool


LifeEvent = Annotated[LifeBegan | TurnResolved, Field(discriminator="kind")]


# ============================================================
#  一个世界（world 流）：只追加的大事记，跨投胎延续
# ============================================================
class WorldEventRecorded(Frozen):
    kind: Literal["world_event"] = "world_event"
    life_id: UUID  # 溯源：哪一世的玩家改变了世界线
    event: WorldEvent


LIFE_EVENTS: TypeAdapter[LifeEvent] = TypeAdapter(LifeEvent)
WORLD_EVENTS: TypeAdapter[WorldEventRecorded] = TypeAdapter(WorldEventRecorded)
