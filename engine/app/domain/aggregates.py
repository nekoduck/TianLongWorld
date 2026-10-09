"""
[INPUT]: 依赖 domain/events 的全部领域事件（含 P1 的 Parleyed / FactLearned / ItemConsumed / Maneuvered）与 EventEnvelope，依赖 domain/models 的 Attitude，依赖 domain/progression 的 MAX_HP / Mastery / Vitality /
         mastery_of / vitality / aptitude_for，依赖 domain/combat 的 CombatOutcome / CombatProposal，依赖 domain/stakes 的 Proposal，
         依赖 domain/threads 的 Thread / fold，依赖 domain/intent 的 Approach / PlayerIntent，
         依赖 domain/rules 的 decide()（裁决），依赖 app.errors 的 UnknownPlayerError / PlayerDeadError
[OUTPUT]: 对外提供 PlayerState（不可变状态值：practice 熟练度之和、aptitude 悟性、hp 气血、came_from 来路、fled_from 逃离过的险地、
          focus 近来打过交道的人与物（至多 FOCUS_SIZE 个，新者在前）与 focus_fresh 上一个主动作是否正与它打交道、attitude_causes 人情的缘由、
          threads 心事线索（至多 6 条）、known_facts 已知见闻、consumed 用掉之物、recent_approaches 近来用过的手段（至多 RECENT_SIZE 个）、
          attempts 对每个对象出过几次有赌注的招（FortuneResolver 的种子），mastery / vitality / inventory 现算）、FOCUS_SIZE、RECENT_SIZE、
          evolve(state, event) 纯函数折叠、Player 聚合根（apply / from_history / replay / spawn / ensure_alive / mastery / decide）
[POS]: domain 的一致性边界：一位玩家的平行世界就是一条事件流，世界在这条流上相对原著的全部偏离（位置、行囊、武学火候、气血、
       被制住之人、人情冷暖、物品易手）都是 PlayerState 的字段。没有状态表——当前状态只能由 evolve 从头折叠事件流算出；
       渐进式状态只做加法：熟练度 = Σ SkillPracticed.proficiency_gained，气血 = MAX_HP + Σ HealthChanged.delta（钳位），
       火候与伤势是读取时经 progression 折算的语义标签，从不入账。
       焦点（focus）与恩怨缘由（attitude_causes）同样只由现有事件折叠：选项跟着剧情走、叙事知道仇从何来，都不需要新的事件；
       焦点「不新鲜」的判据认 HealthChanged.source=="rest"（服药的气血回升不算走开）。
       P1 的四种新事件各有折叠：Parleyed / Maneuvered 进焦点、手段与尝试次数，心事线索由 threads.fold 开合；
       FactLearned 记入已知见闻；ItemConsumed 记入 consumed，行囊派生时把它排除（易手覆盖照旧，用掉之物从此不在任何人身上）。
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
    FactLearned,
    HealthChanged,
    ItemConsumed,
    ItemTransferred,
    Maneuvered,
    Moved,
    Parleyed,
    PlayerDied,
    PlayerSpawned,
    RelationChanged,
    SkillExecuted,
    SkillPracticed,
)
from app.domain.intent import Approach, PlayerIntent
from app.domain.models import Attitude
from app.domain.progression import MAX_HP, Mastery, Vitality, aptitude_for, mastery_of, vitality
from app.domain.snapshot import LocalSnapshot
from app.domain.stakes import Proposal
from app.domain.threads import Thread
from app.domain.threads import fold as fold_threads
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
    came_from: str | None = None  # 上一次移动的出发地：重伤逃脱时沿来路退回
    fled_from: frozenset[str] = frozenset()  # 夺路逃离过的险地：仇人不会挪窝，逃跑绝不逃回那里
    aptitude: float = 1.0
    hp: int = MAX_HP
    alive: bool = True
    death_cause: str | None = None
    practice: Mapping[str, int] = field(default_factory=dict)  # 武学 → 历次修习所得熟练度之和
    item_holders: Mapping[str, str] = field(default_factory=dict)
    subdued: frozenset[str] = frozenset()
    attitudes: Mapping[str, Attitude] = field(default_factory=dict)
    attitude_causes: Mapping[str, str] = field(default_factory=dict)  # 人物 → 最近一次人情变化的缘由（RelationChanged.cause）
    focus: tuple[str, ...] = ()  # 近来亲手打过交道的人与物，新者在前、至多 FOCUS_SIZE 个（定义见 _engaged）
    focus_fresh: bool = False  # 上一个主动作是否正与 focus[0] 打交道：走开、调息、碰壁之后它就只是「先前」的人与事
    threads: tuple[Thread, ...] = ()  # 心事线索：未了的所图，新者在前、至多 THREADS_MAX 条（threads.py）
    known_facts: frozenset[str] = frozenset()  # 已知见闻（fact:）
    consumed: frozenset[str] = frozenset()  # 用掉之物：从此不在行囊、不在任何地方
    recent_approaches: tuple[Approach, ...] = ()  # 近来出招用过的手段，新者在前、至多 RECENT_SIZE 个
    attempts: Mapping[str, int] = field(default_factory=dict)  # 对象 → 出过几次有赌注的招（出手 / 交涉 / 暗取）

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
        """行囊不是一张表，而是"此刻持有者是我"且没用掉的那些物品——由物品易手与用掉的历史推导。"""
        return frozenset(
            item for item, holder in self.item_holders.items() if holder == self.player_id and item not in self.consumed
        )

    def attitude_of(self, character_id: str) -> Attitude:
        return self.attitudes.get(character_id, Attitude.NEUTRAL)


# ============================================================
#  焦点 —— 玩家亲手打过交道的人与物，供选项的显著性打分与记忆召回
# ============================================================
FOCUS_SIZE = 4
RECENT_SIZE = 4


def _engaged(event: DomainEvent, player_id: str) -> tuple[str, ...]:
    """
    一条事件里玩家亲手打过交道的实体，排在前面的更要紧：
      出手（SkillExecuted）→ 对手；攀谈（Conversed）→ 对方；
      易手（ItemTransferred）→ 另一端的人（从被制住者身上取、交还物主）在前，那件东西在后；
      修习（SkillPracticed）→ 传功点拨之人或所凭典籍（闭门苦练没有对象）；
      交涉（Parleyed）→ 对方；暗中取物（Maneuvered）→ 失主在前、那件东西在后。
    人情涟漪（RelationChanged）不算：目睹者并没有和你打交道，被你打的人已由出手记下；
    驳回（ActionFailed）的指称是原话而非实体 id，地点（Moved）是去处而非对象——都不进焦点。
    """
    match event:
        case SkillExecuted(target_id=target) | Conversed(npc_id=target):
            return (target,)
        case ItemTransferred(item_id=item, from_holder=giver, to_holder=taker):
            other = taker if giver == player_id else giver
            return (other, item) if other.startswith("chr:") else (item,)
        case SkillPracticed(source_id=str(source)):
            return (source,)
        case Parleyed(npc_id=target):
            return (target,)
        case Maneuvered(target_id=target, item_id=item):
            return (target, item)
    return ()


def _attempt(event: DomainEvent) -> tuple[str, Approach] | None:
    """有赌注的一招：对谁、用什么手段。尝试次数与近来手段由它们折叠。"""
    match event:
        case SkillExecuted(target_id=target, approach=approach) | Parleyed(npc_id=target, approach=approach):
            return target, approach
        case Maneuvered(target_id=target, approach=approach):
            return target, approach
    return None


def _refocus(focus: tuple[str, ...], engaged: tuple[str, ...]) -> tuple[str, ...]:
    return (*engaged, *(x for x in focus if x not in engaged))[:FOCUS_SIZE]


def _moves_on(event: DomainEvent) -> bool:
    """
    不与谁打交道的主动作：自行离去、调息（source="rest" 的气血回升；旧账经上抛器读作 rest）、闭门苦练。它们让焦点不再「新鲜」。
    交手的余波（人情涟漪、伤人者造成的气血涨落、身死、夺路而逃）属于上一招，不算；
    碰壁（ActionFailed）与静观一样什么也没改变——世界不变，菜单也该逐字不变。
    """
    match event:
        case Moved(fleeing=False) | SkillPracticed(source_id=None):
            return True
        case HealthChanged(source="rest"):
            return True
    return False


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
    if engaged := _engaged(event, state.player_id):
        state = replace(state, focus=_refocus(state.focus, engaged), focus_fresh=True)
    elif state.focus_fresh and _moves_on(event):
        state = replace(state, focus_fresh=False)
    if (threads := fold_threads(state.threads, event, state.player_id)) != state.threads:
        state = replace(state, threads=threads)
    if attempt := _attempt(event):
        target, approach = attempt
        state = replace(
            state,
            attempts={**state.attempts, target: state.attempts.get(target, 0) + 1},
            recent_approaches=(approach, *state.recent_approaches)[:RECENT_SIZE],
        )

    match event:
        case Moved(from_location_id=origin, to_location_id=destination, fleeing=fleeing):
            fled = state.fled_from | {origin} if fleeing else state.fled_from
            return replace(state, location_id=destination, came_from=origin, fled_from=fled)
        case ItemTransferred(item_id=item, to_holder=holder):
            return replace(state, item_holders={**state.item_holders, item: holder})
        case SkillPracticed(skill_id=skill, proficiency_gained=gained):
            return replace(state, practice={**state.practice, skill: state.practice.get(skill, 0) + gained})
        case HealthChanged(delta=delta):
            return replace(state, hp=min(MAX_HP, max(0, state.hp + delta)))
        case SkillExecuted(outcome=CombatOutcome.SUCCESS, target_id=target):
            return replace(state, subdued=state.subdued | {target})
        case RelationChanged(character_id=character, attitude=attitude, cause=cause):
            return replace(
                state,
                attitudes={**state.attitudes, character: attitude},
                attitude_causes={**state.attitude_causes, character: cause},
            )
        case PlayerDied(cause=cause):
            return replace(state, alive=False, death_cause=cause)
        case FactLearned(fact_id=fact):
            return replace(state, known_facts=state.known_facts | {fact})
        case ItemConsumed(item_id=item):
            return replace(state, consumed=state.consumed | {item})
        case SkillExecuted() | Conversed() | ActionFailed() | Parleyed() | Maneuvered():
            return state  # 只是历史（线索、焦点、手段、尝试次数已在上面折叠），不改变世界
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
        self, intent: PlayerIntent, snapshot: LocalSnapshot, proposal: Proposal | CombatProposal | None = None
    ) -> list[DomainEvent]:
        """
        命令侧入口：守住生死与身份两道门，其余交给纯函数裁决。proposal 是地下城主对胜负未定之事（出手 / 交涉 / 暗中）的提议，
        领域把它钳进可裁区间后才落为事件。返回尚未入账的事件，由调用方追加到事件流。
        """
        return rules.decide(intent, self.ensure_alive(), snapshot, proposal)
