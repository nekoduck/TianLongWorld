"""
[INPUT]: 依赖 rules/base 的裁决结果与助手、rules/physical / talk / martial / parley 的各条 Rule 与两路接线，
         依赖 domain/approach 的 normalize（兼容表规整），依赖 domain/stakes 的 AnyStakes / Proposal / settle_any，
         依赖 domain/combat 的 CombatProposal，依赖 domain/social / covert 的 SocialStakes / CovertStakes，
         依赖 domain/events 的 ActionFailed / DomainEvent，依赖 domain/intent 的 ActionType / PlayerIntent，依赖 domain/snapshot 的 LocalSnapshot；
         PlayerState 仅作类型标注（避免与 aggregates 成环）
[OUTPUT]: 对外提供 门面 adjudicate()（合法性：规整 + 指称落地 + 物理/逻辑双重校验）、stakes()（胜负未定之事的可裁区间：出手 / 交涉 / 暗中三路之一）、
          decide()（合法性 + 定案 → 事件）、normalized()（按此情此景规整意图）、RULES 注册表，
          并原样转出 Rejection / Approval / Verdict / Rule / resolve / ground / skill_tier / player_tier / best_skill / retreat /
          required_regard / TRUST_RESTORED 与各条 Rule——拆包之前从 app.domain.rules 能导入的一切，拆包之后照样能导入
[POS]: domain 的裁决核心（Decider）门面：纯函数，不做 IO、不调大模型、不看时钟。
       意图先经 approach.normalize 规整（表外手段退回寻常、所图不配置空、话题落不了地置空），再交给各条 Rule：
       物理校验看快照（出口是否相连、人是否在场、物在谁手），逻辑校验看玩家状态与本体（火候、根基、门径、谁肯传授、伤势）；
       能力成长、物品获取、人际变化只能由此处从图谱拓扑推导得出。胜负未定之事由 Rule.stakes 圈出可裁区间（出手 / 交涉 / 暗中），
       地下城主的提议经 stakes.settle_any 钳进区间后才成为事件——大模型在这里有一票，但只能投给区间里的候选。
       交与暗两路的定案经 parley.settled 落为事件（Parleyed / Maneuvered 及其附带）；险物不论经哪一路到手，都追加一次留一口气的伤。
       每种动作一条 Rule（开闭：新动作 = 新 Rule + 注册一行；新的模糊动作 = 覆写 stakes 钩子），options 生成器复用 adjudicate 过滤出合法行为。
       驳回入账为 ActionFailed，带上 unlock（怎样才行）、玩家当时的所图与手段、落了地的对象与标的（target_id / subject_id）
[PROTOCOL]: 变更时更新此头部，然后检查 rules/CLAUDE.md
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.domain.approach import normalize
from app.domain.combat import CombatProposal
from app.domain.covert import CovertStakes
from app.domain.events import ActionFailed, DomainEvent
from app.domain.intent import ActionType, PlayerIntent
from app.domain.rules.base import (
    Approval,
    Rejection,
    Rule,
    Verdict,
    best_skill,
    ground,
    names,
    player_tier,
    resolve,
    skill_tier,
)
from app.domain.rules.martial import AttackRule, LearnRule, retreat
from app.domain.rules.parley import required_regard, settled
from app.domain.rules.physical import InvalidRule, MoveRule, ObserveRule, RestRule, TakeRule, UseRule, handled
from app.domain.rules.talk import TRUST_RESTORED, GiveRule, TalkRule
from app.domain.snapshot import LocalSnapshot
from app.domain.social import SocialStakes
from app.domain.stakes import AnyStakes, Proposal, settle_any

if TYPE_CHECKING:
    from app.domain.aggregates import PlayerState

__all__ = [
    "RULES",
    "TRUST_RESTORED",
    "Approval",
    "AttackRule",
    "GiveRule",
    "InvalidRule",
    "LearnRule",
    "MoveRule",
    "ObserveRule",
    "Rejection",
    "RestRule",
    "Rule",
    "TakeRule",
    "TalkRule",
    "UseRule",
    "Verdict",
    "_retreat",
    "adjudicate",
    "best_skill",
    "decide",
    "ground",
    "normalized",
    "player_tier",
    "required_regard",
    "resolve",
    "retreat",
    "skill_tier",
    "stakes",
]

_retreat = retreat  # 拆包前的旧名：脱身席的注释与用例以它指称"重伤逃脱的去处"

RULES: dict[ActionType, Rule] = {
    ActionType.OBSERVE: ObserveRule(),
    ActionType.MOVE: MoveRule(),
    ActionType.TALK: TalkRule(),
    ActionType.ATTACK: AttackRule(),
    ActionType.TAKE: TakeRule(),
    ActionType.GIVE: GiveRule(),
    ActionType.LEARN: LearnRule(),
    ActionType.REST: RestRule(),
    ActionType.USE: UseRule(),
    ActionType.INVALID: InvalidRule(),
}


# ============================================================
#  门面
# ============================================================
def _held(intent: PlayerIntent, state: PlayerState, snap: LocalSnapshot) -> bool:
    """TAKE 的物是否在他人手中（兼容表分两行）；落不了地的一律按他人之物那一行规整——反正规则会驳回。"""
    if intent.action_type is not ActionType.TAKE:
        return False
    thing = resolve(intent.target_entity or intent.item_used, [i for i in snap.items if i.holder_id != state.player_id], names)
    return thing is None or snap.character(thing.holder_id) is not None


def normalized(intent: PlayerIntent, state: PlayerState, snap: LocalSnapshot) -> PlayerIntent:
    """按此情此景规整意图：表外的手段退回寻常、所图与动作不配置空、话题落不了地置空。幂等。"""
    return normalize(intent, held=_held(intent, state, snap), grounds=lambda t: ground(t, snap) is not None)


def adjudicate(intent: PlayerIntent, state: PlayerState, snap: LocalSnapshot) -> Verdict:
    intent = normalized(intent, state, snap)
    return RULES[intent.action_type].adjudicate(intent, state, snap)


def stakes(intent: PlayerIntent, state: PlayerState, snap: LocalSnapshot) -> AnyStakes | None:
    """这一举动若获准，胜负是否未定、可裁区间几何（出手 / 交涉 / 暗中）。驳回的举动与结果确定的举动都没有赌注。"""
    verdict = adjudicate(intent, state, snap)
    if isinstance(verdict, Rejection):
        return None
    return RULES[verdict.intent.action_type].stakes(verdict, state, snap)


def decide(
    intent: PlayerIntent, state: PlayerState, snap: LocalSnapshot, proposal: Proposal | CombatProposal | None = None
) -> list[DomainEvent]:
    """
    意图 → 定案 → 事件。驳回同样入账为 ActionFailed：失败也是历史。
    胜负未定之事，地下城主的提议（proposal）经 settle_any 钳进可裁区间；没有提议即取区间里的确定性裁决——离线也能玩。
    """
    intent = normalized(intent, state, snap)
    verdict = RULES[intent.action_type].adjudicate(intent, state, snap)
    if isinstance(verdict, Rejection):
        return [
            ActionFailed(
                action=intent.action_type,
                target=intent.target_entity or intent.skill_used or intent.item_used,
                reason_code=verdict.code,
                reason=verdict.reason,
                unlock=verdict.unlock,
                aim=intent.aim,
                approach=intent.approach,
                target_id=verdict.target_id,
                subject_id=verdict.subject_id,
            )
        ]
    rule = RULES[intent.action_type]
    at_stake = rule.stakes(verdict, state, snap)
    ruling = settle_any(at_stake, proposal) if at_stake is not None else None
    if isinstance(at_stake, SocialStakes | CovertStakes):
        events = settled(at_stake, ruling, state)
    else:
        events = rule.consequences(verdict, state, snap, ruling)
    return events + handled(events, state, snap)
