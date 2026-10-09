"""
[INPUT]: 依赖 domain/ambient 的 Activity / ActivityKind / EnvironmentalTrace / FactToken 与 id、TOKEN_CHARS，
         依赖 domain/commands 的 SPAWN_TICK / TICKS_PER_DAY / day_of，依赖 domain/events 的事件词汇，
         依赖 domain/models 的 WorldBlueprint / Item / Character / CharacterStatus / Disposition / Ownership / ownership，
         依赖 domain/combat 的 CombatOutcome，依赖 domain/outcomes 的 SocialOutcome / CovertOutcome，依赖 domain/snapshot 的 LocalSnapshot；
         PlayerState 仅作类型标注
[OUTPUT]: 对外提供 Atlas（静态地理：道路邻接、室内之地、正典物品、常驻之人）与 Atlas.of(bp) / ring()（从一地广度优先的次序表）、
          Mark 痕迹样式与 BLOOD / SCUFFLE / LITTER、ROUT_TICKS / PILFER_ODDS、
          intensity()（一批事件的烈度 0~10）、deed()（一批事件里那件公开之事的消息正文与主体）、
          aftermath()（这一招在此地留下的余波：交手的活动与痕迹、人群溃散、公开之事成为消息）、
          spread()（消息每刻沿路传开 speed 处）、ecology()（跨过的每个黎明：露天无主之物按物料风化、遗落之物被此地的人顺手拿走）
[POS]: domain 的世界物理（纯函数，不做 IO、不调大模型、不看时钟——时间是参数 tick）：图谱属性与规则驱动世界，叙事只翻译感知。
       由 application/world_clock 在每条命令定案之后调用，算出的事件与定案事件一并入账：
         余波——交手在此地留下一个一刻即止的「交手」活动与一道痕迹（见了血是血迹，否则是打斗的狼藉）；
               一举的烈度高过在场人群的惊惧阈值，人群即溃散逃离（一个 ROUT_TICKS 刻的活动）并留下一地狼藉；
               公开之事（交手、暗取败露、当场翻脸、物归原主）成为一枚消息，有人群目睹则每刻传两处，烈度越高传得越远；
               暗中得手、无人察觉之事不成消息——没人知道的事，谁也不该知道；
         扩散——消息沿 CONNECTS_TO 广度优先，每刻至多 speed 处、至多 radius 跳：传到哪里，那里的人才知道（局部认知）；
         生态——每跨过一个黎明结算一次：露天、无主、经不起风雨的东西按物料的日数朽坏；无主或遗落在地、可携而无险的东西，
               有一定的机会被此地不仁厚、也不是物主的常驻之人顺手拿走（物主在侧则无人敢拿）；机会按（玩家, 日, 物）哈希，确定
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.domain.ambient import (
    TOKEN_CHARS,
    Activity,
    ActivityKind,
    EnvironmentalTrace,
    FactToken,
    activity_id,
    token_id,
    trace_id,
)
from app.domain.combat import CombatOutcome
from app.domain.commands import SPAWN_TICK, TICKS_PER_DAY, day_of
from app.domain.events import (
    ActivityStarted,
    DomainEvent,
    FactTokenSpawned,
    HealthChanged,
    ItemDecayed,
    ItemPilfered,
    ItemTransferred,
    Maneuvered,
    Parleyed,
    PlayerDied,
    RumorSpread,
    SkillExecuted,
    TraceLeft,
)
from app.domain.models import Character, CharacterStatus, Disposition, Item, Ownership, WorldBlueprint, ownership
from app.domain.outcomes import CovertOutcome, SocialOutcome
from app.domain.snapshot import LocalSnapshot

if TYPE_CHECKING:
    from app.domain.aggregates import PlayerState


# ============================================================
#  静态地理 —— 只读正典：道路、室内、物品、常驻之人
# ============================================================
@dataclass(frozen=True, slots=True)
class Atlas:
    neighbors: Mapping[str, tuple[str, ...]] = field(default_factory=dict)  # 道路双向，按 id 排序
    sheltered: frozenset[str] = frozenset()  # 有遮蔽的室内之地（Location.sheltered）
    items: tuple[Item, ...] = ()  # T=0 就在世上的正典物品，按 id 排序
    residents: Mapping[str, tuple[Character, ...]] = field(default_factory=dict)  # 地点 → T=0 身在其中的健在之人

    @classmethod
    def of(cls, bp: WorldBlueprint) -> Atlas:
        places = {loc.id for loc in bp.locations}
        roads: dict[str, set[str]] = {loc.id: set() for loc in bp.locations}
        for loc in bp.locations:
            for target in loc.exits.values():
                if target in places:  # 道路双向：出口只写了一头，另一头照样走得通
                    roads[loc.id].add(target)
                    roads[target].add(loc.id)
        residents: dict[str, list[Character]] = {}
        for c in sorted(bp.characters, key=lambda c: c.id):
            if c.location_id and c.status is CharacterStatus.ALIVE and c.arrives_with is None:
                residents.setdefault(c.location_id, []).append(c)
        return cls(
            neighbors={k: tuple(sorted(v)) for k, v in roads.items()},
            sheltered=frozenset(loc.id for loc in bp.locations if loc.sheltered),
            items=tuple(sorted((i for i in bp.items if i.arrives_with is None), key=lambda i: i.id)),
            residents={k: tuple(v) for k, v in residents.items()},
        )

    def ring(self, origin: str, radius: int) -> tuple[str, ...]:
        """从 origin 广度优先、radius 跳以内的次序表：按（跳数, id）排，origin 居首。消息就按这张表一处一处地传开。"""
        order, frontier, seen = [origin], [origin], {origin}
        for _ in range(radius):
            frontier = sorted({n for x in frontier for n in self.neighbors.get(x, ()) if n not in seen})
            seen |= set(frontier)
            order += frontier
        return tuple(order)


# ============================================================
#  余波 —— 这一招在此地留下了什么
# ============================================================
@dataclass(frozen=True, slots=True)
class Mark:
    description: str
    decay_ticks: int


BLOOD = Mark("地上点点血迹", TICKS_PER_DAY)  # 见了血：一日方散
SCUFFLE = Mark("地上脚印凌乱，有过打斗", 32)  # 没见血的交手：一个上午
LITTER = Mark("人群仓皇散去，一地狼藉", 48)
ROUT_TICKS = 48  # 溃散的人群半日之后回来
PILFER_ODDS = 64  # 每个黎明被顺手拿走的机会：哈希首字节 < 64，即四分之一

_STRIKE = {  # 交手的烈度：从玩家视角的结局（毙命是玩家死了）
    CombatOutcome.SUCCESS: 6, CombatOutcome.STALEMATE: 4, CombatOutcome.MINOR_WOUND: 5,
    CombatOutcome.SEVERE_WOUND: 7, CombatOutcome.DEATH: 9,
}
_BLOODY = frozenset({CombatOutcome.MINOR_WOUND, CombatOutcome.SEVERE_WOUND, CombatOutcome.DEATH})


def intensity(events: Iterable[DomainEvent]) -> int:
    """一批事件的烈度（0~10，取最烈的一件）：人群的惊惧阈值与消息传多远都比它。"""
    level = 0
    for event in events:
        match event:
            case SkillExecuted(outcome=outcome):
                level = max(level, _STRIKE[outcome])
            case PlayerDied():
                level = max(level, 9)
            case HealthChanged(source="blow", source_id=str(source)) if source.startswith("chr:"):
                level = max(level, 5)  # 有人出手伤你（含敌意时钟坍缩的那一击）
            case Maneuvered(outcome=CovertOutcome.EXPOSED | CovertOutcome.CAUGHT):
                level = max(level, 3)  # 捉贼的叫嚷
            case Parleyed(outcome=SocialOutcome.FALLOUT):
                level = max(level, 2)
    return level


_STRUCK = {
    CombatOutcome.SUCCESS: "{p}出手制住了{t}",
    CombatOutcome.STALEMATE: "{p}与{t}动手，不分胜负",
    CombatOutcome.MINOR_WOUND: "{p}与{t}动手，挂了彩",
    CombatOutcome.SEVERE_WOUND: "{p}被{t}打成重伤",
    CombatOutcome.DEATH: "{p}死在{t}手下",
}
_SEIZED = "{p}出手制住{t}，夺走了{i}"
_SNEAKED = {CovertOutcome.EXPOSED: "{p}暗取{t}的{i}，被当场识破", CovertOutcome.CAUGHT: "{p}想暗取{t}的{i}，失了手"}
_FALLOUT = "{p}与{t}言语不合，当场翻脸"
_RETURNED = "{p}把{i}还给了{t}"


def deed(events: Sequence[DomainEvent], before: LocalSnapshot) -> tuple[str, tuple[str, ...]] | None:
    """
    这一批事件里那件公开之事（按事件次序取第一件）→（消息正文, 主体）。封闭的五种：交手（夺物另写）、暗取被察觉、当场翻脸、物归原主；
    其余（闲谈、静观、修习、暗中得手无人察觉……）没人看见或不值一提，不成消息。
    """
    p, me = before.player_name, before.player_id
    taken = {e.item_id: e for e in events if isinstance(e, ItemTransferred) and e.to_holder == me}
    for event in events:
        match event:
            case SkillExecuted(target_id=target, outcome=outcome):
                seized = next((i for i, e in taken.items() if e.from_holder == target), None)
                if outcome is CombatOutcome.SUCCESS and seized is not None:
                    return _SEIZED.format(p=p, t=before.label(target), i=before.label(seized)), (me, target, seized)
                return _STRUCK[outcome].format(p=p, t=before.label(target)), (me, target)
            case Maneuvered(target_id=target, item_id=item, outcome=outcome) if outcome in _SNEAKED:
                return _SNEAKED[outcome].format(p=p, t=before.label(target), i=before.label(item)), (me, target, item)
            case Parleyed(npc_id=target, outcome=SocialOutcome.FALLOUT):
                return _FALLOUT.format(p=p, t=before.label(target)), (me, target)
            case ItemTransferred(item_id=item, from_holder=giver, to_holder=taker) if (
                giver == me and (seen := before.item(item)) is not None and seen.owner_id == taker
            ):
                return _RETURNED.format(p=p, t=before.label(taker), i=before.label(item)), (me, taker, item)
    return None


def _radius(level: int) -> int:
    return 4 if level >= 7 else 3 if level >= 4 else 2 if level >= 2 else 1


def _trace(location_id: str, mark: Mark, tick: int) -> EnvironmentalTrace:
    return EnvironmentalTrace(
        id=trace_id(location_id, mark.description, tick), location_id=location_id,
        description=mark.description, born_tick=tick, decay_ticks=mark.decay_ticks,
    )


def _activity(kind: ActivityKind, location_id: str, who: tuple[str, ...], tick: int, ends: int, trace: str) -> Activity:
    return Activity(
        id=activity_id(kind, location_id, who, tick), kind=kind, participants=who, location_id=location_id,
        started_tick=tick, ends_tick=ends, trace_id=trace,
    )


def aftermath(events: Sequence[DomainEvent], before: LocalSnapshot, tick: int) -> list[DomainEvent]:
    """
    这一招在出招之地（before 的所在）留下的余波，tick 是出招那一刻：交手 → 活动 + 痕迹；烈度高过人群的惊惧阈值 → 人群溃散 + 狼藉；
    公开之事 → 一枚只有此地知道的消息（有人群在场每刻传两处，否则一处；烈度越高传得越远）。
    """
    here = before.location.id
    out: list[DomainEvent] = []
    for event in events:
        if isinstance(event, SkillExecuted):
            trace = _trace(here, BLOOD if event.outcome in _BLOODY else SCUFFLE, tick)
            fight = _activity(ActivityKind.FIGHT, here, (before.player_id, event.target_id), tick, tick + 1, trace.id)
            out += [TraceLeft(trace=trace), ActivityStarted(activity=fight)]
    level = intensity(events)
    if panicked := [s for s in before.swarms if not s.routed and level > s.panic_threshold]:
        litter = _trace(here, LITTER, tick)
        out.append(TraceLeft(trace=litter))
        out += [
            ActivityStarted(activity=_activity(ActivityKind.ROUT, here, (s.id,), tick, tick + ROUT_TICKS, litter.id))
            for s in panicked
        ]
    if (found := deed(events, before)) is not None:
        text, subjects = found
        text = text[:TOKEN_CHARS]
        out.append(FactTokenSpawned(token=FactToken(
            id=token_id(text, here, tick), text=text, subject_ids=subjects, origin_id=here, born_tick=tick,
            speed=2 if before.swarms else 1, radius=_radius(level), reached=(here,),
        )))
    return out


# ============================================================
#  扩散 —— 消息一刻一两处地传开
# ============================================================
def spread(tokens: Iterable[FactToken], atlas: Atlas, ticks: int) -> list[RumorSpread]:
    """过了 ticks 刻：每枚消息沿广度优先的次序表再传 speed × ticks 处（radius 跳为止，传满即停）。"""
    out: list[RumorSpread] = []
    for token in sorted(tokens, key=lambda t: t.id):
        fresh = tuple(x for x in atlas.ring(token.origin_id, token.radius) if x not in token.reached)
        if fresh := fresh[: token.speed * ticks]:
            out.append(RumorSpread(token_id=token.id, location_ids=fresh))
    return out


# ============================================================
#  生态 —— 每个黎明一次：风化与顺手牵羊
# ============================================================
def _lucky(player_id: str, day: int, item_id: str) -> bool:
    return hashlib.sha256(f"{player_id}|{day}|{item_id}".encode()).digest()[0] < PILFER_ODDS


def ecology(state: PlayerState, atlas: Atlas, since: int) -> list[DomainEvent]:
    """
    since 之后、state.tick 之前（含）跨过的每个黎明各结算一次，先风化、后顺手牵羊，物品按 id 依次过：
    风化——持有者是露天之地、无主、物料经不起风雨，且自 T=0 搁到今日已满物料的日数；
    顺手牵羊——持有者是某地、无主或遗落、可携无险，物主不在那里，那里有不仁厚、未被制住的常驻之人，且（玩家, 日, 物）的哈希落进机会里：
    狠辣者先伸手，同类按 id。
    """
    out: list[DomainEvent] = []
    gone = set(state.consumed)
    holders = dict(state.item_holders)
    for day in range(day_of(since) + 1, day_of(state.tick) + 1):
        for item in atlas.items:
            holder = holders.get(item.id) or item.canon_holder
            if item.id in gone or holder is None or not holder.startswith("loc:"):
                continue
            owned = ownership(item.owner_id, holder)
            lifespan = item.material.weathers_in
            if (lifespan is not None and owned is Ownership.UNOWNED and holder not in atlas.sheltered
                    and day - day_of(SPAWN_TICK) >= lifespan):
                out.append(ItemDecayed(item_id=item.id, material=item.material))
                gone.add(item.id)
                continue
            if not item.portable or item.hazard or owned not in (Ownership.UNOWNED, Ownership.STRAYED):
                continue
            locals_ = atlas.residents.get(holder, ())
            if any(c.id == item.owner_id for c in locals_):
                continue
            takers = [c for c in locals_ if c.disposition is not Disposition.MERCIFUL and c.id not in state.subdued]
            if takers and _lucky(state.player_id, day, item.id):
                taker = min(takers, key=lambda c: (c.disposition is not Disposition.RUTHLESS, c.id))
                out.append(ItemPilfered(item_id=item.id, from_holder=holder, to_holder=taker.id))
                holders[item.id] = taker.id
    return out
