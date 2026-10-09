"""
[INPUT]: 依赖 application/briefs/combat 的 safe，依赖 domain/covert 的 CovertStakes，依赖 domain/outcomes 的 CovertOutcome，依赖 domain/intent 的 Approach，
         依赖 domain/rules 的 player_tier，依赖 domain/models 的 Attitude，依赖 domain/aggregates 的 PlayerState，依赖 domain/snapshot 的 LocalSnapshot
[OUTPUT]: 对外提供 covert_stakes()（暗取一路的 <stakes> 段：所取、手段、境界差与情势）、meaning()（某结局在这一次暗取里意味着什么）、MANNER（骗取 / 偷取）
[POS]: application/briefs 的「暗」：失主的身份、性情、态度与恩怨在 <people> 里（scene.py），这里只写这一手赌的是什么。
       物的险性（hazard）不写：到手即伤由规则另行追加，推演不该替它预告，也不该把它算进代价重复扣一次。
       境界差与情势与 domain/covert.assess_covert 的差额同一套加减，只是写成话
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from app.application.briefs.combat import safe
from app.domain.aggregates import PlayerState
from app.domain.covert import CovertStakes
from app.domain.intent import Approach
from app.domain.models import Attitude
from app.domain.outcomes import CovertOutcome
from app.domain.rules import player_tier
from app.domain.snapshot import LocalSnapshot

C = CovertOutcome
MANNER = {Approach.GUILE: "骗取", Approach.STEALTH: "偷取"}


def meaning(stakes: CovertStakes, outcome: CovertOutcome, owner: str) -> str:
    """结局的含义与 domain/covert.effects 逐条对应：得手即易手，被察觉即结仇（已敌视者不重复）。"""
    grudge = "" if stakes.attitude is Attitude.HOSTILE else "，从此与你结仇"
    return {
        C.CLEAN: f"你得手了，{owner}浑然不觉",
        C.FOILED: "你没能得手，好在无人察觉",
        C.EXPOSED: f"东西到了你手里，却被{owner}当场看破{grudge}",
        C.CAUGHT: f"你没能得手，还被{owner}当场撞破{grudge}",
    }[outcome]


def _edge(stakes: CovertStakes, gap: int, subdued: bool) -> str:
    rank = "与他境界相当" if gap == 0 else f"比他{'高' if gap > 0 else '低'}{'一二三四'[min(abs(gap), 4) - 1]}境"
    notes = []
    if subdued:
        notes.append("他已被你制住")
    if stakes.attitude.rank <= Attitude.WARY.rank:
        notes.append("他正提防你")
    elif stakes.approach is Approach.GUILE and stakes.attitude.rank >= Attitude.FRIENDLY.rank:
        notes.append("他信你")
    return rank + (f"；{'、'.join(notes)}" if notes else "")


def covert_stakes(stakes: CovertStakes, scene: LocalSnapshot, state: PlayerState) -> list[str]:
    """暗取的赌注：失主、所取之物（名字、种类、描述，不写险性）、手段、情势。暗中行事永不动武。每行逐值转义。"""
    owner = scene.character(stakes.target_id)
    thing = scene.item(stakes.item_id)
    what = f"{thing.name}｜{thing.kind or '物件'}｜{thing.description or '（无描述）'}" if thing else scene.label(stakes.item_id)
    gap = player_tier(state, scene).rank - owner.tier.rank if owner else 0
    lines = [
        f"失主：{owner.name if owner else scene.label(stakes.target_id)}",
        f"所取：{what}",
        f"手段：{stakes.approach.value}（{MANNER.get(stakes.approach, '暗取')}）",
        f"情势：你{_edge(stakes, gap, bool(owner and owner.subdued))}",
    ]
    return [safe(line) for line in lines]
