"""
[INPUT]: 依赖 pydantic v2 的 BaseModel / Field / field_validator / model_validator，依赖 domain/clocks 的 NarrativeClock / ClockKind / clock_id 与上限，
         依赖 domain/combat 的 Stakes / CombatOutcome / HP_BANDS，依赖 domain/social 的 SocialStakes / CAP，依赖 domain/covert 的 CovertStakes，
         依赖 domain/outcomes 的 SocialOutcome / CovertOutcome，依赖 domain/stakes 的 AnyStakes / Outcome / Proposal，依赖 domain/approach 的 Route，
         依赖 domain/events 的 ClockStarted / ClockAdvanced / ClockCollapsed / ClockCleared / FactEmerged / RenownChanged / RelationChanged / HealthChanged，
         依赖 domain/models 的 Attitude，依赖 domain/snapshot 的 LocalSnapshot；PlayerState 仅作类型标注
[OUTPUT]: 对外提供 语义物理引擎的符号层——
          推演契约：Severity（爆炸 / 暗流）、ActionTrigger（无 / 交手 / 脱身 / 死亡判定）、ClockOp（新建 / 推进 / 回退 / 销毁）、DeltaKind 与 parse_delta_key()、
                    ClockMutation、ResolutionOutput（推理层 collision / severity / cost / convergence + 符号层 deltas / clock_mutations / new_facts / action_trigger）；
          物理边界：Envelope（路线、可裁结局、确定性裁决、舒适区差距 strain、可用的属性键）与 envelope_of()、delta_keys() / clock_anchors()（给简报与 schema 用）；
          定案：output_for()（规则或气运的结局 → 同形的推演输出）、settle()（推演 → Settlement：结局、扣减、量级、路由、附带事件）
[POS]: domain 的「神经-符号-神经」中间那一层：大模型（application/resolution_agent.py）读快照推演出一份结构化的 ResolutionOutput，
       这里把它当作一份提议逐项过物理闸门，定案后才成为事件，再交给说书人渲染。闸门守的是图谱的物理与因果：
         属性键只认图谱里已有的（气血、名望、在场者的人情、制住对手、所图得成），名字落不了地即作罢；
         结局由属性变化推出（制住 → 得手、所图得成 → 如愿 / 无痕、对方人情跌落 → 碰壁 / 翻脸 / 被察觉……），推出的结局须在 rules 圈出的可裁区间里，
         出界即整份推演作废、取确定性裁决——越级取胜依旧不在区间里，大模型说得再动听也写不进账；
         量级：声明「暗流」就只许软结局并必须挂上或推进一只时钟（没挂就由领域按路线补挂），硬结局与时钟坍缩一律是「爆炸」；
         等价交换：结局好过确定性裁决几格、或越出舒适区还要得手，就得付同样多格的代价（名望、旁人的人情、暗取时的气血、凶险时钟净添的格数——回退与销毁扣回；
                   只算满了还会有后果的时钟：挂在已被制住之人身上的敌意时钟他无从出手，不算），付不够的由领域补成对象身上一只凶险时钟的格数
                   （对象已被制住就折名望）——补满了它当场坍缩；
         时钟：只挂在眼前之物上、总数与每个挂处都有上限、一次推进至多三格、一回合每只至多动一次（补代价除外），满格即按种类坍缩为硬结算（疑心翻脸、敌意出手伤你、危机受创脱身、交情更进一步；伤人都留一口气，且算上这一回合已受的伤）；
         微观事实：至多三条一行中文、不得夹带改变属性 / 归属 / 生死 / 位置的字眼，点了名的场景实体随事件入图。
       交涉永不伤人、暗中与交涉永不致死，这两条旧铁律在新闸门里照样成立。没有大模型（离线、点选、保险丝熔断）时，
       规则或气运的结局经 output_for 变成同形的推演输出走同一道闸门——等价交换对谁都一样
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.approach import Route
from app.domain.clocks import (
    CLOCKS_MAX,
    CONSEQUENCE_CHARS,
    NAME_CHARS,
    PER_ANCHOR,
    STEP_MAX,
    ClockKind,
    NarrativeClock,
    clock_id,
)
from app.domain.combat import HP_BANDS, CombatOutcome, Stakes
from app.domain.events import (
    ClockAdvanced,
    ClockCleared,
    ClockCollapsed,
    ClockStarted,
    DomainEvent,
    FactEmerged,
    HealthChanged,
    RelationChanged,
    RenownChanged,
)
from app.domain.models import Attitude
from app.domain.outcomes import CovertOutcome, SocialOutcome
from app.domain.social import CAP, SocialStakes
from app.domain.stakes import AnyStakes, Outcome, Proposal

if TYPE_CHECKING:
    from app.domain.aggregates import PlayerState
    from app.domain.snapshot import LocalSnapshot

SELF = "你"  # 时钟挂在玩家自己身上、或属性键指玩家时的指称
FACTS_MAX = 3
FACT_CHARS = 40
MUTATIONS_MAX = 3
COVERT_HP_COST = 20  # 暗取的代价至多伤到这里（留一口气）：翻墙越梁、被貂咬一口，不是交手
HP_UNIT = 10  # 等价交换：十点气血折一格代价
RENOWN_UNIT = 5  # 五点名望折一格
RENOWN_SWING = {"爆炸": (-10, 5), "暗流": (-3, 0)}  # 名望每回合的涨落上限：暗流只会悄悄折损，扬名须是硬结算
BYSTANDERS_MAX = 2  # 一招至多牵连两位旁人的人情（只降不升：讨好旁人不是这一招的副产品）
COLLAPSE_HURT = 30  # 危机坍缩的创伤
COLLAPSE_RENOWN = 5


class Severity(StrEnum):
    BLAST = "爆炸"  # 不可逆：当场硬结算
    UNDERCURRENT = "暗流"  # 累加：只许软结局，挂上或推进一只时钟


class ActionTrigger(StrEnum):
    NONE = "无"
    CONFRONT = "交手"  # 对象就此与你剑拔弩张（敌视）
    FLEE = "脱身"  # 你被迫夺路而逃（沿 rules.retreat 的退路）
    DEATH = "死亡判定"  # 只在出手且可裁区间含毙命时成立


class ClockOp(StrEnum):
    START = "新建"
    ADVANCE = "推进"
    REWIND = "回退"
    CLEAR = "销毁"


class DeltaKind(StrEnum):
    HP = "气血"  # 玩家气血的相对变化（只降：疗伤走 REST / USE）
    RENOWN = "名望"  # 玩家名望
    REGARD = "人情"  # 人情:<在场者>，以档计；对象的人情决定结局，旁人的只许降一档作代价
    SUBDUE = "制住"  # 制住:<对手> = 1：出手得手
    AIM = "所图"  # 所图 = 1：这一招图的那件事成了（夺物 / 讨要 / 打探 / 结交……）


_KEY = re.compile(r"^(人情|制住)\s*[:：]\s*(.{1,24})$")


def parse_delta_key(key: str) -> tuple[DeltaKind, str | None]:
    """「气血」「名望」「所图」不带对象；「人情:<名>」「制住:<名>」带对象（名字或 id）。其余一律不合文法。"""
    key = key.strip()
    if key in {DeltaKind.HP, DeltaKind.RENOWN, DeltaKind.AIM}:
        return DeltaKind(key), None
    if m := _KEY.match(key):
        return DeltaKind(m.group(1)), m.group(2).strip()
    raise ValueError(f"属性键「{key}」不合文法：只认 气血 / 名望 / 所图 / 人情:<名> / 制住:<名>")


# ============================================================
#  推演契约 —— 大模型交出的结构化提议（推理层在前、符号层在后：字段顺序即思维顺序）
# ============================================================
class ClockMutation(BaseModel):
    """对一只时钟的一道指令。clock 是已有时钟的 id 或名称；新建时是新名称，并须给出种类、挂处、阈值与满则如何。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    op: ClockOp
    clock: str = Field(min_length=1, max_length=24)
    kind: ClockKind | None = None
    anchor: str | None = Field(default=None, max_length=24)
    maximum: Literal[4, 6, 8] | None = None
    steps: int = Field(default=1, ge=0, le=STEP_MAX)  # 新建：初始进度；推进 / 回退：格数
    consequence: str = Field(default="", max_length=CONSEQUENCE_CHARS)

    @model_validator(mode="after")
    def _complete(self) -> Self:
        if self.op is ClockOp.START:
            if self.kind is None or not self.anchor or self.maximum is None:
                raise ValueError("新建时钟须给出 kind / anchor / maximum")
            if len(self.clock) > NAME_CHARS:
                raise ValueError(f"时钟名称至多 {NAME_CHARS} 字")
        elif self.op in (ClockOp.ADVANCE, ClockOp.REWIND) and self.steps < 1:
            raise ValueError("推进与回退至少一格")
        return self


class ResolutionOutput(BaseModel):
    """
    地下城主的推演：先按「属性碰撞 → 量级 → 代价 → 时钟与收敛」写下推理，再交出符号化的结果。
    deltas 是图谱已有属性的相对变化（键见 DeltaKind；线上也可写成 [{key, value}] 列表——有些厂商的结构化输出不支持任意键的对象）。
    宽容而不放任：超出条数的时钟指令与事实截断、空白事实丢弃，属性键不合文法即整份不合契约（由调用方重采样）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    collision: str = Field(default="", max_length=240)  # 属性碰撞分析
    severity: Severity  # 量级评估
    cost: str = Field(default="", max_length=160)  # 代价计算
    convergence: str = Field(default="", max_length=160)  # 时钟操作与状态收敛
    deltas: dict[str, int] = Field(default_factory=dict)
    clock_mutations: tuple[ClockMutation, ...] = ()
    new_facts: tuple[str, ...] = ()
    action_trigger: ActionTrigger = ActionTrigger.NONE

    @field_validator("deltas", mode="before")
    @classmethod
    def _pairs(cls, value: Any) -> Any:
        if isinstance(value, list):  # [{key, value}, …] → {key: value}；同键累加
            folded: dict[str, int] = {}
            for pair in value:
                if not isinstance(pair, Mapping) or "key" not in pair:
                    raise ValueError("deltas 列表的每一项须是 {key, value}")
                key = str(pair["key"]).strip()
                folded[key] = folded.get(key, 0) + int(pair.get("value", 0))
            return folded
        return value

    @field_validator("deltas")
    @classmethod
    def _grammar(cls, value: dict[str, int]) -> dict[str, int]:
        for key, amount in value.items():
            parse_delta_key(key)
            if not -100 <= amount <= 100:
                raise ValueError(f"属性「{key}」的变化量 {amount} 超出 ±100")
        return {k.strip(): v for k, v in value.items() if v}

    @field_validator("clock_mutations", mode="before")
    @classmethod
    def _few_mutations(cls, value: Any) -> Any:
        return tuple(value)[:MUTATIONS_MAX] if isinstance(value, list | tuple) else value

    @field_validator("new_facts", mode="before")
    @classmethod
    def _few_facts(cls, value: Any) -> Any:
        if isinstance(value, list | tuple):
            return tuple(t.strip() for t in value if isinstance(t, str) and t.strip())[:FACTS_MAX]
        return value


# ============================================================
#  物理边界 —— rules 圈出的可裁区间，换算成推演看得懂、闸门守得住的样子
# ============================================================
_HARD: frozenset[Outcome] = frozenset({
    CombatOutcome.SUCCESS, CombatOutcome.SEVERE_WOUND, CombatOutcome.DEATH,
    SocialOutcome.GRANTED, SocialOutcome.FALLOUT,
    CovertOutcome.CLEAN, CovertOutcome.EXPOSED, CovertOutcome.CAUGHT,
})
_WINS: frozenset[Outcome] = frozenset({
    CombatOutcome.SUCCESS, SocialOutcome.GRANTED, CovertOutcome.CLEAN, CovertOutcome.EXPOSED,
})
_ORDER: dict[Route, tuple[Outcome, ...]] = {
    Route.COMBAT: tuple(CombatOutcome), Route.SOCIAL: tuple(SocialOutcome), Route.COVERT: tuple(CovertOutcome),
}


def hard(outcome: Outcome) -> bool:
    """硬结局：不可逆，当场结算。其余（相持、轻伤、松动、无果、碰壁、未遂）是软结局，可以只是暗流里的一步。"""
    return outcome in _HARD


@dataclass(frozen=True, slots=True)
class Envelope:
    """
    一招的物理边界。admissible 由对玩家最有利到最不利排列；FIXED（结果已定之事）没有可裁结局，推演只能动时钟、事实与名望。
    strain 是越出舒适区几格（0 即舒适区内）：越级、交情不够、技不如人——它决定得手要付多少代价。
    """

    route: Route
    target_id: str | None
    target_name: str
    admissible: tuple[Outcome, ...] = ()
    canonical: Outcome | None = None
    strain: int = 0
    regard: Attitude = Attitude.NEUTRAL  # 对象此刻对你的态度
    player_hp: int = 100

    @property
    def contested(self) -> bool:
        return len(self.admissible) > 1

    @property
    def can_subdue(self) -> bool:
        return CombatOutcome.SUCCESS in self.admissible

    @property
    def can_achieve(self) -> bool:
        return any(o in self.admissible for o in (SocialOutcome.GRANTED, CovertOutcome.CLEAN, CovertOutcome.EXPOSED))

    @property
    def lethal(self) -> bool:
        return CombatOutcome.DEATH in self.admissible


def envelope_of(
    stakes: AnyStakes | None, state: PlayerState, snap: LocalSnapshot, target_id: str | None = None
) -> Envelope:
    """把三路赌注之一（或无赌注）换算成物理边界。舒适区：出手看境界差，交涉看分数（≥1 从容），暗取看差额（≥1 从容）。"""
    target = stakes.target_id if stakes is not None else target_id
    name = snap.label(target) if target else ""
    regard = state.attitude_of(target) if target else Attitude.NEUTRAL
    if stakes is None:
        return Envelope(route=Route.FIXED, target_id=target, target_name=name, regard=regard, player_hp=state.hp)
    if isinstance(stakes, Stakes):
        route, strain = Route.COMBAT, max(0, stakes.gap)
    elif isinstance(stakes, SocialStakes):
        route, strain = Route.SOCIAL, min(3, max(0, 1 - stakes.score))
    else:
        route, strain = Route.COVERT, min(3, max(0, 1 - stakes.margin))
    return Envelope(
        route=route, target_id=target, target_name=name, admissible=tuple(stakes.admissible),
        canonical=stakes.canonical, strain=strain, regard=regard, player_hp=state.hp,
    )


def delta_keys(env: Envelope, snap: LocalSnapshot) -> tuple[str, ...]:
    """这一招推演可用的属性键（给简报与 schema 的枚举）：名字一律写本名。"""
    keys = [DeltaKind.RENOWN.value]
    if env.route is Route.FIXED:
        return tuple(keys)
    if env.route in (Route.COMBAT, Route.COVERT):
        keys.insert(0, DeltaKind.HP.value)
    if env.can_achieve:
        keys.append(DeltaKind.AIM.value)
    if env.can_subdue and env.target_name:
        keys.append(f"{DeltaKind.SUBDUE}:{env.target_name}")
    keys += [f"{DeltaKind.REGARD}:{c.name}" for c in snap.characters if not c.subdued or c.id == env.target_id]
    return tuple(keys)


def clock_anchors(snap: LocalSnapshot) -> tuple[str, ...]:
    """时钟可挂之处：此地、在场之人、可见之物、你自己（写名字）。"""
    return (snap.location.name, *(c.name for c in snap.characters), *(i.name for i in snap.items), SELF)


# ============================================================
#  定案
# ============================================================
@dataclass(frozen=True, slots=True)
class Settlement:
    """
    推演过闸之后的定案。outcome / hp_change 交给 stakes.settle_any 落成路线事件（FIXED 为 None）；
    events 是路线事件之外的附带（暗取的代价、名望、旁人人情、时钟、坍缩的后果、微观事实）；
    flee 要求调用方补一次夺路而逃（rules.retreat 才知道退路）；notes 记下闸门钳了什么、补了什么、驳了什么，只供日志与测试。
    """

    outcome: Outcome | None
    hp_change: int
    severity: Severity
    trigger: ActionTrigger
    adopted: bool
    events: tuple[DomainEvent, ...] = ()
    flee: bool = False
    notes: tuple[str, ...] = ()

    @property
    def proposal(self) -> Proposal | None:
        return Proposal(outcome=self.outcome, hp_change=self.hp_change) if self.outcome is not None else None


def _mid(outcome: CombatOutcome) -> int:
    low, high = HP_BANDS[outcome]
    return (low + high) // 2


def _own_band(outcome: CombatOutcome) -> tuple[int, int]:
    """这一格独占的气血带：与更轻一格共用的端点（轻伤的 −10 也是相持的下沿，_derive 平局取轻者）让给那一格，钳进来的数才推得回自身。"""
    low, high = HP_BANDS[outcome]
    graded = (CombatOutcome.STALEMATE, CombatOutcome.MINOR_WOUND, CombatOutcome.SEVERE_WOUND)  # 只凭气血推结局的三格
    if outcome in graded and any(HP_BANDS[o][0] <= high <= HP_BANDS[o][1] for o in graded[: graded.index(outcome)]):
        high -= 1
    return low, high


def output_for(env: Envelope, outcome: Outcome | None = None, hp_change: int | None = None) -> ResolutionOutput:
    """
    规则或气运给出的结局 → 同形的推演输出（属性变化与之一一对应），好与大模型的推演走同一道闸门。
    出手的扣减先钳进那一格结局独占的气血带（_own_band：与轻一格共用的端点让出去），免得推回来落到别的结局上。
    """
    inside = outcome is not None and outcome in env.admissible
    o = outcome if inside else env.canonical  # 出界、别的路线的结局：取确定性裁决
    if not inside:  # 出界的提议连扣减一并作废（取确定性裁决的气血带中值），与旧 settle_any 的口径一致
        hp_change = None
    name, deltas, trigger = env.target_name, dict[str, int](), ActionTrigger.NONE
    if isinstance(o, CombatOutcome):
        low, high = _own_band(o)
        hp = min(high, max(low, -abs(hp_change))) if hp_change is not None else _mid(o)
        deltas[DeltaKind.HP.value] = hp
        if o is CombatOutcome.SUCCESS:
            deltas[f"{DeltaKind.SUBDUE}:{name}"] = 1
        elif o is CombatOutcome.DEATH:
            trigger = ActionTrigger.DEATH
    elif isinstance(o, SocialOutcome):
        shift = {SocialOutcome.SOFTENED: 1, SocialOutcome.REBUFFED: -1, SocialOutcome.FALLOUT: -2}.get(o, 0)
        if shift:
            deltas[f"{DeltaKind.REGARD}:{name}"] = shift
        if o is SocialOutcome.GRANTED:
            deltas[DeltaKind.AIM.value] = 1
    elif isinstance(o, CovertOutcome):
        if o in (CovertOutcome.CLEAN, CovertOutcome.EXPOSED):
            deltas[DeltaKind.AIM.value] = 1
        if o in (CovertOutcome.EXPOSED, CovertOutcome.CAUGHT):
            deltas[f"{DeltaKind.REGARD}:{name}"] = -1
    return ResolutionOutput(severity=Severity.BLAST, deltas=deltas, action_trigger=trigger)


@dataclass(slots=True)
class _Typed:
    hp: int = 0
    renown: int = 0
    regard: dict[str, int] | None = None
    subdued: frozenset[str] = frozenset()
    aim: bool = False


def _names(state: PlayerState, snap: LocalSnapshot) -> dict[str, str]:
    """名字 → 场景 id（精确匹配；同名多指即作废，不猜）。id 本身也认。"""
    table: dict[str, set[str]] = {}

    def put(name: str, any_id: str) -> None:
        if name:
            table.setdefault(name, set()).add(any_id)

    for name in (SELF, state.name, snap.player_name):
        put(name, state.player_id)
    put(snap.location.name, snap.location.id)
    for c in snap.characters:
        for name in c.names:
            put(name, c.id)
    for i in snap.items:
        for name in i.names:
            put(name, i.id)
    resolved = {name: next(iter(ids)) for name, ids in table.items() if len(ids) == 1}
    resolved |= {any_id: any_id for ids in table.values() for any_id in ids}
    return resolved


def _typed(output: ResolutionOutput, names: Mapping[str, str], notes: list[str]) -> _Typed:
    typed = _Typed(regard={})
    subdued: set[str] = set()
    for key, amount in output.deltas.items():
        kind, who = parse_delta_key(key)
        target = names.get(who) if who is not None else None
        if who is not None and target is None:
            notes.append(f"「{who}」不在眼前，「{key}」作罢")
            continue
        match kind:
            case DeltaKind.HP:
                typed.hp += amount
            case DeltaKind.RENOWN:
                typed.renown += amount
            case DeltaKind.REGARD if target is not None:
                assert typed.regard is not None
                typed.regard[target] = typed.regard.get(target, 0) + amount
            case DeltaKind.SUBDUE if target is not None and amount > 0:
                subdued.add(target)
            case DeltaKind.AIM:
                typed.aim = amount > 0
    typed.subdued = frozenset(subdued)
    return typed


def _derive(env: Envelope, typed: _Typed, trigger: ActionTrigger) -> Outcome | None:
    """结局由属性变化推出：大模型不选结局，它描述发生了什么，领域据此归类。"""
    regard = (typed.regard or {}).get(env.target_id or "", 0)
    match env.route:
        case Route.COMBAT:
            if trigger is ActionTrigger.DEATH:
                return CombatOutcome.DEATH
            if env.target_id in typed.subdued:
                return CombatOutcome.SUCCESS
            hp = min(typed.hp, 0)
            bands = [o for o in (CombatOutcome.STALEMATE, CombatOutcome.MINOR_WOUND, CombatOutcome.SEVERE_WOUND)]
            return min(bands, key=lambda o: (max(HP_BANDS[o][0] - hp, hp - HP_BANDS[o][1], 0), bands.index(o)))
        case Route.SOCIAL:
            if typed.aim:
                return SocialOutcome.GRANTED
            if regard <= -2 or trigger is ActionTrigger.CONFRONT:
                return SocialOutcome.FALLOUT
            if regard < 0:
                return SocialOutcome.REBUFFED
            return SocialOutcome.SOFTENED if regard > 0 else SocialOutcome.NOTHING
        case Route.COVERT:
            noticed = regard < 0 or trigger is ActionTrigger.CONFRONT
            if typed.aim:
                return CovertOutcome.EXPOSED if noticed else CovertOutcome.CLEAN
            return CovertOutcome.CAUGHT if noticed else CovertOutcome.FOILED
    return None


def _nearest_soft(env: Envelope, wanted: Outcome) -> Outcome | None:
    order = _ORDER[env.route]
    soft = [o for o in env.admissible if not hard(o)]
    if not soft:
        return None
    return min(soft, key=lambda o: (abs(order.index(o) - order.index(wanted)), order.index(o)))


_STATE_WORDS = re.compile(
    r"死了|身死|毙命|气绝|断气|昏死|杀了|杀死|夺下|夺得|夺走|抢走|偷走|到手|交给|送给|学会|传授|传给|离开了|来到|逃到|擒住|制住"
)
_JUNK = re.compile(r"[A-Za-z0-9０-９<>〈〉`{}\[\]\n\r\t]")  # 〈〉是系统提示格式示例的占位：照抄进来的一律不收


def _fact(text: str, notes: list[str]) -> str | None:
    """微观事实只写细节：一行中文、四十字内、不夹带改变属性 / 归属 / 生死 / 位置的字眼（那些只能由属性键与结局写进账）。"""
    if len(text) > FACT_CHARS or _JUNK.search(text):
        notes.append(f"事实「{text[:20]}」不合格式，作罢")
        return None
    if _STATE_WORDS.search(text):
        notes.append(f"事实「{text[:20]}」夹带了状态变化，作罢")
        return None
    return text


def _subjects(text: str, snap: LocalSnapshot) -> tuple[str, ...]:
    """事实点了名的场景实体（两字以上的名字才算，免得单字误中）。"""
    found = {snap.location.id} if snap.location.name and snap.location.name in text else set()
    for c in snap.characters:
        if any(len(n) >= 2 and n in text for n in c.names):
            found.add(c.id)
    for i in snap.items:
        if any(len(n) >= 2 and n in text for n in i.names):
            found.add(i.id)
    return tuple(sorted(found))


def fact_id(text: str) -> str:
    return "emg:" + hashlib.sha1(text.encode()).hexdigest()[:10]


class _Clocks:
    """一回合里的时钟账：在快照里的那几只 + 本回合新挂的；每只至多被动一次。"""

    def __init__(
        self, state: PlayerState, snap: LocalSnapshot, names: Mapping[str, str], subdued: frozenset[str] = frozenset()
    ) -> None:
        self.state, self.names = state, names
        self.subdued = subdued  # 这一回合定案之后被制住的人（含这一招得手制住的对手）
        self.scene = {c.id: c for c in snap.clocks}
        self.anchors = {snap.location.id, state.player_id, *(c.id for c in snap.characters), *(i.id for i in snap.items)}
        self.active = len(state.clocks)
        self.touched: set[str] = set()
        self.events: list[DomainEvent] = []
        self.collapsed: list[NarrativeClock] = []
        self.threat_net = 0  # 凶险时钟本回合净添的格数（回退、销毁扣回）：先挂后退、先挂后销都付不了账
        self.moved = False  # 本回合挂上或推进过至少一只

    @property
    def threat_ticks(self) -> int:
        return max(0, self.threat_net)

    def pays(self, kind: ClockKind, anchor_id: str) -> bool:
        """
        这只时钟的格数算不算代价：只有凶险、且满了还会有后果的才算。敌意满了是挂处之人出手伤你——
        他若已被你制住（含这一招刚制住），满了也无从出手，挂多少格都是空头账。疑心满了至少折名望，危机满了你受创，都算。
        """
        return kind.threat and not (kind is ClockKind.ENMITY and anchor_id in self.subdued)

    def _again(self, clock: NarrativeClock, cause: str, notes: list[str]) -> bool:
        """一回合每只至多动一次（新建、推进、回退、销毁都算）；唯一的例外是领域补代价时推进同名的那只。"""
        if clock.id in self.touched and not cause.startswith("代价"):
            notes.append(f"时钟「{clock.name}」本回合已动过")
            return True
        return False

    def find(self, ref: str) -> NarrativeClock | None:
        return self.scene.get(ref) or next((c for c in self.scene.values() if c.name == ref), None)

    def start(self, name: str, kind: ClockKind, anchor_id: str, maximum: int, steps: int, consequence: str,
              cause: str, notes: list[str]) -> None:
        cid = clock_id(anchor_id, name)
        if cid in self.scene:
            self.advance(self.scene[cid], max(1, steps), cause, notes)
            return
        if self.active >= CLOCKS_MAX or sum(c.anchor_id == anchor_id for c in self.scene.values()) >= PER_ANCHOR:
            notes.append(f"时钟「{name}」挂不上：悬着的时钟已满")
            return
        clock = NarrativeClock(id=cid, name=name, kind=kind, anchor_id=anchor_id,
                               progress=min(steps, maximum - 1), maximum=maximum, consequence=consequence)
        self.scene[cid], self.active = clock, self.active + 1
        self.touched.add(cid)
        self.events.append(ClockStarted(clock=clock, cause=cause))
        self.moved = True
        if self.pays(kind, anchor_id):
            self.threat_net += clock.progress

    def advance(self, clock: NarrativeClock, steps: int, cause: str, notes: list[str]) -> None:
        if self._again(clock, cause, notes):
            return
        self.touched.add(clock.id)
        if clock.progress + steps >= clock.maximum:
            self.events.append(ClockCollapsed(clock_id=clock.id, name=clock.name, consequence=clock.consequence))
            self.collapsed.append(clock)
            self.scene.pop(clock.id, None)
            self.active -= 1
        else:
            steps = max(-clock.progress, steps)  # 回退至多退到零
            if steps:
                moved = clock.model_copy(update={"progress": clock.progress + steps})
                self.events.append(ClockAdvanced(clock_id=clock.id, steps=steps, name=clock.name, progress=moved.progress,
                                                 maximum=clock.maximum, cause=cause))
                self.scene[clock.id] = moved
        if steps > 0:
            self.moved = True
        if self.pays(clock.kind, clock.anchor_id):
            self.threat_net += min(steps, clock.remaining)  # 坍缩那一下只算到满格为止

    def clear(self, clock: NarrativeClock, cause: str, notes: list[str]) -> None:
        if self._again(clock, cause, notes):
            return
        self.touched.add(clock.id)
        if self.pays(clock.kind, clock.anchor_id):
            self.threat_net -= clock.progress
        self.events.append(ClockCleared(clock_id=clock.id, name=clock.name, cause=cause))
        self.scene.pop(clock.id, None)
        self.active -= 1

    def apply(self, m: ClockMutation, notes: list[str]) -> None:
        if m.op is ClockOp.START:
            if "〈" in m.clock + m.consequence:
                notes.append(f"时钟「{m.clock}」照抄了格式示例的占位，作罢")
                return
            anchor = self.names.get(m.anchor or "")
            if anchor not in self.anchors or anchor is None:
                notes.append(f"时钟「{m.clock}」的挂处「{m.anchor}」不在眼前")
                return
            assert m.kind is not None and m.maximum is not None
            self.start(m.clock, m.kind, anchor, m.maximum, max(m.steps, 1), m.consequence, "推演", notes)
            return
        clock = self.find(m.clock)
        if clock is None:
            notes.append(f"眼前没有「{m.clock}」这只时钟")
            return
        if m.op is ClockOp.CLEAR:
            self.clear(clock, "化解", notes)
        else:
            self.advance(clock, m.steps if m.op is ClockOp.ADVANCE else -m.steps, "推演", notes)


def _default_clock(env: Envelope, regard_shift: int, *, cost: bool) -> tuple[str, ClockKind, int, str]:
    """领域补挂的时钟（名称、种类、阈值、满则如何）：暗流没挂钟、代价没付够时用。"""
    who = (env.target_name or "此地")[: NAME_CHARS - 4]  # 最长的模板「与某某的交情」多四个字：名字再长也挂得上
    if env.route is Route.COMBAT:
        return (f"{who}的旧恨" if cost else f"{who}的杀意"), ClockKind.ENMITY, 4, "怒而动手"
    if env.route is Route.SOCIAL and regard_shift >= 0 and not cost:
        return f"与{who}的交情", ClockKind.PROGRESS, 6, "交情更进一步"
    if env.route is Route.COVERT:
        return f"{who}的疑心", ClockKind.SUSPICION, 4, "识破你的手脚"
    return f"{who}的戒心", ClockKind.SUSPICION, 4, "看穿你的用心"


def _collapse(
    clock: NarrativeClock, state: PlayerState, snap: LocalSnapshot, hp: int, subdued: frozenset[str]
) -> tuple[list[DomainEvent], bool, bool]:
    """
    坍缩表：满格的暗流按种类落为硬结算。hp 是这一回合此前已结算的气血（伤人都在它上面留一口气），
    subdued 是定案之后被制住的人。返回（事件, 是否被迫脱身, 是否剑拔弩张）。
      疑心：挂处之人敌视你、名望 −5；敌意：挂处之人敌视你并出手，你受创三十——已被制住的人无从出手，只剩敌视；
      危机：你受创三十，挂在此地或你身上即被迫脱身；进展：挂处之人人情升一档（至多友善，已够则名望 +3）。
    """
    cause = f"{clock.name}满了" + (f"：{clock.consequence}" if clock.consequence else "")
    who = snap.character(clock.anchor_id)
    regard = state.attitude_of(clock.anchor_id)
    hurt = min(COLLAPSE_HURT, max(0, hp - 1))
    match clock.kind:
        case ClockKind.SUSPICION | ClockKind.ENMITY if who is not None:
            events: list[DomainEvent] = []
            if regard is not Attitude.HOSTILE:
                events.append(RelationChanged(character_id=who.id, attitude=Attitude.HOSTILE, cause=cause, basis=clock.kind.value))
            if clock.kind is ClockKind.SUSPICION:
                events.append(RenownChanged(delta=-COLLAPSE_RENOWN, cause=cause))
            elif who.id not in subdued and hurt:
                events.append(HealthChanged(delta=-hurt, cause=cause, source_id=who.id, source="blow"))
            return events, False, clock.kind is ClockKind.ENMITY
        case ClockKind.PERIL:
            hit: list[DomainEvent] = [HealthChanged(delta=-hurt, cause=cause, source_id=None)] if hurt else []
            return hit, clock.anchor_id in (snap.location.id, state.player_id), False
        case ClockKind.PROGRESS if who is not None and regard.rank < CAP.rank:
            return [RelationChanged(character_id=who.id, attitude=regard.step(1), cause=cause, basis=clock.kind.value)], False, False
        case ClockKind.PROGRESS:
            return [RenownChanged(delta=3, cause=cause)], False, False
    return [RenownChanged(delta=-COLLAPSE_RENOWN, cause=cause)], False, False


def settle(env: Envelope, output: ResolutionOutput, state: PlayerState, snap: LocalSnapshot) -> Settlement:
    """
    推演 → 定案。顺序即物理：属性落地 → 推出结局 → 量级 → 可裁区间 → 气血与名望钳位 → 旁人人情 → 时钟 →
    暗流补挂 → 等价交换补足代价 → 坍缩 → 路由 → 微观事实。整份推演出界即作废，只按确定性裁决结算（不挂它的时钟、不收它的事实）。
    """
    notes: list[str] = []
    names = _names(state, snap)
    typed = _typed(output, names, notes)
    trigger = output.action_trigger
    if trigger is ActionTrigger.DEATH and not env.lethal:
        notes.append("死亡判定不在可裁区间里，作罢")
        trigger = ActionTrigger.NONE
    severity = output.severity
    outcome = _derive(env, typed, trigger)

    if outcome is not None and severity is Severity.UNDERCURRENT and hard(outcome):
        soft = _nearest_soft(env, outcome)
        if soft is None:
            severity = Severity.BLAST
            notes.append("区间里没有软结局，暗流改作爆炸")
        else:
            notes.append(f"暗流不许硬结局：{outcome.value} → {soft.value}")
            outcome = soft
    adopted = outcome is None or outcome in env.admissible
    if not adopted:
        notes.append(f"推出的结局「{outcome.value if outcome else ''}」不在可裁区间里，整份推演作废")
        return _canonical(env, state, snap, notes)

    hp = 0
    if isinstance(outcome, CombatOutcome):
        low, high = HP_BANDS[outcome]
        hp = min(high, max(low, min(typed.hp, 0) if typed.hp else _mid(outcome)))
    elif env.route is Route.COVERT:
        hp = max(-COVERT_HP_COST, min(0, typed.hp), 1 - state.hp)
    elif typed.hp:
        notes.append("交涉与无赌注之事不伤人，气血变化作罢")

    events: list[DomainEvent] = []
    if env.route is Route.COVERT and hp:
        events.append(HealthChanged(delta=hp, cause="暗中行事的代价", source_id=None))
    low, high = RENOWN_SWING[severity.value]
    if typed.renown > 0 and (env.route is Route.FIXED or outcome not in _WINS):
        high = 0
    renown_change = max(low, min(high, typed.renown))
    if renown_change != typed.renown:
        notes.append(f"名望 {typed.renown} 钳为 {renown_change}")

    bystanders = 0
    for who, shift in sorted((typed.regard or {}).items()) if env.route is not Route.FIXED else ():
        view = snap.character(who)
        if who == env.target_id or view is None or shift >= 0 or bystanders >= BYSTANDERS_MAX:
            if shift > 0 and who != env.target_id:
                notes.append("旁人的人情只降不升")
            continue
        regard = state.attitude_of(who)
        if regard is not Attitude.HOSTILE:
            events.append(RelationChanged(character_id=who, attitude=regard.step(-1), cause="看不惯你的作为", basis="代价"))
            bystanders += 1

    subdued = state.subdued | ({env.target_id} if outcome is CombatOutcome.SUCCESS and env.target_id else set())
    clocks = _Clocks(state, snap, names, frozenset(subdued))
    for m in output.clock_mutations:
        clocks.apply(m, notes)
    target_shift = (typed.regard or {}).get(env.target_id or "", 0)
    if severity is Severity.UNDERCURRENT and not clocks.moved and env.route is not Route.FIXED and env.target_id:
        name, kind, size, then = _default_clock(env, target_shift, cost=False)
        anchor = env.target_id if snap.character(env.target_id) else snap.location.id
        clocks.start(name, kind, anchor, size, 1, then, "暗流", notes)
        notes.append(f"暗流须挂时钟：补挂「{name}」")

    shortfall = _shortfall(env, outcome, hp, renown_change, bystanders, clocks.threat_ticks)
    if shortfall > 0 and env.target_id:
        name, kind, size, then = _default_clock(env, target_shift, cost=True)
        anchor = env.target_id if snap.character(env.target_id) else snap.location.id
        before = len(clocks.events)
        if clocks.pays(kind, anchor):  # 挂上了也没有后果的（对象已被制住）不挂，直接折名望
            clocks.start(name, kind, anchor, size, shortfall, then, "代价", notes)
        if len(clocks.events) == before:  # 挂不上（时钟已满，或挂了也是空头账）：折成名望
            renown_change = max(-20, renown_change - shortfall * RENOWN_UNIT)
        notes.append(f"等价交换：代价欠 {shortfall} 格，由领域补足")

    if renown_change:
        events.append(RenownChanged(delta=renown_change, cause="江湖传言"))
    events += clocks.events

    flee = trigger is ActionTrigger.FLEE
    confront = trigger is ActionTrigger.CONFRONT or outcome is SocialOutcome.FALLOUT
    left = max(0, state.hp + hp)  # 这一回合路线已结算的气血：坍缩伤人在它上面留一口气
    for clock in clocks.collapsed:
        fallout, forced, hostile = _collapse(clock, state, snap, left, clocks.subdued)
        left += sum(e.delta for e in fallout if isinstance(e, HealthChanged))
        events += fallout
        flee, confront = flee or forced, confront or hostile
        severity = Severity.BLAST
    if outcome is not None and hard(outcome):
        severity = Severity.BLAST

    for text in output.new_facts:
        if (kept := _fact(text, notes)) is not None:
            events.append(FactEmerged(fact_id=fact_id(kept), text=kept, subject_ids=_subjects(kept, snap)))

    final = (
        ActionTrigger.DEATH if outcome is CombatOutcome.DEATH
        else ActionTrigger.FLEE if flee
        else ActionTrigger.CONFRONT if confront
        else ActionTrigger.NONE
    )
    return Settlement(outcome=outcome, hp_change=hp, severity=severity, trigger=final, adopted=True,
                      events=tuple(events), flee=flee, notes=tuple(notes))


def _shortfall(env: Envelope, outcome: Outcome | None, hp: int, renown: int, bystanders: int, threat_ticks: int) -> int:
    """
    等价交换：好过确定性裁决几格就欠几格；越出舒适区还要得手，再欠 strain 格。
    付的是：暗取的气血（十点一格）、名望（五点一格）、旁人的人情（一档一格）、凶险时钟新添的格数。出手的气血在结局的气血带里，不算代价。
    """
    if outcome is None or env.canonical is None:
        return 0
    order = _ORDER[env.route]
    owed = max(0, order.index(env.canonical) - order.index(outcome)) + (env.strain if outcome in _WINS else 0)
    paid = bystanders + threat_ticks + math.ceil(max(0, -renown) / RENOWN_UNIT)
    if env.route is Route.COVERT:
        paid += math.ceil(max(0, -hp) / HP_UNIT)
    return max(0, owed - paid)


def _canonical(env: Envelope, state: PlayerState, snap: LocalSnapshot, notes: Iterable[str]) -> Settlement:
    """出界的推演作废：按确定性裁决结算（它好不过自己，不欠代价），推演里的时钟、事实、名望一概不收。"""
    o = env.canonical
    hp = _mid(o) if isinstance(o, CombatOutcome) else 0
    return Settlement(
        outcome=o, hp_change=hp, severity=Severity.BLAST if o is not None and hard(o) else Severity.UNDERCURRENT,
        trigger=ActionTrigger.DEATH if o is CombatOutcome.DEATH else ActionTrigger.NONE, adopted=False, notes=tuple(notes),
    )
