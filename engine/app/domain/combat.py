"""
[INPUT]: 依赖 domain/models 的 Tier / Disposition，依赖 domain/progression 的 MAX_HP / Vitality / vitality
[OUTPUT]: 对外提供 CombatOutcome（得手 / 相持 / 轻伤 / 重伤 / 毙命）、HP_BANDS（每种结果的气血扣减区间）、
          Stakes（一次出手的赌注：可裁区间 + 确定性裁决；seize_id 是夺物时得手即易手之物，target_id 与另两路赌注同一口径）与 assess()、CombatProposal（地下城主的提议）、CombatRuling 与 settle()（领域定案）
[POS]: domain 的模糊裁决护栏（Fuzzy Resolution 的"硬轨"）：
       境界差与性情不再一锤定生死，而是圈出一个可裁区间——地下城主（application/resolution_agent.py 的大模型）只能在区间里挑结果、
       在该结果的气血区间里挑扣减；挑出界、缺席或失灵，就取区间里的确定性裁决。
       大模型因此第一次参与"发生了什么"，却依旧写不出图谱不允许的结局：越级取胜不在区间里，非极端找死也不会毙命
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from dataclasses import dataclass
from enum import StrEnum

from app.domain.models import Disposition, Tier
from app.domain.progression import MAX_HP, Vitality, vitality


class CombatOutcome(StrEnum):
    """从玩家视角看的一招结果，由对玩家最有利到最不利排列。"""

    SUCCESS = "得手"  # 对手被制住
    STALEMATE = "相持"
    MINOR_WOUND = "轻伤"  # 吃了亏，带着轻伤退开
    SEVERE_WOUND = "重伤"  # 重伤逃脱：保住了性命
    DEATH = "毙命"  # 极端找死（随后必有 PlayerDied）


# 每种结果的气血变化区间（闭区间，负数为扣减）
HP_BANDS: dict[CombatOutcome, tuple[int, int]] = {
    CombatOutcome.SUCCESS: (-5, 0),
    CombatOutcome.STALEMATE: (-10, -2),
    CombatOutcome.MINOR_WOUND: (-25, -10),
    CombatOutcome.SEVERE_WOUND: (-70, -45),
    CombatOutcome.DEATH: (-MAX_HP, -MAX_HP),
}

# 伤势让"以卵击石"更重一分：境界差 + 伤势加码 ≥ 3 即为极端找死
_RECKLESS = {Vitality.HALE: 0, Vitality.HURT: 0, Vitality.WOUNDED: 1, Vitality.DYING: 2}
_SUICIDAL = 3


@dataclass(frozen=True, slots=True)
class Stakes:
    """一次出手的赌注：领域依图谱事实算出的可裁区间。admissible 由对玩家最有利到最不利排列，canonical 是其中的确定性裁决。"""

    defender_id: str
    skill_id: str | None
    item_id: str | None
    attacker_tier: Tier
    defender_tier: Tier
    disposition: Disposition
    player_hp: int
    admissible: tuple[CombatOutcome, ...]
    canonical: CombatOutcome
    seize_id: str | None = None  # 夺物：得手即易手的那件东西（item_id 是出手所持的兵器，两者不混）

    @property
    def target_id(self) -> str:
        """三路赌注同一个口径：赌的是对谁。"""
        return self.defender_id

    @property
    def gap(self) -> int:
        return self.defender_tier.rank - self.attacker_tier.rank

    @property
    def contested(self) -> bool:
        """结果未定：区间里不止一种结局，才值得请地下城主来裁。"""
        return len(self.admissible) > 1


def _envelope(gap: int, disposition: Disposition, hp: int) -> tuple[tuple[CombatOutcome, ...], CombatOutcome]:
    o = CombatOutcome
    if gap < 0:  # 技高一筹：稳稳制住
        return (o.SUCCESS,), o.SUCCESS
    if gap == 0:  # 旗鼓相当：胜负皆有可能，最多吃点小亏
        return (o.SUCCESS, o.STALEMATE, o.MINOR_WOUND), o.STALEMATE
    ruthless = disposition is Disposition.RUTHLESS
    if ruthless and gap + _RECKLESS[vitality(hp)] >= _SUICIDAL:  # 极端找死：以卵击石，或重伤未愈又去撩拨狠辣之人
        return (o.SEVERE_WOUND, o.DEATH), o.DEATH
    if gap == 1:  # 略逊一筹：狠辣之人下手重
        if ruthless:
            return (o.MINOR_WOUND, o.SEVERE_WOUND), o.SEVERE_WOUND
        return (o.STALEMATE, o.MINOR_WOUND, o.SEVERE_WOUND), o.MINOR_WOUND
    # 云泥之别：仁厚者手下留情，中庸者打发了事，狠辣者往死里打——但还留你一条命逃走
    if disposition is Disposition.MERCIFUL:
        return (o.MINOR_WOUND, o.SEVERE_WOUND), o.MINOR_WOUND
    if not ruthless:
        return (o.MINOR_WOUND, o.SEVERE_WOUND), o.SEVERE_WOUND
    return (o.SEVERE_WOUND,), o.SEVERE_WOUND


def assess(
    *,
    defender_id: str,
    skill_id: str | None,
    item_id: str | None,
    attacker: Tier,
    defender: Tier,
    disposition: Disposition,
    player_hp: int,
    seize_id: str | None = None,
) -> Stakes:
    admissible, canonical = _envelope(defender.rank - attacker.rank, disposition, player_hp)
    return Stakes(
        defender_id=defender_id, skill_id=skill_id, item_id=item_id, attacker_tier=attacker, defender_tier=defender,
        disposition=disposition, player_hp=player_hp, admissible=admissible, canonical=canonical, seize_id=seize_id,
    )


@dataclass(frozen=True, slots=True)
class CombatProposal:
    """地下城主的提议：未经 settle 定案之前什么都不是。hp_change 宽容地按绝对值理解为扣减。"""

    outcome: CombatOutcome
    hp_change: int


@dataclass(frozen=True, slots=True)
class CombatRuling:
    outcome: CombatOutcome
    hp_change: int  # ≤ 0
    adopted: bool  # 采纳了地下城主的结局；未采纳时，它的叙事速写与定案不符，应当作废


def settle(stakes: Stakes, proposal: CombatProposal | None = None) -> CombatRuling:
    """把提议钳进可裁区间：结局出界取确定性裁决；扣减钳进该结局的区间；非毙命至少留一口气，毙命则气血归零。"""
    adopted = proposal is not None and proposal.outcome in stakes.admissible
    outcome = proposal.outcome if adopted and proposal else stakes.canonical
    low, high = HP_BANDS[outcome]
    wanted = -abs(proposal.hp_change) if adopted and proposal else (low + high) // 2
    delta = min(high, max(low, wanted))
    delta = -stakes.player_hp if outcome is CombatOutcome.DEATH else max(delta, 1 - stakes.player_hp)
    return CombatRuling(outcome=outcome, hp_change=delta, adopted=adopted)
