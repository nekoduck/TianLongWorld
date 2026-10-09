"""
[INPUT]: 依赖 domain/outcomes 的 CovertOutcome，依赖 domain/events 的 Maneuvered / ItemTransferred / RelationChanged / DomainEvent，
         依赖 domain/intent 的 Approach，依赖 domain/models 的 Attitude / Tier
[OUTPUT]: 对外提供 CovertStakes（一次暗中取物的赌注：可裁区间 + 确定性裁决）、assess_covert()（按境界差与失主的戒心圈区间）、
          CovertRuling 与 settle_covert()（出界取确定性裁决）、effects()（定案 → 事件）
[POS]: domain 的暗中行事硬轨，P1 只做一件事：计谋（骗取）或潜行（偷取）拿走他人身上之物。与 combat / social 对称——
       领域圈区间，地下城主在区间里挑。差额 margin = 你的境界 − 失主的境界；失主已被制住 +2，失主敌视或戒备 −1（正盯着你），
       骗取时失主友善以上 +1（信你）。区间按 outcomes 的次序（无痕 / 未遂 / 败露 / 失手，被察觉才是代价）切片：
         ≥2 无痕；1 无痕·未遂（无痕）；0 无痕·未遂·败露（未遂）；−1 未遂·败露·失手（未遂）；≤−2 败露·失手（失手）。
       效果：无痕 → 易手；未遂 → 什么也没惹出来；败露 → 易手且失主敌视；失手 → 失主敌视。每次都入账一条 Maneuvered。
       暗中行事永不致死：不产出 HealthChanged 或 PlayerDied（险物取到手的伤另由 rules 按物性追加，同样留一口气）
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from dataclasses import dataclass

from app.domain.events import DomainEvent, ItemTransferred, Maneuvered, RelationChanged
from app.domain.intent import Approach
from app.domain.models import Attitude, Tier
from app.domain.outcomes import CovertOutcome

C = CovertOutcome
_CAUGHT_CAUSE = {Approach.GUILE: "识破你的骗局", Approach.STEALTH: "撞破你行窃"}


@dataclass(frozen=True, slots=True)
class CovertStakes:
    """一次暗中取物的赌注：向谁（target_id，失主）下手、取什么（item_id）、凭什么手段；admissible 由轻到重。"""

    target_id: str
    item_id: str
    approach: Approach
    attitude: Attitude  # 失主此刻对你的态度
    margin: int
    admissible: tuple[CovertOutcome, ...]
    canonical: CovertOutcome

    @property
    def contested(self) -> bool:
        return len(self.admissible) > 1


@dataclass(frozen=True, slots=True)
class CovertRuling:
    outcome: CovertOutcome
    adopted: bool
    hp_change: int = 0  # 暗中行事不伤人：恒为 0，只为与 CombatRuling 同形


def _envelope(margin: int) -> tuple[tuple[CovertOutcome, ...], CovertOutcome]:
    if margin >= 2:
        return (C.CLEAN,), C.CLEAN
    if margin == 1:
        return (C.CLEAN, C.FOILED), C.CLEAN
    if margin == 0:
        return (C.CLEAN, C.FOILED, C.EXPOSED), C.FOILED
    if margin == -1:
        return (C.FOILED, C.EXPOSED, C.CAUGHT), C.FOILED
    return (C.EXPOSED, C.CAUGHT), C.CAUGHT


def assess_covert(
    *,
    target_id: str,
    item_id: str,
    approach: Approach,
    attitude: Attitude,
    player: Tier,
    holder: Tier,
    subdued: bool = False,
) -> CovertStakes:
    margin = player.rank - holder.rank + (2 if subdued else 0)
    if attitude.rank <= Attitude.WARY.rank:
        margin -= 1  # 他本就提防你：一举一动都在他眼里
    elif approach is Approach.GUILE and attitude.rank >= Attitude.FRIENDLY.rank:
        margin += 1  # 骗的是信你的人
    admissible, canonical = _envelope(margin)
    return CovertStakes(target_id=target_id, item_id=item_id, approach=approach, attitude=attitude, margin=margin,
                        admissible=admissible, canonical=canonical)


def settle_covert(stakes: CovertStakes, outcome: object | None = None) -> CovertRuling:
    """提议的结局在区间里即采纳，否则取确定性裁决。暗中行事的提议只有结局，没有扣减。"""
    if isinstance(outcome, CovertOutcome) and outcome in stakes.admissible:
        return CovertRuling(outcome=outcome, adopted=True)
    return CovertRuling(outcome=stakes.canonical, adopted=False)


def effects(stakes: CovertStakes, ruling: CovertRuling, player_id: str) -> list[DomainEvent]:
    o = ruling.outcome
    events: list[DomainEvent] = [
        Maneuvered(item_id=stakes.item_id, target_id=stakes.target_id, approach=stakes.approach, outcome=o)
    ]
    if o in (C.CLEAN, C.EXPOSED):
        events.append(ItemTransferred(item_id=stakes.item_id, from_holder=stakes.target_id, to_holder=player_id))
    if o in (C.EXPOSED, C.CAUGHT) and stakes.attitude is not Attitude.HOSTILE:
        events.append(RelationChanged(
            character_id=stakes.target_id, attitude=Attitude.HOSTILE,
            cause=_CAUGHT_CAUSE.get(stakes.approach, "察觉你暗中下手"), basis=o.value,
        ))
    return events
