"""
[INPUT]: 依赖 rules/base 的裁决结果与助手、rules/physical / talk / martial / parley 的各条 Rule 与两路接线，
         依赖 domain/approach 的 normalize（兼容表规整），依赖 domain/stakes 的 AnyStakes / Proposal / settle_any，
         依赖 domain/resolution 的 ResolutionOutput / Envelope / envelope_of / output_for / settle（语义物理闸门），
         依赖 domain/combat 的 CombatProposal，依赖 domain/social / covert 的 SocialStakes / CovertStakes，
         依赖 domain/events 的 ActionFailed / DomainEvent，依赖 domain/intent 的 ActionType / PlayerIntent，依赖 domain/snapshot 的 LocalSnapshot；
         PlayerState 仅作类型标注（避免与 aggregates 成环）
[OUTPUT]: 对外提供 门面 adjudicate()（合法性：规整 + 指称落地 + 物理/逻辑双重校验）、command()（命令耗时：意图 + time_cost，驳回只花一刻，移动取那条出路的 ExitView.time_cost）、stakes()（胜负未定之事的可裁区间：出手 / 交涉 / 暗中三路之一）、
          envelope()（任一获准之举的物理边界：三路赌注或结果已定之事）、decide()（合法性 + 推演过闸 + 定案 → 事件）、normalized()（按此情此景规整意图）、RULES 注册表，
          并原样转出 Rejection / Approval / Verdict / Rule / resolve / ground / skill_tier / player_tier / best_skill / retreat /
          required_regard / TRUST_RESTORED 与各条 Rule——拆包之前从 app.domain.rules 能导入的一切，拆包之后照样能导入
[POS]: domain 的裁决核心（Decider）门面：纯函数，不做 IO、不调大模型、不看时钟。
       意图先经 approach.normalize 规整（表外手段退回寻常、所图不配置空、话题落不了地置空），再交给各条 Rule：
       物理校验看快照（出口是否相连、人是否在场、物在谁手），逻辑校验看玩家状态与本体（火候、根基、门径、谁肯传授、伤势）；
       能力成长、物品获取、人际变化只能由此处从图谱拓扑推导得出。胜负未定之事由 Rule.stakes 圈出可裁区间（出手 / 交涉 / 暗中），
       地下城主的推演（ResolutionOutput：属性变化、时钟指令、微观事实、路由）先经 resolution.settle 过物理闸门推出结局与附带事件，
       结局再经 stakes.settle_any 落成路线事件——大模型描述发生了什么，领域决定它算不算数；规则与气运的结局经 output_for 走同一道闸门。
       结果已定之事（FIXED）也可以带一份推演：只许动时钟、留事实、折损名望。
       交与暗两路的定案经 parley.settled 落为事件（Parleyed / Maneuvered 及其附带）；险物不论经哪一路到手，都追加一次留一口气的伤。
       每种动作一条 Rule（开闭：新动作 = 新 Rule + 注册一行；新的模糊动作 = 覆写 stakes 钩子），options 生成器复用 adjudicate 过滤出合法行为。
       驳回入账为 ActionFailed，带上 unlock（怎样才行）、玩家当时的所图与手段、落了地的对象与标的（target_id / subject_id）。
       空间与 NPC 生态：移动按方位把手落地、耗时取那条出路的 time_cost；寻常攀谈的话题落在此地或去处即问路（PlacesLearned）；
       出手与暗取对此世带伤之人（CharacterView.wounded）境界折一档，交涉不折
[PROTOCOL]: 变更时更新此头部，然后检查 rules/CLAUDE.md
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.domain.approach import normalize
from app.domain.combat import CombatProposal
from app.domain.commands import Command, time_cost
from app.domain.covert import CovertStakes
from app.domain.events import ActionFailed, DomainEvent, Moved, RelationChanged
from app.domain.intent import ActionType, PlayerIntent
from app.domain.models import Attitude
from app.domain.resolution import Envelope, ResolutionOutput, envelope_of, output_for, settle
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
from app.domain.rules.physical import (
    InvalidRule,
    MoveRule,
    ObserveRule,
    RestRule,
    TakeRule,
    ThinkRule,
    UseRule,
    handled,
)
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
    "ThinkRule",
    "UseRule",
    "Verdict",
    "_retreat",
    "adjudicate",
    "best_skill",
    "command",
    "decide",
    "envelope",
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
    ActionType.THINK: ThinkRule(),
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


def envelope(intent: PlayerIntent, state: PlayerState, snap: LocalSnapshot) -> Envelope | None:
    """获准之举的物理边界：胜负未定之事是三路赌注之一，结果已定之事是 FIXED（推演只能动时钟、事实与名望）。驳回为 None。"""
    verdict = adjudicate(intent, state, snap)
    if isinstance(verdict, Rejection):
        return None
    at_stake = RULES[verdict.intent.action_type].stakes(verdict, state, snap)
    return envelope_of(at_stake, state, snap, target_id=verdict.target)


def command(intent: PlayerIntent, state: PlayerState, snap: LocalSnapshot) -> Command:
    """
    裁定一条命令花多少刻（domain/commands 的封闭表）：驳回只花一刻；移动取所走那条出路的耗时（ExitView.time_cost：道路注记或 geography 推出的）。
    意图按此情此景规整后装进 Command——世界心跳据 time_cost 走动。
    """
    verdict = adjudicate(intent, state, snap)
    if isinstance(verdict, Rejection):
        return Command(intent=normalized(intent, state, snap), time_cost=time_cost(intent.action_type, approved=False))
    action = verdict.intent.action_type
    if action is ActionType.MOVE:
        way = next(e for e in snap.exits if e.to_id == verdict.target and e.label == verdict.exit_label)
        return Command(intent=verdict.intent, time_cost=time_cost(action, way_cost=way.time_cost))
    return Command(intent=verdict.intent, time_cost=time_cost(action))


def _after(route: list[DomainEvent], extras: tuple[DomainEvent, ...]) -> list[DomainEvent]:
    """
    闸门的附带事件排在路线事件之后。旁人人情的代价（basis「代价」）按路线落账之后的人情再降一档：
    涟漪刚让左子穆敌视你，代价不能反把他拉回戒备——已敌视的就不再折。
    """
    settled_regard = {e.character_id: e.attitude for e in route if isinstance(e, RelationChanged)}
    kept: list[DomainEvent] = []
    for e in extras:
        if isinstance(e, RelationChanged) and e.basis == "代价" and e.character_id in settled_regard:
            now = settled_regard[e.character_id]
            if now is Attitude.HOSTILE:
                continue
            e = e.model_copy(update={"attitude": now.step(-1)})
        kept.append(e)
    return kept


def decide(
    intent: PlayerIntent,
    state: PlayerState,
    snap: LocalSnapshot,
    proposal: ResolutionOutput | Proposal | CombatProposal | None = None,
) -> list[DomainEvent]:
    """
    意图 → 推演过闸 → 定案 → 事件。驳回同样入账为 ActionFailed：失败也是历史。
    胜负未定之事：地下城主的推演（ResolutionOutput）或规则 / 气运的结局（Proposal，经 output_for 变成同形的推演）一律经 resolution.settle 过闸，
    推出的结局再经 settle_any 落成路线事件，闸门的附带事件（代价、时钟、坍缩、事实）随后入账；没有提议即取确定性裁决——离线也能玩。
    结果已定之事只有带着推演来时才过闸（只收时钟、事实与名望的折损）。闸门要求脱身而路线没有逃，就沿 retreat 补一次夺路而逃。
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
    if at_stake is None and not isinstance(proposal, ResolutionOutput):
        events = rule.consequences(verdict, state, snap, None)
        return events + handled(events, state, snap)
    env = envelope_of(at_stake, state, snap, target_id=verdict.target)
    if not isinstance(proposal, ResolutionOutput):
        proposal = output_for(env, proposal.outcome if proposal else None, proposal.hp_change if proposal else None)
    settlement = settle(env, proposal, state, snap)
    ruling = settle_any(at_stake, settlement.proposal) if at_stake is not None else None
    if isinstance(at_stake, SocialStakes | CovertStakes):
        events = settled(at_stake, ruling, state)
    else:
        events = rule.consequences(verdict, state, snap, ruling)
    events += _after(events, settlement.events)
    fled = any(isinstance(e, Moved) and e.fleeing for e in events)
    if settlement.flee and not fled and (way := retreat(state, snap)) is not None:
        events.append(Moved(from_location_id=state.location_id, to_location_id=way.to_id, exit_label=way.label, fleeing=True))
    return events + handled(events, state, snap)
