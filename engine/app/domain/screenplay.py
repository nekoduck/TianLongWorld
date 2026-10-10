"""
[INPUT]: 依赖 domain/events 的领域事件，依赖 domain/arc 的 ArcPhase / CHAPTER_MIN_TICKS / phase_of，依赖 domain/intent 的 Aim / Approach，
         依赖 domain/outcomes 的 SocialOutcome；PlayerState 只在类型标注里（aggregates 运行期导入本模块，反向不得）
[OUTPUT]: 对外提供 编剧代理的领域规则——
          弧光：PLEAS（求人之事的所图）、mark(event, player_id)（一条事件 → 弧光标记：出手杀伐、交涉碰壁与求而不得孤绝、
          结交化解如愿与赠予侠义、暗取与计谋诡道、修习求道、自行离去漂泊；其余 None）、chapter_due(state)（阶段已变且距上一章够久 → 新阶段，否则 None）；
          因果线（domain-karma 轨补齐）：Seed、SEEDS_MAX、seeds()、KarmaProposal、admit_karma()、canonical_karma()、intersects()、closures()
[POS]: domain 的编剧物理：编剧代理（application/narrative/screenwriter）只提议，这里定案——弧光标记由 evolve 折叠（与聚合根、内存图谱同一套）、
       章回何时该换由 chapter_due 判定、因果线的种子由确定性的预筛选出（结仇、放生、拾遗、欺瞒）、大模型的评估经 admit_karma 闸门才入账、
       NPC 的行止与因果线的物理交集由 intersects 判定（亲历、传闻、途经）、了结由 closures 判定。这里一个大模型也不调，也不改变任何物理事实
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.domain.arc import CHAPTER_MIN_TICKS, ArcPhase, phase_of
from app.domain.events import (
    ActionFailed,
    DomainEvent,
    ItemTransferred,
    Maneuvered,
    Moved,
    Parleyed,
    SkillExecuted,
    SkillPracticed,
)
from app.domain.intent import Aim, Approach
from app.domain.outcomes import SocialOutcome

if TYPE_CHECKING:
    from app.domain.aggregates import PlayerState

# 求人之事：碰了壁、被拒绝，算「求助无门」
PLEAS = frozenset({Aim.LEARN, Aim.BEFRIEND, Aim.DEFUSE, Aim.ASK, Aim.PROBE})
_KIND = frozenset({Aim.BEFRIEND, Aim.DEFUSE})
_DENIED = frozenset({SocialOutcome.NOTHING, SocialOutcome.REBUFFED, SocialOutcome.FALLOUT})
_GRANTED = frozenset({SocialOutcome.GRANTED, SocialOutcome.SOFTENED})


# ============================================================
#  弧光 —— 一条事件折成一枚标记，近来十二枚定下当下阶段
# ============================================================
def mark(event: DomainEvent, player_id: str) -> ArcPhase | None:
    """
    一条事件 → 弧光标记（只看玩家自己的举动）：出手 → 杀伐；计谋交涉与暗取 → 诡道；求人之事碰壁、无果、翻脸或被驳回 → 孤绝；
    结交 / 化解如愿或松动、把东西交到别人手里 → 侠义；修习 → 求道；自行离去（不是夺路而逃）→ 漂泊。其余不成标记。
    """
    match event:
        case SkillExecuted():
            return ArcPhase.CARNAGE
        case Maneuvered() | Parleyed(approach=Approach.GUILE):
            return ArcPhase.GUILE
        case Parleyed(aim=aim, outcome=outcome) if aim in PLEAS and outcome in _DENIED:
            return ArcPhase.FORSAKEN
        case ActionFailed(aim=aim) if aim in PLEAS:
            return ArcPhase.FORSAKEN
        case Parleyed(aim=aim, outcome=outcome) if aim in _KIND and outcome in _GRANTED:
            return ArcPhase.CHIVALRY
        case ItemTransferred(from_holder=giver, to_holder=taker) if giver == player_id and taker.startswith("chr:"):
            return ArcPhase.CHIVALRY
        case SkillPracticed():
            return ArcPhase.SEEKER
        case Moved(fleeing=False):
            return ArcPhase.DRIFT
    return None


def chapter_due(state: PlayerState) -> ArcPhase | None:
    """该开新章了吗：近来的弧光成了一个新阶段（初入江湖不算新章）、且距上一章开章已过 CHAPTER_MIN_TICKS。"""
    phase = phase_of(state.arc_marks)
    if phase is ArcPhase.DAWN or phase is state.chapter.phase:
        return None
    if state.tick - state.chapter.opened_tick < CHAPTER_MIN_TICKS:
        return None
    return phase
