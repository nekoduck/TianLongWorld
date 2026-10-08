"""
[INPUT]: 依赖 domain/events 的全部领域事件与 EventEnvelope，依赖 domain/models 的 Attitude，依赖 domain/progression 的 MAX_HP / Mastery / Vitality /
         mastery_of / vitality / aptitude_for，依赖 domain/combat 的 CombatOutcome / CombatProposal，
         依赖 domain/rules 的 decide()（裁决），依赖 domain/intent 的 PlayerIntent，依赖 app.errors 的 UnknownPlayerError / PlayerDeadError
[OUTPUT]: 对外提供 PlayerState（不可变状态值：practice 熟练度之和、aptitude 悟性、hp 气血，mastery / vitality 现算）、
          evolve(state, event) 纯函数折叠、Player 聚合根（apply / from_history / replay / spawn / ensure_alive / mastery / decide）
[POS]: domain 的一致性边界：一位玩家的平行世界就是一条事件流，世界在这条流上相对原著的全部偏离（位置、行囊、武学火候、气血、
       被制住之人、人情冷暖、物品易手）都是 PlayerState 的字段。没有状态表——当前状态只能由 evolve 从头折叠事件流算出；
       渐进式状态只做加法：熟练度 = Σ SkillPracticed.proficiency_gained，气血 = MAX_HP + Σ HealthChanged.delta（钳位），
       火候与伤势是读取时经 progression 折算的语义标签，从不入账。
       内存图谱投影（infrastructure/persistence/memory_graph.py）复用同一个 evolve，投影与真相因此同构
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from functools import reduce

from app.domain import rules
from app.domain.combat import CombatOutcome, CombatProposal
from app.domain.events import (
    ActionFailed,
    Conversed,
    DomainEvent,
    EventEnvelope,
    HealthChanged,
    ItemTransferred,
    Moved,
    PlayerDied,
    PlayerSpawned,
    RelationChanged,
    SkillExecuted,
    SkillPracticed,
)
from app.domain.intent import PlayerIntent
from app.domain.models import Attitude
from app.domain.progression import MAX_HP, Mastery, Vitality, aptitude_for, mastery_of, vitality
from app.domain.snapshot import LocalSnapshot
from app.errors import PlayerDeadError, UnknownPlayerError


@dataclass(frozen=True, slots=True)
class PlayerState:
    """
    玩家平行世界相对原著的全部偏离。映射字段在 evolve 中只做写时复制，从不原地修改。
    item_holders 只记离开过原位的物品：没出现在这里的物品仍在原著给它的位置。
    """

    player_id: str
    name: str
    location_id: str
    aptitude: float = 1.0
    hp: int = MAX_HP
    alive: bool = True
    death_cause: str | None = None
    practice: Mapping[str, int] = field(default_factory=dict)  # 武学 → 历次修习所得熟练度之和
    item_holders: Mapping[str, str] = field(default_factory=dict)
    subdued: frozenset[str] = frozenset()
    attitudes: Mapping[str, Attitude] = field(default_factory=dict)

    @property
    def skills(self) -> frozenset[str]:
        """已入门的武学：修习过至少一次。"""
        return frozenset(skill for skill, points in self.practice.items() if points > 0)

    def mastery(self, skill_id: str) -> Mastery | None:
        """火候 = 熟练度之和 × 悟性，未入门为 None。"""
        return mastery_of(self.practice.get(skill_id, 0), self.aptitude)

    @property
    def vitality(self) -> Vitality:
        return vitality(self.hp)

    @property
    def inventory(self) -> frozenset[str]:
        """行囊不是一张表，而是"此刻持有者是我"的那些物品——由物品易手的历史推导。"""
        return frozenset(item for item, holder in self.item_holders.items() if holder == self.player_id)

    def attitude_of(self, character_id: str) -> Attitude:
        return self.attitudes.get(character_id, Attitude.NEUTRAL)


# ============================================================
#  纯函数折叠 —— (状态, 事件) → 新状态；不读时钟、不查数据库、不抛业务异常
# ============================================================
def evolve(state: PlayerState | None, event: DomainEvent) -> PlayerState:
    if isinstance(event, PlayerSpawned):
        return PlayerState(
            player_id=event.player_id, name=event.name, location_id=event.location_id, aptitude=event.aptitude
        )
    if state is None:
        raise ValueError(f"事件流必须以 PlayerSpawned 开头，却遇到 {type(event).__name__}")

    match event:
        case Moved(to_location_id=destination):
            return replace(state, location_id=destination)
        case ItemTransferred(item_id=item, to_holder=holder):
            return replace(state, item_holders={**state.item_holders, item: holder})
        case SkillPracticed(skill_id=skill, proficiency_gained=gained):
            return replace(state, practice={**state.practice, skill: state.practice.get(skill, 0) + gained})
        case HealthChanged(delta=delta):
            return replace(state, hp=min(MAX_HP, max(0, state.hp + delta)))
        case SkillExecuted(outcome=CombatOutcome.SUCCESS, target_id=target):
            return replace(state, subdued=state.subdued | {target})
        case RelationChanged(character_id=character, attitude=attitude):
            return replace(state, attitudes={**state.attitudes, character: attitude})
        case PlayerDied(cause=cause):
            return replace(state, alive=False, death_cause=cause)
        case SkillExecuted() | Conversed() | ActionFailed():
            return state  # 只是历史，不改变世界
    raise TypeError(f"未知的领域事件：{type(event).__name__}")


# ============================================================
#  聚合根
# ============================================================
class Player:
    def __init__(self, player_id: str) -> None:
        self.id = player_id
        self.version = 0
        self._state: PlayerState | None = None

    @property
    def spawned(self) -> bool:
        return self._state is not None

    @property
    def state(self) -> PlayerState:
        if self._state is None:
            raise UnknownPlayerError(f"江湖中查无此人：{self.id}")
        return self._state

    def apply(self, event: DomainEvent) -> None:
        """吸收一条已发生的事实。重放与新事件走同一条路：聚合根没有第二种改变自己的方式。"""
        self._state = evolve(self._state, event)
        self.version += 1

    @classmethod
    def from_history(cls, player_id: str, history: Iterable[EventEnvelope]) -> "Player":
        player = cls(player_id)
        for envelope in history:
            if envelope.version != player.version + 1:
                raise ValueError(f"事件流断裂：期望版本 {player.version + 1}，得到 {envelope.version}")
            player.apply(envelope.event)
        return player

    @staticmethod
    def replay(events: Iterable[DomainEvent]) -> PlayerState | None:
        """不经聚合根、直接折叠一串事件：演示"状态 = reduce(evolve, 历史)"这一等式本身。"""
        return reduce(evolve, events, None)

    @staticmethod
    def spawn(player_id: str, name: str, location_id: str, aptitude: float | None = None) -> list[DomainEvent]:
        """投胎即定下悟性：缺省由 id 确定性抽出（根骨天定），写进事件后便与日后的抽法无关。"""
        gift = aptitude_for(player_id) if aptitude is None else aptitude
        return [PlayerSpawned(player_id=player_id, name=name, location_id=location_id, aptitude=gift)]

    def ensure_alive(self) -> PlayerState:
        """永久死亡：死者的事件流只读。命令在解析之前就该撞上这道门，免得为死人白白调用一次大模型。"""
        state = self.state
        if not state.alive:
            raise PlayerDeadError(f"{state.name}已经死了：{state.death_cause}")
        return state

    def mastery(self, skill_id: str) -> Mastery | None:
        """武学等级：本流里该武学全部 SkillPracticed 的熟练度之和（evolve 已逐条 reduce），经悟性系数折算。"""
        return self.state.mastery(skill_id)

    def decide(
        self, intent: PlayerIntent, snapshot: LocalSnapshot, proposal: CombatProposal | None = None
    ) -> list[DomainEvent]:
        """
        命令侧入口：守住生死与身份两道门，其余交给纯函数裁决。proposal 是地下城主对胜负未定之事的提议，
        领域把它钳进可裁区间后才落为事件。返回尚未入账的事件，由调用方追加到事件流。
        """
        return rules.decide(intent, self.ensure_alive(), snapshot, proposal)
