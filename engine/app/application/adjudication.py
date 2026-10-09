"""
[INPUT]: 依赖 application/resolution_agent 的 Resolver / Resolution，依赖 domain/stakes 的 AnyStakes，
         依赖 domain/events 的 SkillExecuted / Parleyed / Maneuvered / DomainEvent，依赖 domain/aggregates 的 PlayerState，依赖 domain/snapshot 的 LocalSnapshot
[OUTPUT]: 对外提供 AdjudicationSlot（一回合一席裁决：resolve(stakes, scene, state, said, *, clicked) 与 adopted_sketch()）、adopted_sketch()
[POS]: application 的「一席裁决」：一回合里谁来为胜负未定之事提议，只在这里定——
         无赌注或结果已定（不 contested）→ None，零调用（领域取确定性裁决）；
         点选 → 气运（FortuneResolver，确定性、不调大模型；FORTUNE_ON_CLICK 关掉时为 None，即 canonical）；
         自由文本 → 地下城主（Resolver，每回合至多调它一次；它内部的重采样与兜底是它自己的事）。
       三路（出手 / 交涉 / 暗中）同一个口径。速写只配它被采纳的结局：入账的 SkillExecuted / Parleyed / Maneuvered 的 outcome
       就是提议的那一个，才交给叙事；被领域钳回确定性裁决的，速写与定案矛盾，当场作废——它本就不入事件、不入记忆
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from collections.abc import Sequence

from app.application.resolution_agent import Resolution, Resolver
from app.domain.aggregates import PlayerState
from app.domain.events import DomainEvent, Maneuvered, Parleyed, SkillExecuted
from app.domain.snapshot import LocalSnapshot
from app.domain.stakes import AnyStakes


def adopted_sketch(resolution: Resolution | None, events: Sequence[DomainEvent]) -> str:
    """速写只在结局被采纳时保留：取这一回合第一条定案事件（出手 / 交涉 / 暗取），其 outcome 正是提议的结局。"""
    if resolution is None or resolution.proposal is None or not resolution.narrative_hint:
        return ""
    ruled = next((e for e in events if isinstance(e, SkillExecuted | Parleyed | Maneuvered)), None)
    adopted = ruled is not None and ruled.outcome is resolution.proposal.outcome
    return resolution.narrative_hint if adopted else ""


class AdjudicationSlot:
    """
    每回合一席：resolver 是自由文本回合的裁决者（地下城主，或离线时的 CanonicalResolver），
    fortune 是点选回合的裁决者（FortuneResolver；None 即点选一律取确定性裁决）。
    """

    def __init__(self, resolver: Resolver, fortune: Resolver | None = None) -> None:
        self._resolver = resolver
        self._fortune = fortune

    async def resolve(
        self,
        stakes: AnyStakes | None,
        scene: LocalSnapshot,
        state: PlayerState,
        said: str | None,
        *,
        clicked: bool,
    ) -> Resolution | None:
        if stakes is None or not stakes.contested:
            return None  # 确定之事：谁也不请
        if clicked:
            return await self._fortune.resolve(stakes, scene, state, said) if self._fortune else None
        return await self._resolver.resolve(stakes, scene, state, said)

    @staticmethod
    def adopted_sketch(resolution: Resolution | None, events: Sequence[DomainEvent]) -> str:
        return adopted_sketch(resolution, events)
