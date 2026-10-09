"""
[INPUT]: 依赖 application/resolution_agent 的 Resolver / Resolution，依赖 domain/resolution 的 Envelope，
         依赖 domain/intent 的 PlayerIntent，依赖 domain/aggregates 的 PlayerState，依赖 domain/snapshot 的 LocalSnapshot
[OUTPUT]: 对外提供 AdjudicationSlot（一回合一席裁决：resolve(env, scene, state, intent, said, *, clicked)）
[POS]: application 的「一席裁决」：一回合里谁来为一招获准之举提议，只在这里定——
         驳回（env 为 None）→ None；
         点选 → 胜负未定（env.contested）交给气运（FortuneResolver，确定性、不调大模型；FORTUNE_ON_CLICK 关掉时为 None，即 canonical），其余 None；
         自由文本 → 胜负未定，或此景挂着时钟（暗流可能被这一举推动）→ 地下城主（Resolver，每回合至多调它一次；它内部的重采样与兜底是它自己的事）；
         其余 None，零调用（领域取确定性裁决）。
       三路（出手 / 交涉 / 暗中）与结果已定之事同一个口径。推演不是散文：它的微观事实经领域闸门落为 FactEmerged 入账，叙事从账上读，
       这里不再有「速写只配被采纳的结局」那道过滤
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from app.application.resolution_agent import Resolution, Resolver
from app.domain.aggregates import PlayerState
from app.domain.intent import PlayerIntent
from app.domain.resolution import Envelope
from app.domain.snapshot import LocalSnapshot


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
        env: Envelope | None,
        scene: LocalSnapshot,
        state: PlayerState,
        intent: PlayerIntent,
        said: str | None,
        *,
        clicked: bool,
    ) -> Resolution | None:
        if env is None:
            return None  # 驳回：谁也不请
        if clicked:  # 点选不花钱：只有胜负未定之事才掷一次气运，时钟留给自由文本
            if env.contested and self._fortune is not None:
                return await self._fortune.resolve(env, scene, state, intent, said)
            return None
        if env.contested or scene.clocks:
            return await self._resolver.resolve(env, scene, state, intent, said)
        return None  # 确定之事、又无暗流：谁也不请

