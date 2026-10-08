"""
[INPUT]: 依赖 domain/intent 的 ActionType / PlayerIntent，依赖 domain/events 的领域事件，依赖 domain/models 的 Tier / Attitude /
         Disposition / RelationKind / Transmission，依赖 domain/snapshot 的 LocalSnapshot 及视图；PlayerState 仅作类型标注（避免与 aggregates 成环）
[OUTPUT]: 对外提供 Rejection / Approval 裁决结果、adjudicate()（合法性：指称落地 + 物理/逻辑双重校验）、decide()（合法性 + 绝对结果 → 事件）、
          player_tier()（玩家境界）、duel()（以境界与性情定胜负的确定性对决）、resolve()（名称 → 实体的唯一匹配）
[POS]: domain 的裁决核心（Decider）：纯函数，不做 IO、不调大模型、不看时钟。
       物理校验看快照（出口是否相连、人是否在场、物在谁手），逻辑校验看玩家状态与本体（是否已会、前置是否齐备、谁肯传授）；
       能力成长、物品获取、人际变化只能由此处从图谱拓扑推导得出——大模型在这里没有一票。
       每种动作一条 Rule（开闭：新动作 = 新 Rule + 注册一行），options 生成器复用 adjudicate 过滤出合法行为
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.domain.events import (
    ActionFailed,
    CombatOutcome,
    Conversed,
    DomainEvent,
    ItemTransferred,
    Moved,
    PlayerDied,
    RelationChanged,
    SkillExecuted,
    SkillLearned,
)
from app.domain.intent import ActionType, PlayerIntent
from app.domain.models import Attitude, Disposition, RelationKind, Tier, Transmission
from app.domain.snapshot import CharacterView, LocalSnapshot, SkillView

if TYPE_CHECKING:
    from app.domain.aggregates import PlayerState


# ============================================================
#  裁决结果
# ============================================================
@dataclass(frozen=True, slots=True)
class Rejection:
    code: str
    reason: str


@dataclass(frozen=True, slots=True)
class Approval:
    """裁决通过：意图里的指称已落地为图谱实体 id。"""

    intent: PlayerIntent
    target: str | None = None
    item: str | None = None
    skill: str | None = None
    exit_label: str | None = None
    source: str | None = None  # TAKE：物品原持有者；LEARN：传功之人或所凭典籍


type Verdict = Approval | Rejection


# ============================================================
#  名称落地 —— 精确命中优先；退而求其次的包含匹配必须唯一，多义不猜
# ============================================================
def resolve[T](name: str | None, candidates: Iterable[T], names: Callable[[T], Iterable[str]]) -> T | None:
    if not name:
        return None
    wanted = name.strip()
    pool = list(candidates)
    exact = [c for c in pool if wanted in tuple(names(c))]
    if exact:
        return exact[0] if len(exact) == 1 else None
    if len(wanted) < 2:
        return None
    fuzzy = [c for c in pool if any(len(n) >= 2 and (n in wanted or wanted in n) for n in names(c))]
    return fuzzy[0] if len(fuzzy) == 1 else None


def _names(view: object) -> Iterable[str]:
    return view.names  # type: ignore[attr-defined]


def _list(snap: LocalSnapshot, ids: Iterable[str]) -> str:
    return "、".join(f"「{snap.label(i)}」" for i in ids)


# ============================================================
#  境界与对决 —— 无数值：只比境界高下，性情决定留不留手
# ============================================================
def player_tier(state: PlayerState, snap: LocalSnapshot) -> Tier:
    tiers = [view.tier for view in snap.skills if view.id in state.skills]
    return max(tiers, key=lambda t: t.rank, default=Tier.NONE)


def best_skill(state: PlayerState, snap: LocalSnapshot) -> SkillView | None:
    known = sorted((s for s in snap.skills if s.id in state.skills), key=lambda s: (-s.tier.rank, s.id))
    return known[0] if known else None


def duel(attacker: Tier, defender: Tier, disposition: Disposition) -> CombatOutcome:
    gap = defender.rank - attacker.rank
    if gap < 0:
        return CombatOutcome.PREVAILED
    if gap == 0:
        return CombatOutcome.STALEMATE
    if gap == 1:  # 略逊一筹：狠辣之人顺手取命，其余只是击退
        return CombatOutcome.FATAL if disposition is Disposition.RUTHLESS else CombatOutcome.REPELLED
    # 云泥之别：除非对方宅心仁厚，一招毙命
    return CombatOutcome.REPELLED if disposition is Disposition.MERCIFUL else CombatOutcome.FATAL


# ============================================================
#  规则
# ============================================================
class Rule(ABC):
    @abstractmethod
    def adjudicate(self, intent: PlayerIntent, state: PlayerState, snap: LocalSnapshot) -> Verdict: ...

    @abstractmethod
    def consequences(self, ok: Approval, state: PlayerState, snap: LocalSnapshot) -> list[DomainEvent]: ...


def _present(name: str | None, snap: LocalSnapshot) -> CharacterView | Rejection:
    if not name:
        return Rejection("NO_TARGET", "须说明对谁而为。")
    who = resolve(name, snap.characters, _names)
    if who is None:
        return Rejection("NOT_PRESENT", f"此处不见「{name}」。")
    return who


class ObserveRule(Rule):
    """静观是纯查询：没有事件，世界不因你看了一眼而改变。"""

    def adjudicate(self, intent: PlayerIntent, state: PlayerState, snap: LocalSnapshot) -> Verdict:
        return Approval(intent)

    def consequences(self, ok: Approval, state: PlayerState, snap: LocalSnapshot) -> list[DomainEvent]:
        return []


class MoveRule(Rule):
    def adjudicate(self, intent: PlayerIntent, state: PlayerState, snap: LocalSnapshot) -> Verdict:
        if not intent.target_entity:
            return Rejection("NO_TARGET", "欲往何处？须说出去向。")
        way = resolve(intent.target_entity, snap.exits, _names)
        if way is None:
            roads = "、".join(f"{e.label}（{e.to_name}）" for e in snap.exits) or "无路可走"
            return Rejection("NO_PATH", f"此处并无通往「{intent.target_entity}」的路。可行之路：{roads}。")
        return Approval(intent, target=way.to_id, exit_label=way.label)

    def consequences(self, ok: Approval, state: PlayerState, snap: LocalSnapshot) -> list[DomainEvent]:
        assert ok.target and ok.exit_label
        return [Moved(from_location_id=state.location_id, to_location_id=ok.target, exit_label=ok.exit_label)]


class TalkRule(Rule):
    def adjudicate(self, intent: PlayerIntent, state: PlayerState, snap: LocalSnapshot) -> Verdict:
        who = _present(intent.target_entity, snap)
        return who if isinstance(who, Rejection) else Approval(intent, target=who.id)

    def consequences(self, ok: Approval, state: PlayerState, snap: LocalSnapshot) -> list[DomainEvent]:
        assert ok.target
        return [Conversed(npc_id=ok.target)]


class AttackRule(Rule):
    def adjudicate(self, intent: PlayerIntent, state: PlayerState, snap: LocalSnapshot) -> Verdict:
        who = _present(intent.target_entity, snap)
        if isinstance(who, Rejection):
            return who
        if who.subdued:
            return Rejection("ALREADY_SUBDUED", f"{who.name}已被你制住，无还手之力。")
        skill = best_skill(state, snap)
        if intent.skill_used:
            skill = resolve(intent.skill_used, snap.known_skills, _names)
            if skill is None:
                return Rejection("NOT_KNOWN", f"你并不会「{intent.skill_used}」。")
        item = None
        if intent.item_used:
            item = resolve(intent.item_used, snap.inventory, _names)
            if item is None:
                return Rejection("NOT_CARRIED", f"你身上并无「{intent.item_used}」。")
        return Approval(intent, target=who.id, skill=skill.id if skill else None, item=item.id if item else None)

    def consequences(self, ok: Approval, state: PlayerState, snap: LocalSnapshot) -> list[DomainEvent]:
        assert ok.target
        foe = snap.character(ok.target)
        assert foe is not None
        art = snap.skill(ok.skill) if ok.skill else None
        outcome = duel(art.tier if art else Tier.NONE, foe.tier, foe.disposition)
        events: list[DomainEvent] = [
            SkillExecuted(skill_id=ok.skill, target_id=foe.id, item_id=ok.item, outcome=outcome)
        ]
        if outcome is CombatOutcome.FATAL:
            events.append(PlayerDied(cause=f"冒犯{foe.name}，当场毙命", killer_id=foe.id))
            return events
        return events + _ripple(foe, state, snap)


def _ripple(foe: CharacterView, state: PlayerState, snap: LocalSnapshot) -> list[DomainEvent]:
    """
    人情的涟漪只沿图谱的 HAS_RELATION 边走一跳，且只波及在场目睹之人：
    受害者记恨，与其休戚与共者记恨，其仇家反生好感。
    """
    verdicts: list[tuple[str, Attitude, str]] = [(foe.id, Attitude.HOSTILE, "遭你出手相攻")]
    for other in snap.characters:
        if other.id == foe.id or other.subdued:
            continue
        kind = other.bond_with(foe.id) or foe.bond_with(other.id)
        if kind is RelationKind.ENEMY:
            verdicts.append((other.id, Attitude.FRIENDLY, f"你与其仇家{foe.name}为敌"))
        elif kind is not None:
            verdicts.append((other.id, Attitude.HOSTILE, f"{kind.value}{foe.name}受你攻击"))
    return [
        RelationChanged(character_id=cid, attitude=att, cause=cause)
        for cid, att, cause in verdicts
        if state.attitude_of(cid) is not att
    ]


class TakeRule(Rule):
    def adjudicate(self, intent: PlayerIntent, state: PlayerState, snap: LocalSnapshot) -> Verdict:
        wanted = intent.target_entity or intent.item_used
        if not wanted:
            return Rejection("NO_TARGET", "欲取何物？")
        reachable = [i for i in snap.items if i.holder_id != state.player_id]
        thing = resolve(wanted, reachable, _names)
        if thing is None:
            mine = resolve(wanted, snap.inventory, _names)
            if mine is not None:
                return Rejection("ALREADY_CARRIED", f"「{mine.name}」已在你身上。")
            return Rejection("NOT_PRESENT", f"此处不见「{wanted}」。")
        holder = snap.character(thing.holder_id)
        if holder is not None and not holder.subdued:
            return Rejection("HELD_BY_OTHER", f"「{thing.name}」在{holder.name}手中，须先胜过此人，或待其相赠。")
        return Approval(intent, target=thing.id, source=thing.holder_id)

    def consequences(self, ok: Approval, state: PlayerState, snap: LocalSnapshot) -> list[DomainEvent]:
        assert ok.target and ok.source
        return [ItemTransferred(item_id=ok.target, from_holder=ok.source, to_holder=state.player_id)]


class GiveRule(Rule):
    def adjudicate(self, intent: PlayerIntent, state: PlayerState, snap: LocalSnapshot) -> Verdict:
        who = _present(intent.target_entity, snap)
        if isinstance(who, Rejection):
            return who
        if not intent.item_used:
            return Rejection("NO_ITEM", "须说明赠予何物。")
        thing = resolve(intent.item_used, snap.inventory, _names)
        if thing is None:
            return Rejection("NOT_CARRIED", f"你身上并无「{intent.item_used}」。")
        return Approval(intent, target=who.id, item=thing.id)

    def consequences(self, ok: Approval, state: PlayerState, snap: LocalSnapshot) -> list[DomainEvent]:
        assert ok.target and ok.item
        events: list[DomainEvent] = [
            ItemTransferred(item_id=ok.item, from_holder=state.player_id, to_holder=ok.target)
        ]
        thing = snap.item(ok.item)
        if thing is not None and thing.owner_id == ok.target and state.attitude_of(ok.target) is not Attitude.FRIENDLY:
            events.append(RelationChanged(character_id=ok.target, attitude=Attitude.FRIENDLY, cause="物归原主"))
        return events


class LearnRule(Rule):
    """
    修习的门槛逐条核验，顺序即玩家看到的第一道拦路石：
    可知 → 未会 → 未失传 → 前置武学 → 相冲 → 典籍 → 地点 → 境界 → 传承（自悟凭典籍 / 师传须有肯教之人在场）。
    """

    def adjudicate(self, intent: PlayerIntent, state: PlayerState, snap: LocalSnapshot) -> Verdict:
        wanted = intent.skill_used or intent.target_entity
        if not wanted:
            return Rejection("NO_TARGET", "欲修习何种武学？")
        art = resolve(wanted, snap.skills, _names)
        if art is None:
            return Rejection("UNKNOWN_SKILL", f"此情此景无从得知「{wanted}」的门径。")
        if art.id in state.skills:
            return Rejection("ALREADY_KNOWN", f"你早已通晓{art.name}。")
        p = art.prerequisites
        if p.sealed:
            return Rejection("SEALED", f"{art.name}的门径已不可考，无从修习。")
        if missing := [s for s in p.skills if s not in state.skills]:
            return Rejection("MISSING_SKILL", f"修习{art.name}须先通晓{_list(snap, missing)}。")
        if clash := [s for s in p.conflicts if s in state.skills]:
            return Rejection("CONFLICT", f"{art.name}与你所习{_list(snap, clash)}相冲。")
        if lacking := [i for i in p.items if i not in state.inventory]:
            return Rejection("MISSING_ITEM", f"修习{art.name}须持有{_list(snap, lacking)}。")
        if p.location_id and p.location_id != state.location_id:
            return Rejection("WRONG_PLACE", f"{art.name}须在{snap.label(p.location_id)}方可修习。")
        if player_tier(state, snap).rank < p.min_tier.rank:
            return Rejection("TIER_TOO_LOW", f"根基不足：修习{art.name}须先有{p.min_tier.value}境界。")
        if p.transmission is Transmission.SELF:
            return Approval(intent, skill=art.id, source=p.items[0])
        return self._teacher(intent, art, snap)

    @staticmethod
    def _teacher(intent: PlayerIntent, art: SkillView, snap: LocalSnapshot) -> Verdict:
        masters = [c for c in snap.characters if art.id in c.skill_ids and not c.subdued]
        if not masters:
            return Rejection("NO_TEACHER", f"{art.name}须有通晓此功之人当面传授。")
        willing = [c for c in masters if c.attitude is Attitude.FRIENDLY]
        named = resolve(intent.target_entity, willing, _names) if intent.skill_used else None
        teacher = named or (willing[0] if willing else None)
        if teacher is None:
            return Rejection("UNWILLING", f"{masters[0].name}不肯将{art.name}传给素不相识之人。")
        return Approval(intent, skill=art.id, source=teacher.id, target=teacher.id)

    def consequences(self, ok: Approval, state: PlayerState, snap: LocalSnapshot) -> list[DomainEvent]:
        assert ok.skill and ok.source
        return [SkillLearned(skill_id=ok.skill, source_id=ok.source)]


class InvalidRule(Rule):
    """违背世界观的操作永不获准：热兵器、法术、元游戏指令在这里撞上死线。"""

    def adjudicate(self, intent: PlayerIntent, state: PlayerState, snap: LocalSnapshot) -> Verdict:
        return Rejection("INVALID", intent.reason or "此举不合江湖天道。")

    def consequences(self, ok: Approval, state: PlayerState, snap: LocalSnapshot) -> list[DomainEvent]:
        raise AssertionError("INVALID 永不获准")


RULES: dict[ActionType, Rule] = {
    ActionType.OBSERVE: ObserveRule(),
    ActionType.MOVE: MoveRule(),
    ActionType.TALK: TalkRule(),
    ActionType.ATTACK: AttackRule(),
    ActionType.TAKE: TakeRule(),
    ActionType.GIVE: GiveRule(),
    ActionType.LEARN: LearnRule(),
    ActionType.INVALID: InvalidRule(),
}


# ============================================================
#  门面
# ============================================================
def adjudicate(intent: PlayerIntent, state: PlayerState, snap: LocalSnapshot) -> Verdict:
    return RULES[intent.action_type].adjudicate(intent, state, snap)


def decide(intent: PlayerIntent, state: PlayerState, snap: LocalSnapshot) -> list[DomainEvent]:
    """意图 → 绝对结果。驳回同样入账为 ActionFailed：失败也是历史。"""
    verdict = adjudicate(intent, state, snap)
    if isinstance(verdict, Rejection):
        return [
            ActionFailed(
                action=intent.action_type,
                target=intent.target_entity or intent.skill_used or intent.item_used,
                reason_code=verdict.code,
                reason=verdict.reason,
            )
        ]
    return RULES[intent.action_type].consequences(verdict, state, snap)
