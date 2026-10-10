"""
[INPUT]: 依赖 domain/clocks 的 ClockKind / NarrativeClock / clock_id / CLOCKS_MAX / PER_ANCHOR / NAME_CHARS，依赖 domain/events 的 ThreatDeclared / ClockStarted / ClockAdvanced，
         依赖 domain/lore 的 Stance / Trigger；PlayerState 只在类型标注里
[OUTPUT]: 对外提供 世界本份的物理（domain-friction 轨补齐感知与施压）——
          ThreatClock 与 CLOCK_OF（反应 → 挂在那人身上的时钟：盘问 → 疑心 4 格推 1、喝止 → 敌意 6 格推 1、敌意 → 敌意 4 格推 2）、PERIL（被卷进对决：挂在你身上的危机 4 格推 1）、
          declare(state, npc_id, npc_name, stance, trigger, basis, location_id, tick, item_id=)（一次对峙落成事件：ThreatDeclared + 挂上或推进那人身上的凶险时钟——
          他身上已有疑心 / 敌意的时钟即推进它（至多推到差一格满，宣告从不当场坍缩），没有且有空位才新挂；时钟的 id 记进 ThreatDeclared.clock_id）、
          peril(state, cause)（被卷进对决：危机时钟「刀剑无眼」挂在你身上、已有即推进一格，返回 (事件, clock_id)）；
          perceive / press / acquaintance / scars 见 DESIGN（domain-friction 轨）
[POS]: domain 的「世界本份」：世界不再绕开玩家——在场的人看得见你、记得你、被你触犯；推进中的局势碾到你所在之处时不再避让。
       一切都是 (玩家状态, 图谱快照, 这一回合入账的事件) 的纯函数，不做 IO、不调大模型：反应只有三档，落成悬在那人身上的时钟，
       置之不理一回合推一格，满了按时钟的坍缩表结算（domain/resolution.collapse）——摩擦是物理，不是剧本
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.domain.clocks import CLOCKS_MAX, NAME_CHARS, PER_ANCHOR, ClockKind, NarrativeClock, clock_id
from app.domain.events import ClockAdvanced, ClockStarted, DomainEvent, ThreatDeclared
from app.domain.lore import Stance, Trigger

if TYPE_CHECKING:
    from app.domain.aggregates import PlayerState

BASIS_CHARS = 24


@dataclass(frozen=True, slots=True)
class ThreatClock:
    kind: ClockKind
    maximum: int
    steps: int
    suffix: str  # 时钟名「某某的盘问」
    consequence: str


CLOCK_OF: dict[Stance, ThreatClock] = {
    Stance.INTERROGATE: ThreatClock(ClockKind.SUSPICION, 4, 1, "盘问", "翻脸相向"),
    Stance.WARN: ThreatClock(ClockKind.ENMITY, 6, 1, "驱赶", "动手驱赶"),
    Stance.HOSTILE: ThreatClock(ClockKind.ENMITY, 4, 2, "杀意", "拔刃相向"),
}
PERIL = ThreatClock(ClockKind.PERIL, 4, 1, "刀剑无眼", "被乱刃所伤")  # 对决的刀剑：挂在你身上
_THREATS = (ClockKind.SUSPICION, ClockKind.ENMITY)


def _named(who: str, suffix: str) -> str:
    """「某某的盘问」：名字再长也挂得上（时钟名至多 NAME_CHARS 字）。"""
    return f"{who[: NAME_CHARS - len(suffix) - 1]}的{suffix}"


def _hang(state: PlayerState, anchor: str, spec: ThreatClock, name: str, cause: str) -> tuple[list[DomainEvent], str | None]:
    """挂上一只新时钟（有空位才挂）或推进他身上已有的同类凶险时钟（至多推到差一格满）。返回 (事件, clock_id)。"""
    kinds = _THREATS if spec.kind in _THREATS else (spec.kind,)
    mine = [c for c in state.clocks if c.anchor_id == anchor and c.kind in kinds]
    if mine:
        clock = max(mine, key=lambda c: (c.kind is spec.kind, c.progress, c.id))
        steps = min(spec.steps, clock.maximum - 1 - clock.progress)
        if steps <= 0:
            return [], clock.id
        bumped = ClockAdvanced(clock_id=clock.id, steps=steps, name=clock.name, progress=clock.progress + steps,
                               maximum=clock.maximum, cause=cause)
        return [bumped], clock.id
    if len(state.clocks) >= CLOCKS_MAX or sum(1 for c in state.clocks if c.anchor_id == anchor) >= PER_ANCHOR:
        return [], None
    clock = NarrativeClock(id=clock_id(anchor, name), name=name, kind=spec.kind, anchor_id=anchor,
                           progress=min(spec.steps, spec.maximum - 1), maximum=spec.maximum, consequence=spec.consequence)
    return [ClockStarted(clock=clock, cause=cause)], clock.id


def declare(
    state: PlayerState, npc_id: str, npc_name: str, stance: Stance, trigger: Trigger, basis: str, location_id: str, tick: int,
    *, item_id: str | None = None,
) -> list[DomainEvent]:
    """一次对峙落成事件：ThreatDeclared 在前，随后挂上或推进他身上的凶险时钟（宣告从不当场坍缩）。"""
    spec = CLOCK_OF[stance]
    cause = basis[:BASIS_CHARS]
    clocks, hung = _hang(state, npc_id, spec, _named(npc_name, spec.suffix), cause or trigger.value)
    threat = ThreatDeclared(npc_id=npc_id, stance=stance, trigger=trigger, basis=cause, location_id=location_id, tick=tick,
                            item_id=item_id, clock_id=hung)
    return [threat, *clocks]


def peril(state: PlayerState, cause: str) -> tuple[list[DomainEvent], str | None]:
    """被卷进对决：危机时钟「刀剑无眼」挂在你身上（已有即推进一格）。返回 (事件, clock_id)。"""
    return _hang(state, state.player_id, PERIL, PERIL.suffix, cause)
