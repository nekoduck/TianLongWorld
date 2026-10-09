"""
[INPUT]: 依赖 domain/agenda 的 NpcAgenda / AgendaEnd / Encounter / EncounterKind / SkirmishOutcome / encounter_id / AGENDA_CHARS，
         依赖 domain/heartbeat 的 Atlas / position / residents_at / intensity / BLOOD / SCUFFLE 与痕迹、活动、消息的 id，
         依赖 domain/events 的事件词汇，依赖 domain/commands 的 TICKS_PER_DAY / day_of，依赖 domain/models 的 Character / Disposition / Tier，
         依赖 domain/ambient 的 Activity / ActivityKind / EnvironmentalTrace / FactToken / token_id / trace_id / activity_id，依赖 domain/resolution 的 fact_id；
         PlayerState 仅作类型标注
[OUTPUT]: 对外提供 分层 NPC 生态的物理（纯函数）——
          宏观层：planning_due()（初临江湖 / 新的一日 / 江湖震动 才规划）、upheaval()、destinations()（一位核心 NPC 可去之处：PLAN_HOPS 跳以内）、
                  free()（能领议程：在世、未被制住、未带伤、不在中断里）、AgendaProposal（大模型的议程提议，宽容）与 admit()（议程闸门 → AgendaPlanned + AgendaIssued / AgendaConcluded）；
          微观层：march()（把每位带议程的 NPC 沿最省时之路推进到此刻：一跳一条 NpcMoved，抵达即了结，相撞即 EncounterBegan 并停步）；
          裁决层：SkirmishStakes 与 skirmish_stakes()（狭路相逢的可裁区间：境界（带伤折一档）× 性情）、settle_skirmish()（结局 → 交手的往事与血迹、
                  落败者受伤败退、消息、可选的一条微观事实）、meet()（撞见的定案：闸门放行的时钟 / 事实 / 名望之后补一条 EncounterResolved）；
          常量 PLAN_HOPS / AGENDAS_MAX / PLAN_COOLDOWN / UPHEAVAL / ENCOUNTER_PAUSE / SKIRMISH_PAUSE / WOUND_TICKS / STAY
[POS]: domain 的 H-Agent 物理：大模型只在宏观层立议程（一日至多一轮、江湖震动另算，且须过这里的闸门），微观层的寻路与行军一个大模型也不调——
       Atlas.path 读道路耗时，NPC 一刻一刻默默挪步；只有相撞（与玩家同处一地、或与开篇仇人同处一地）才中断，交给裁决层：
       撞见由地下城主在结果已定的物理边界（FIXED：只许动时钟、事实、名望）里推演，狭路相逢由地下城主在这里圈出的可裁区间里挑结局，
       都失灵时取确定性裁决。战力洗牌是真的：落败者带伤一日（交手战力打一档折扣、不再赶路）、议程败退、消息沿路传开。
       与 heartbeat 同口径：时间是参数（state.tick），每一步都是可重放的事件
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, field_validator

from app.domain.agenda import AGENDA_CHARS, AgendaEnd, Encounter, EncounterKind, NpcAgenda, SkirmishOutcome, encounter_id
from app.domain.ambient import (
    Activity,
    ActivityKind,
    EnvironmentalTrace,
    FactToken,
    activity_id,
    token_id,
    trace_id,
)
from app.domain.commands import TICKS_PER_DAY, day_of
from app.domain.events import (
    ActivityStarted,
    AgendaConcluded,
    AgendaIssued,
    AgendaPlanned,
    DomainEvent,
    EncounterBegan,
    EncounterResolved,
    FactEmerged,
    FactTokenSpawned,
    NpcMoved,
    NpcWounded,
    TraceLeft,
)
from app.domain.heartbeat import BLOOD, Atlas, intensity, position
from app.domain.models import Character, Disposition
from app.domain.resolution import fact_id

if TYPE_CHECKING:
    from app.domain.aggregates import PlayerState

PLAN_HOPS = 3  # 一轮议程的去处：此刻所在 3 跳以内
AGENDAS_MAX = 4  # 一轮至多放行四条议程
PLAN_COOLDOWN = 8  # 江湖震动的再规划冷却：两个时辰
UPHEAVAL = 7  # 烈度到此即江湖震动（重伤、身死）
ENCOUNTER_PAUSE = 4  # 撞见之后驻足一个时辰
SKIRMISH_PAUSE = 8
WOUND_TICKS = TICKS_PER_DAY  # 落败者带伤一日
STAY = "留守"  # 议程提议里「不出门」的写法


# ============================================================
#  宏观层 —— 何时规划、谁能领议程、能去哪、提议怎么过闸
# ============================================================
def upheaval(events: Iterable[DomainEvent]) -> bool:
    """江湖震动：这一批里有烈度 ≥ UPHEAVAL 的事，或有一场狭路相逢动了手。"""
    batch = list(events)
    return intensity(batch) >= UPHEAVAL or any(
        isinstance(e, EncounterResolved) and e.outcome is not None and e.outcome.fight for e in batch
    )


def planning_due(state: PlayerState, atlas: Atlas, events: Iterable[DomainEvent] = ()) -> str | None:
    """该不该为核心 NPC 规划一轮议程，该则返回缘由：没有核心 NPC 永不规划；从没规划过、跨进新的一日、或冷却已过而江湖震动。"""
    if not atlas.core:
        return None
    if state.agenda_tick is None:
        return "初临江湖"
    if day_of(state.tick) > day_of(state.agenda_tick):
        return "新的一日"
    if state.tick - state.agenda_tick >= PLAN_COOLDOWN and upheaval(events):
        return "江湖震动"
    return None


def wounded(state: PlayerState, npc_id: str) -> bool:
    return state.npc_wounds.get(npc_id, -1) > state.tick


def free(state: PlayerState, atlas: Atlas, npc_id: str) -> bool:
    """能领议程、能上路：在世的核心 NPC，没被玩家制住、没带伤、不在一场待裁决的中断里。"""
    engaged = {x for e in state.encounters for x in (e.npc_id, e.other_id)}
    return (
        npc_id in atlas.characters and npc_id not in state.subdued and not wounded(state, npc_id) and npc_id not in engaged
    )


def destinations(state: PlayerState, atlas: Atlas, npc_id: str) -> tuple[str, ...]:
    """一位核心 NPC 这一轮可去之处：此刻所在 PLAN_HOPS 跳以内（不含此刻所在），按（跳数, id）。"""
    here = position(state, atlas, npc_id)
    return atlas.ring(here, PLAN_HOPS)[1:] if here else ()


_FLAW = re.compile(r"[A-Za-z0-9０-９<>〈〉`{}\[\]\n\r\t]")


class AgendaProposal(BaseModel):
    """大模型的一条议程提议：npc 与 target 写名字（或 id），target 写「留守」即不出门；字段宽容，闸门在 admit。"""

    model_config = ConfigDict(extra="ignore")

    npc: str
    target: str
    intent: str = ""
    priority: int = 2

    @field_validator("npc", "target", "intent", mode="before")
    @classmethod
    def _text(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("priority", mode="before")
    @classmethod
    def _priority(cls, value: Any) -> int:
        try:
            return max(1, min(3, int(value)))
        except (TypeError, ValueError):
            return 2


def _pick(name: str, pool: Iterable[str], names: Mapping[str, str]) -> str | None:
    """名字或 id 全等落地（不做包含匹配，多义不猜）。"""
    hits = [x for x in pool if name in (x, names.get(x))]
    return hits[0] if len(hits) == 1 else None


def admit(proposals: Sequence[AgendaProposal], state: PlayerState, atlas: Atlas, cause: str) -> list[DomainEvent]:
    """
    议程闸门：先记一条 AgendaPlanned（不论放行几条）；每位核心 NPC 至多一条、须 free、去处须在 destinations 里（全等落地）、
    战略意图须是 2~16 字的一行中文（无英文数字标记）；「留守」让已有的议程作罢；按轻重（priority 降序）再按 NPC id 至多放行 AGENDAS_MAX 条。
    """
    out: list[DomainEvent] = [AgendaPlanned(tick=state.tick, cause=cause)]
    seen: set[str] = set()
    issued: list[NpcAgenda] = []
    for p in sorted(proposals, key=lambda p: -p.priority):
        npc = _pick(p.npc, atlas.core, atlas.names)
        if npc is None or npc in seen or not free(state, atlas, npc):
            continue
        seen.add(npc)
        if p.target == STAY:
            if npc in state.agendas:
                out.append(AgendaConcluded(npc_id=npc, how=AgendaEnd.DROPPED, tick=state.tick))
            continue
        target = _pick(p.target, destinations(state, atlas, npc), atlas.names)
        if target is None or not (2 <= len(p.intent) <= AGENDA_CHARS) or _FLAW.search(p.intent):
            continue
        issued.append(NpcAgenda(npc_id=npc, target_id=target, intent=p.intent, priority=p.priority, issued_tick=state.tick))
    issued.sort(key=lambda a: (-a.priority, a.npc_id))
    out += [AgendaIssued(agenda=a) for a in issued[:AGENDAS_MAX]]
    return out


# ============================================================
#  微观层 —— 寻路行军，相撞即停
# ============================================================
def _rival(atlas: Atlas, npc_id: str, here: str, where: Mapping[str, str | None]) -> str | None:
    """此处有没有这位核心 NPC 的开篇仇人（也是核心 NPC）：有则返回 id 最小的一位。"""
    return next(
        (other for other in atlas.core
         if other != npc_id and where.get(other) == here and frozenset((npc_id, other)) in atlas.rivals),
        None,
    )


def march(state: PlayerState, atlas: Atlas, *, player_arrived: bool = False) -> list[DomainEvent]:
    """
    把每位带议程、能上路的 NPC（按 id）沿 Atlas.path 推进到 state.tick：下一跳在「上一刻 + 道路耗时」到达，赶得及就走、走一跳一条 NpcMoved；
    到了目标即 AgendaConcluded(抵达)；无路可通即受阻；被玩家制住即受阻；带伤或在中断里的原地不动。
    相撞即中断并停步：走进玩家所在之处是撞见，走到开篇仇人（核心 NPC）所在之处是狭路相逢。
    player_arrived：玩家这一回合挪了地方——他走进的地方若有带议程、离了家的 NPC，同样是撞见。
    """
    out: list[DomainEvent] = []
    where: dict[str, str | None] = {cid: position(state, atlas, cid) for cid in atlas.characters}
    engaged = {x for e in state.encounters for x in (e.npc_id, e.other_id)}
    me, here = state.player_id, state.location_id

    def interrupt(npc: str, kind: EncounterKind, other: str, at: str, tick: int) -> None:
        enc = Encounter(id=encounter_id(kind, npc, other, at, tick), kind=kind, npc_id=npc, other_id=other,
                        location_id=at, tick=tick)
        out.append(EncounterBegan(encounter=enc))
        engaged.update((npc, other))

    if player_arrived:
        for npc in sorted(state.agendas):
            away = (c := atlas.characters.get(npc)) is not None and c.location_id != here  # 在自己家里的不算撞见
            if where.get(npc) == here and npc not in engaged and away:
                interrupt(npc, EncounterKind.MEET_PLAYER, me, here, state.tick)
    for npc in sorted(state.agendas):
        agenda = state.agendas[npc]
        if npc in engaged or npc not in atlas.characters or wounded(state, npc):
            continue
        if npc in state.subdued:
            out.append(AgendaConcluded(npc_id=npc, how=AgendaEnd.BLOCKED, tick=state.tick))
            continue
        pos, since = where.get(npc), state.npc_since.get(npc, agenda.issued_tick)
        while pos is not None:
            if pos == agenda.target_id:
                out.append(AgendaConcluded(npc_id=npc, how=AgendaEnd.ARRIVED, tick=max(since, agenda.issued_tick)))
                break
            route = atlas.path(pos, agenda.target_id)
            if len(route) < 2:
                out.append(AgendaConcluded(npc_id=npc, how=AgendaEnd.BLOCKED, tick=state.tick))
                break
            nxt = route[1]
            arrive = since + atlas.cost(pos, nxt)
            if arrive > state.tick:
                break
            seen = "来到" if nxt == here else "离开" if pos == here else ""
            out.append(NpcMoved(npc_id=npc, from_location_id=pos, to_location_id=nxt, tick=arrive, witnessed=seen))
            pos, since = nxt, arrive
            where[npc] = nxt
            if nxt == here:
                interrupt(npc, EncounterKind.MEET_PLAYER, me, nxt, arrive)
                break
            if (rival := _rival(atlas, npc, nxt, where)) is not None and rival not in engaged:
                interrupt(npc, EncounterKind.CROSS_PATHS, rival, nxt, arrive)
                break
    return out


# ============================================================
#  裁决层 —— 狭路相逢的可裁区间与定案；撞见的收尾
# ============================================================
_FIGHTS = (SkirmishOutcome.COMER_WINS, SkirmishOutcome.BOTH_HURT, SkirmishOutcome.HOLDER_WINS)


@dataclass(frozen=True, slots=True)
class SkirmishStakes:
    encounter: Encounter
    comer: Character
    holder: Character
    admissible: tuple[SkirmishOutcome, ...]
    canonical: SkirmishOutcome


def _strength(state: PlayerState, c: Character) -> int:
    return c.tier.rank - (1 if wounded(state, c.id) else 0)


def skirmish_stakes(encounter: Encounter, state: PlayerState, atlas: Atlas) -> SkirmishStakes:
    """
    狭路相逢的确定性裁决与可裁区间：两个仁厚者相安无事；有狠辣者在场即动手——强者胜（带伤折一档），势均力敌两败俱伤；
    余下的（中庸对中庸、中庸对仁厚）差两档以上强者胜，否则口角。区间是确定性裁决在 [来者胜, 两败俱伤, 在此者胜] 上的左右一格，
    不全是狠辣者时另许口角；口角的区间是相安无事 / 口角 / 两败俱伤；相安无事的区间是相安无事 / 口角。
    """
    comer, holder = atlas.characters[encounter.npc_id], atlas.characters[encounter.other_id]
    dispositions = {comer.disposition, holder.disposition}
    diff = _strength(state, comer) - _strength(state, holder)
    if dispositions == {Disposition.MERCIFUL}:
        canonical = SkirmishOutcome.PASS
    elif Disposition.RUTHLESS in dispositions:
        canonical = _FIGHTS[0] if diff > 0 else _FIGHTS[2] if diff < 0 else _FIGHTS[1]
    elif abs(diff) >= 2:
        canonical = _FIGHTS[0] if diff > 0 else _FIGHTS[2]
    else:
        canonical = SkirmishOutcome.QUARREL
    if canonical in _FIGHTS:
        i = _FIGHTS.index(canonical)
        admissible = tuple(_FIGHTS[max(0, i - 1): i + 2])
        if dispositions != {Disposition.RUTHLESS}:
            admissible += (SkirmishOutcome.QUARREL,)
    elif canonical is SkirmishOutcome.QUARREL:
        admissible = (SkirmishOutcome.BOTH_HURT, SkirmishOutcome.QUARREL, SkirmishOutcome.PASS)
    else:
        admissible = (SkirmishOutcome.QUARREL, SkirmishOutcome.PASS)
    return SkirmishStakes(encounter, comer, holder, admissible, canonical)


def _flee(state: PlayerState, atlas: Atlas, npc: str, at: str, tick: int) -> list[DomainEvent]:
    """落败者往正典所在退一跳（已在家即不动）。"""
    home = atlas.characters[npc].location_id
    route = atlas.path(at, home) if home and home != at else ()
    if len(route) < 2:
        return []
    seen = "离开" if at == state.location_id else "来到" if route[1] == state.location_id else ""
    return [NpcMoved(npc_id=npc, from_location_id=at, to_location_id=route[1], tick=tick, witnessed=seen)]


_RUMOR = {
    SkirmishOutcome.COMER_WINS: "{a}与{b}在{p}交手，{b}落败",
    SkirmishOutcome.HOLDER_WINS: "{a}与{b}在{p}交手，{a}落败",
    SkirmishOutcome.BOTH_HURT: "{a}与{b}在{p}交手，两败俱伤",
    SkirmishOutcome.QUARREL: "{a}与{b}在{p}起了口角",
}


def settle_skirmish(
    stakes: SkirmishStakes, proposed: SkirmishOutcome | None, fact: str | None, state: PlayerState, atlas: Atlas,
    *, by: str = "规则",
) -> list[DomainEvent]:
    """
    定案：提议的结局不在区间里即取确定性裁决（by 随之改为规则）。动了手——此地留下交手的往事与血迹，落败者带伤 WOUND_TICKS、议程败退、
    往家退一跳（两败俱伤则双方带伤、议程中断、各自原地）；动手与口角都成一枚消息（动手传三跳、口角一跳）；
    地下城主给的一条微观事实（一行中文、≤40 字、无英文数字标记）挂在两人与此地身上。最后一条 EncounterResolved 让两人驻足。
    """
    enc = stakes.encounter
    outcome = proposed if proposed in stakes.admissible else stakes.canonical
    judge = by if proposed is not None and proposed is outcome else "规则"
    a, b, at, tick = stakes.comer, stakes.holder, enc.location_id, enc.tick
    place = atlas.names.get(at, at)
    out: list[DomainEvent] = []
    if outcome.fight:
        trace = EnvironmentalTrace(id=trace_id(at, BLOOD.description, tick), location_id=at, description=BLOOD.description,
                                   born_tick=tick, decay_ticks=BLOOD.decay_ticks)
        who = (a.id, b.id)
        out += [TraceLeft(trace=trace), ActivityStarted(activity=Activity(
            id=activity_id(ActivityKind.FIGHT, at, who, tick), kind=ActivityKind.FIGHT, participants=who, location_id=at,
            started_tick=tick, ends_tick=tick + 1, trace_id=trace.id))]
        losers = {SkirmishOutcome.COMER_WINS: (b.id,), SkirmishOutcome.HOLDER_WINS: (a.id,)}.get(outcome, (a.id, b.id))
        for loser in losers:
            out.append(NpcWounded(npc_id=loser, until_tick=tick + WOUND_TICKS, cause=f"{place}一战"))
            if loser in state.agendas:
                how = AgendaEnd.INTERRUPTED if outcome is SkirmishOutcome.BOTH_HURT else AgendaEnd.ROUTED
                out.append(AgendaConcluded(npc_id=loser, how=how, tick=tick))
            if outcome is not SkirmishOutcome.BOTH_HURT:
                out += _flee(state, atlas, loser, at, tick)
    if outcome in _RUMOR:
        text = _RUMOR[outcome].format(a=a.name, b=b.name, p=place)[:40]
        out.append(FactTokenSpawned(token=FactToken(
            id=token_id(text, at, tick), text=text, subject_ids=(a.id, b.id), origin_id=at, born_tick=tick, speed=1,
            radius=3 if outcome.fight else 1, reached=(at,))))
    if fact and 2 <= len(fact := fact.strip()) <= 40 and not _FLAW.search(fact):
        out.append(FactEmerged(fact_id=fact_id(fact), text=fact, subject_ids=(a.id, b.id, at)))
    out.append(EncounterResolved(
        encounter_id=enc.id, kind=enc.kind, location_id=at, npc_ids=(a.id, b.id), outcome=outcome,
        by="地下城主" if judge == "地下城主" else "规则", resume_tick=tick + SKIRMISH_PAUSE, witnessed=at == state.location_id,
    ))
    return out


def meet(encounter: Encounter, settled: Sequence[DomainEvent], state: PlayerState, *, by: str = "规则") -> list[DomainEvent]:
    """
    撞见的收尾：settled 是结果已定的物理边界里过了闸的事件（时钟 / 微观事实 / 名望，由应用层经 resolution.settle 得来），
    其后补一条 EncounterResolved——来者驻足 ENCOUNTER_PAUSE 刻再上路。
    """
    return [*settled, EncounterResolved(
        encounter_id=encounter.id, kind=encounter.kind, location_id=encounter.location_id, npc_ids=(encounter.npc_id,),
        by="地下城主" if by == "地下城主" else "规则", resume_tick=max(encounter.tick, state.tick) + ENCOUNTER_PAUSE,
        witnessed=encounter.location_id == state.location_id,
    )]
