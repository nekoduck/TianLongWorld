"""
[INPUT]: 依赖 domain/models 的 Tier，依赖 enum 的 StrEnum，依赖 zlib 的 crc32
[OUTPUT]: 对外提供 渐进式状态的两把尺——Mastery 火候（mastery_of 熟练度 × 悟性 → 火候、effective_tier 火候折算境界（降档且封顶）、FOUNDATION 根基门槛）、
          Guidance 修习方式、GAIN 基础所得与 gain()（越高深的武学一次所得越少）、aptitude_for() 根骨天定的悟性系数；
          Vitality 伤势（MAX_HP 气血上限、vitality() 气血 → 伤势、REST_GAIN 调息所得）
[POS]: domain 的渐进式状态（Progressive State）：内部是可加减的整数（熟练度、气血），对外只露语义标签（火候、伤势）。
       聚合根的 evolve 只做加法（reduce），一切折算都在这里——事件里记的是"练了多少"，"练到了哪一步"永远由此处现算：
       调整门槛或折算方式不必改写一条历史
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import zlib
from enum import StrEnum

from app.domain.models import Tier


# ============================================================
#  火候 —— 熟练度（SkillPracticed 之和）经悟性折算后的境地
# ============================================================
class Mastery(StrEnum):
    NOVICE = "初窥门径"
    MINOR = "略有小成"
    ADEPT = "融会贯通"
    EXPERT = "炉火纯青"
    PEAK = "登峰造极"

    @property
    def rank(self) -> int:
        return _MASTERY_RANK[self]


_MASTERY_RANK = {m: rank for rank, m in enumerate(Mastery)}

# 折算后熟练度的门槛（≥），自高而低查表；大于 0 即算入门
_THRESHOLDS: tuple[tuple[float, Mastery], ...] = (
    (110, Mastery.PEAK),
    (75, Mastery.EXPERT),
    (45, Mastery.ADEPT),
    (20, Mastery.MINOR),
)
# 火候未到，出手的境界打折扣：初窥门径低两档、略有小成低一档，融会贯通起才拿得出这门功夫的真本事；
# 且火候本身封顶——初窥门径的绝顶神功也拿不出一流本事（实测：捡到帛卷读两回就成了一流高手，"逻辑死线"就塌了）
_TIER_PENALTY = {Mastery.NOVICE: 2, Mastery.MINOR: 1}
_TIER_CEILING = {Mastery.NOVICE: Tier.THIRD, Mastery.MINOR: Tier.SECOND, Mastery.ADEPT: Tier.FIRST}

FOUNDATION = Mastery.MINOR  # 作为别的武学的根基，须先练到略有小成


def mastery_of(points: int, aptitude: float) -> Mastery | None:
    """熟练度 × 悟性 → 火候；从未修习（熟练度为 0）即未入门，返回 None。"""
    if points <= 0:
        return None
    effective = points * aptitude
    return next((m for threshold, m in _THRESHOLDS if effective >= threshold), Mastery.NOVICE)


def effective_tier(art_tier: Tier, mastery: Mastery) -> Tier:
    rank = max(0, art_tier.rank - _TIER_PENALTY.get(mastery, 0))
    if (ceiling := _TIER_CEILING.get(mastery)) is not None:
        rank = min(rank, ceiling.rank)
    return list(Tier)[rank]


class Guidance(StrEnum):
    """一次修习凭的是什么。入门只有一种；精进时有人点拨最快、照典籍次之、闭门苦练最慢。"""

    ENTRY = "入门"
    TEACHER = "名师点拨"
    MANUAL = "参照典籍"
    ALONE = "闭门苦练"


GAIN: dict[Guidance, int] = {Guidance.ENTRY: 10, Guidance.TEACHER: 15, Guidance.MANUAL: 10, Guidance.ALONE: 5}

# 武学越高深越难练：同样一次修习，绝顶之功只长三分之一。难度折进"这一次练了多少"（入账的事实），
# 而不是折进火候门槛——事件仍只记点数，火候的折算公式与武学无关，聚合根的 reduce 一个字不用改
_DIFFICULTY = {Tier.NONE: 1.0, Tier.THIRD: 1.0, Tier.SECOND: 1.5, Tier.FIRST: 2.0, Tier.PEERLESS: 3.0}


def gain(guidance: Guidance, art_tier: Tier) -> int:
    """一次修习所得熟练度：基础所得按武学境界的难度折算，至少一分。"""
    return max(1, round(GAIN[guidance] / _DIFFICULTY[art_tier]))

_APTITUDES = (0.8, 0.9, 1.0, 1.0, 1.1, 1.2, 1.3)


def aptitude_for(player_id: str) -> float:
    """根骨天定：悟性系数由玩家 id 确定性地抽出，写进 PlayerSpawned 后即成事实——日后改了这张表，老玩家的悟性也不会变。"""
    return _APTITUDES[zlib.crc32(player_id.encode()) % len(_APTITUDES)]


# ============================================================
#  伤势 —— 气血（HealthChanged 之和）落在哪一档
# ============================================================
MAX_HP = 100
REST_GAIN = 25  # 调息一回合恢复的气血


class Vitality(StrEnum):
    HALE = "安然无恙"
    HURT = "轻伤"
    WOUNDED = "重伤"
    DYING = "奄奄一息"

    @property
    def rank(self) -> int:
        return _VITALITY_RANK[self]


_VITALITY_RANK = {v: rank for rank, v in enumerate(Vitality)}


def vitality(hp: int) -> Vitality:
    if hp >= 90:
        return Vitality.HALE
    if hp >= 50:
        return Vitality.HURT
    if hp >= 20:
        return Vitality.WOUNDED
    return Vitality.DYING
