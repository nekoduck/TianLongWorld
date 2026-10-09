"""
[INPUT]: 依赖 briefs/combat / social / covert 三份简报与各自的铁律段，依赖 application/ports 的 JsonSchema，
         依赖 domain/stakes 的 AnyStakes / route_of，依赖 domain/approach 的 Route，依赖 domain/aggregates 的 PlayerState，依赖 domain/snapshot 的 LocalSnapshot
[OUTPUT]: 对外提供 brief()（按路线出 XML 简报）、schema()（按路线出结构化输出契约：出手 {outcome_type, hp_change, narrative_hint}，
          交涉与暗中 {outcome, narrative_hint}，结局枚举只列可裁区间）、Section 与 SECTIONS（每路一段铁律：裁什么、几条规矩、一个示例），
          并转出 combat_brief / social_brief / covert_brief / verdict_schema / outcome_schema / MEANING
[POS]: application 的地下城主简报包（门面）：三路赌注各一份简报，都只用快照里的 T=0 事实——
       Character.foreshadow（后文剧情）从不进快照，也就从不进任何简报；玩家尚不知道的见闻正文同样不进。
       简报是地下城主唯一的世界：简报之外的人物物功，速写里一字不提
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from typing import NamedTuple

from app.application.briefs import combat, covert, social
from app.application.briefs.combat import MEANING, combat_brief, verdict_schema
from app.application.briefs.covert import covert_brief
from app.application.briefs.social import social_brief
from app.application.ports import JsonSchema
from app.domain.aggregates import PlayerState
from app.domain.approach import Route
from app.domain.combat import Stakes
from app.domain.covert import CovertStakes
from app.domain.snapshot import LocalSnapshot
from app.domain.social import SocialStakes
from app.domain.stakes import AnyStakes

__all__ = [
    "MEANING",
    "SECTIONS",
    "Section",
    "brief",
    "combat_brief",
    "covert_brief",
    "outcome_schema",
    "schema",
    "social_brief",
    "verdict_schema",
]


class Section(NamedTuple):
    """一路的铁律段：scope 是「只裁什么」，rules 依次编号接在共用 0 号之后，example 附在最后。"""

    scope: str
    rules: tuple[str, ...]
    example: str


SECTIONS: dict[Route, Section] = {
    Route.COMBAT: Section(combat.SCOPE, combat.RULES, combat.EXAMPLE),
    Route.SOCIAL: Section(social.SCOPE, social.RULES, social.EXAMPLE),
    Route.COVERT: Section(covert.SCOPE, covert.RULES, covert.EXAMPLE),
}


def brief(stakes: AnyStakes, scene: LocalSnapshot, state: PlayerState, said: str | None) -> str:
    if isinstance(stakes, Stakes):
        return combat_brief(stakes, scene, state, said)
    if isinstance(stakes, SocialStakes):
        return social_brief(stakes, scene, state, said)
    return covert_brief(stakes, scene, state, said)


def outcome_schema(stakes: SocialStakes | CovertStakes) -> JsonSchema:
    """交涉与暗中没有扣减：只有结局（枚举名只列可裁区间——约束采样时区间外的结局采不出来）与一句速写。"""
    return {
        "type": "object",
        "properties": {
            "outcome": {"type": "string", "enum": [o.name for o in stakes.admissible]},
            "narrative_hint": {"type": "string"},
        },
        "required": ["outcome", "narrative_hint"],
        "additionalProperties": False,
    }


def schema(stakes: AnyStakes) -> JsonSchema:
    return verdict_schema(stakes) if isinstance(stakes, Stakes) else outcome_schema(stakes)
