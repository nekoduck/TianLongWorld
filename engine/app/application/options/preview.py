"""
[INPUT]: 依赖 domain/rules 的 stakes（可裁区间）/ Approval，依赖 domain/stakes 的 Risk / risk_of，依赖 domain/approach 的 Route，
         依赖 domain/intent 的 ActionType / Aim / Approach，依赖 domain/models 的 Attitude，依赖 domain/progression 的 Guidance / Vitality，
         依赖 domain/snapshot 的 LocalSnapshot，依赖 options/salience 的 Scored；PlayerState 仅作类型标注
[OUTPUT]: 对外提供 why()（≤12 字的确定性上榜缘由）、risk()（语义风险档：稳妥 / 有险 / 凶险）
[POS]: options 包的预览：选项露给玩家的两样东西——为什么上榜、最坏能坏到哪一步——都只由 (状态, 快照) 决定，绝不泄露结局。
       why 的先后：物归原主 → 心事线索（「换个手段」：已试过别的手段；「心事未了」）→ 人情与焦点（仇怨未了 / 与你有交情 / 方才 / 先前打过交道 /
       典籍在手 / 新近经手之物）→ 这一类举动本身的理由（P0 原句保留；P1 的新招各有一句：可结善缘、嫌隙或可化解、他或知内情、有势可借、
       不妨开口相求、可暗中下手、强取亦是一途、恳求或能打动、伤药在身；问路是「前路未明」）。席位的缘由（伤重宜调息）由 slate 盖在上面；
       出路的缘由随移动归导航而去（导航只标 retreat：仇人在侧时 rules.retreat 会走的那条）。
       risk = risk_of(rules.stakes(意图))：只看可裁区间最坏的一端，确定之事没有赌注即稳妥
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.application.options.salience import Scored
from app.domain import rules
from app.domain.approach import Route
from app.domain.intent import ActionType, Aim, Approach, PlayerIntent
from app.domain.models import Attitude
from app.domain.progression import Guidance, Vitality
from app.domain.snapshot import LocalSnapshot
from app.domain.stakes import Risk, risk_of

if TYPE_CHECKING:
    from app.domain.aggregates import PlayerState

_TALK_WHY = {Aim.PROBE: "他或知内情", Aim.DEFUSE: "嫌隙或可化解", Aim.BEFRIEND: "可结善缘"}
_TAKE_WHY = {
    Approach.WORDS: "不妨开口相求", Approach.FAVOR: "不妨开口相求", Approach.LEVERAGE: "有势可借",
    Approach.STEALTH: "可暗中下手", Approach.GUILE: "可暗中下手", Approach.FORCE: "强取亦是一途",
}


def risk(intent: PlayerIntent, state: PlayerState, snap: LocalSnapshot) -> Risk:
    return risk_of(rules.stakes(intent, state, snap))


def why(s: Scored, state: PlayerState, snap: LocalSnapshot, foes: set[str]) -> str:
    """上榜缘由：先看物归原主与心事，再看人情与焦点，再看这一类举动本身的理由。每句都只由状态与快照决定。"""
    action = s.action
    if action is ActionType.GIVE:
        return "物归原主"
    if s.thread is not None:
        return "换个手段" if s.thread.tried else "心事未了"
    if s.focus is not None:
        anchor = state.focus[s.focus]
        if anchor.startswith("chr:"):
            if anchor in foes:
                return "仇怨未了"
            if state.attitude_of(anchor).rank >= Attitude.FRIENDLY.rank:  # 友善与信赖都算交情
                return "与你有交情"
            return "方才打过交道" if s.focus == 0 and state.focus_fresh else "先前打过交道"
        if action is ActionType.LEARN:
            return "典籍在手，趁热打铁" if s.focus == 0 and state.focus_fresh else "典籍在手"
        return "新近经手之物"
    return _kind(s, state, snap, foes)


def _kind(s: Scored, state: PlayerState, snap: LocalSnapshot, foes: set[str]) -> str:
    ok, action, approach = s.approval, s.action, s.option.intent.approach
    match action:
        case ActionType.REST:
            return "伤重宜调息" if state.vitality.rank >= Vitality.WOUNDED.rank else "略作调养"
        case ActionType.USE:
            return "伤药在身"
        case ActionType.ATTACK:
            return "仇人当面" if ok.target in foes else "以武相见"
        case ActionType.TALK if approach is Approach.LEVERAGE:
            return "有势可借"
        case ActionType.TALK if approach is Approach.PLAIN and s.option.intent.topic == snap.location.name:
            return "前路未明"  # 问路：话题是此地
        case ActionType.TALK if ok.route is Route.SOCIAL and ok.aim in _TALK_WHY:
            return _TALK_WHY[ok.aim]
        case ActionType.TALK:
            return "可探口风"
        case ActionType.TAKE if ok.route is not Route.FIXED:
            return _TAKE_WHY.get(approach, "东西在他手上")
        case ActionType.TAKE:
            return "唾手可得" if ok.source == snap.location.id else "其人已被制住"
        case ActionType.LEARN if ok.route is Route.SOCIAL:
            return "恳求或能打动"
        case ActionType.LEARN:
            return {
                Guidance.ENTRY: "有人肯传授" if ok.target else "典籍在手",
                Guidance.TEACHER: "名师在侧",
                Guidance.MANUAL: "典籍在手",
            }.get(ok.guidance or Guidance.ALONE, "勤能补拙")
    return "看清形势"
