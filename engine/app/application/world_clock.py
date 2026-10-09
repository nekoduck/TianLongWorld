"""
[INPUT]: 依赖 domain/heartbeat 的 Atlas / aftermath / spread / ecology（世界物理的纯函数），依赖 domain/aggregates 的 PlayerState / evolve，
         依赖 domain/commands 的 Command，依赖 domain/events 的 DomainEvent / TimePassed，依赖 domain/snapshot 的 LocalSnapshot
[OUTPUT]: 对外提供 WorldClock（advance：一条命令定案之后的世界心跳 → 待入账的事件）
[POS]: application 的世界时钟：把命令的 time_cost 绑定成世界心跳——没有一条命令不花时间，时间一走，世界就跟着走。
       它只编排、不裁判：每一步都是 domain/heartbeat 的纯函数，状态由领域的 evolve 逐步折叠（与聚合根、内存图谱同一套折叠）；
       次序是因果的次序——
         1. 余波：这一招在出招之地留下的活动、痕迹、人群溃散与消息（出招那一刻的 tick）；
         2. TimePassed(time_cost)：时间走了，到期的痕迹与随之消散的往事由折叠剪掉；
         3. 扩散：这几刻里每枚消息沿路又传开几处；
         4. 生态：跨过的每个黎明结算一次风化与顺手牵羊。
       死者没有心跳：定案里有 PlayerDied，这条流就此封存，世界时钟不再为它走动。
       由 TurnPipeline 在命令侧玩家锁里同步调用，返回的事件与定案事件一并原子追加
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from collections.abc import Sequence
from functools import reduce

from app.domain.aggregates import PlayerState, evolve
from app.domain.commands import Command
from app.domain.events import DomainEvent, TimePassed
from app.domain.heartbeat import Atlas, aftermath, ecology, spread
from app.domain.snapshot import LocalSnapshot


def _fold(state: PlayerState, events: Sequence[DomainEvent]) -> PlayerState:
    return reduce(evolve, events, state)


class WorldClock:
    def __init__(self, atlas: Atlas) -> None:
        self._atlas = atlas

    def advance(
        self, command: Command, before: LocalSnapshot, state: PlayerState, decided: Sequence[DomainEvent]
    ) -> list[DomainEvent]:
        """state 是定案之前的玩家状态、decided 是定案事件、before 是出招时的快照：返回心跳事件（余波 → 时间 → 扩散 → 生态）。"""
        after = _fold(state, decided)
        if not after.alive:
            return []
        deeds = aftermath(decided, before, after.tick)
        passed = TimePassed(ticks=command.time_cost)
        later = _fold(after, [*deeds, passed])
        ripples = spread(later.tokens, self._atlas, command.time_cost)
        dawn = ecology(_fold(later, ripples), self._atlas, after.tick)
        return [*deeds, passed, *ripples, *dawn]
