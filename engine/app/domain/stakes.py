"""
[INPUT]: 依赖 domain/combat 的 Stakes / CombatOutcome / CombatProposal / CombatRuling / settle，依赖 domain/social 的 SocialStakes / SocialRuling / settle_social，
         依赖 domain/covert 的 CovertStakes / CovertRuling / settle_covert，依赖 domain/outcomes 的 SocialOutcome / CovertOutcome，
         依赖 domain/approach 的 Route
[OUTPUT]: 对外提供 AnyStakes（三路赌注的联合）、Outcome（三路结局的联合）、Proposal（通用提议：结局 + 扣减）、Ruling（三路定案的联合）、
          settle_any()（统一入口：分派到三路 settle，出界取确定性裁决）、route_of()（赌注属于哪一路）、Risk（稳妥 / 有险 / 凶险）与 risk_of()
[POS]: domain 的统一赌注门面：rules 圈出的可裁区间、地下城主的提议、选项的风险档都只经这里认三路赌注。
       三者同一口径：admissible 由对玩家最有利到最不利、canonical 是确定性裁决、contested 是否值得请地下城主、target_id 赌的是对谁。
       风险档只看区间最坏的一端，不露结局：出手 毙命 → 凶险、轻伤 / 重伤 → 有险、其余稳妥；交涉 翻脸 → 有险、其余稳妥；
       暗中 败露 / 失手 → 有险、其余稳妥；没有赌注（确定之事）一律稳妥
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from dataclasses import dataclass
from enum import StrEnum

from app.domain.approach import Route
from app.domain.combat import CombatOutcome, CombatProposal, CombatRuling, Stakes, settle
from app.domain.covert import CovertRuling, CovertStakes, settle_covert
from app.domain.outcomes import CovertOutcome, SocialOutcome
from app.domain.social import SocialRuling, SocialStakes, settle_social

type AnyStakes = Stakes | SocialStakes | CovertStakes
type Outcome = CombatOutcome | SocialOutcome | CovertOutcome
type Ruling = CombatRuling | SocialRuling | CovertRuling


@dataclass(frozen=True, slots=True)
class Proposal:
    """地下城主（或 FortuneResolver）对任一路赌注的提议：未经 settle_any 定案之前什么都不是。hp_change 只对出手有意义。"""

    outcome: Outcome
    hp_change: int = 0


def settle_any(stakes: AnyStakes, proposal: Proposal | CombatProposal | None = None) -> Ruling:
    """统一定案：出手照旧经 combat.settle（扣减钳进气血带、非毙命留一口气）；交涉与暗中只认区间里的结局，出界取确定性裁决。"""
    outcome = proposal.outcome if proposal is not None else None
    if isinstance(stakes, Stakes):
        if proposal is None or not isinstance(outcome, CombatOutcome):
            return settle(stakes, None)
        return settle(stakes, CombatProposal(outcome=outcome, hp_change=proposal.hp_change))
    if isinstance(stakes, SocialStakes):
        return settle_social(stakes, outcome)
    return settle_covert(stakes, outcome)


def route_of(stakes: AnyStakes | None) -> Route:
    if isinstance(stakes, Stakes):
        return Route.COMBAT
    if isinstance(stakes, SocialStakes):
        return Route.SOCIAL
    if isinstance(stakes, CovertStakes):
        return Route.COVERT
    return Route.FIXED


class Risk(StrEnum):
    """选项露出的语义风险档：只说最坏能坏到哪一步，不露结局。"""

    SAFE = "稳妥"
    RISKY = "有险"
    GRAVE = "凶险"


_WORST: dict[str, Risk] = {
    CombatOutcome.DEATH: Risk.GRAVE,
    CombatOutcome.SEVERE_WOUND: Risk.RISKY,
    CombatOutcome.MINOR_WOUND: Risk.RISKY,
    SocialOutcome.FALLOUT: Risk.RISKY,
    CovertOutcome.EXPOSED: Risk.RISKY,
    CovertOutcome.CAUGHT: Risk.RISKY,
}


def risk_of(stakes: AnyStakes | None) -> Risk:
    """只看区间最坏的一端（admissible 恒由好到坏排列，最坏的就是最后一格）；确定之事没有赌注，稳妥。"""
    if stakes is None:
        return Risk.SAFE
    return _WORST.get(stakes.admissible[-1], Risk.SAFE)
