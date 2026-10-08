"""
[INPUT]: 依赖 domain/intent 的 ActionType / PlayerIntent，依赖 domain/events 的领域事件，依赖 domain/models 的 Tier / Attitude /
         RelationKind / Transmission，依赖 domain/combat 的 Stakes / assess / settle / CombatProposal / CombatRuling / CombatOutcome，
         依赖 domain/progression 的火候与伤势（FOUNDATION / GAIN / Guidance / Mastery / Vitality / MAX_HP / REST_GAIN / effective_tier），
         依赖 domain/snapshot 的 LocalSnapshot 及视图；PlayerState 仅作类型标注（避免与 aggregates 成环）
[OUTPUT]: 对外提供 Rejection / Approval 裁决结果、adjudicate()（合法性：指称落地 + 物理/逻辑双重校验）、stakes()（胜负未定之事的可裁区间）、
          decide()（合法性 + 定案 → 事件）、player_tier()（火候折算后的玩家境界）、best_skill()、resolve()（名称 → 实体的唯一匹配）
[POS]: domain 的裁决核心（Decider）：纯函数，不做 IO、不调大模型、不看时钟。
       物理校验看快照（出口是否相连、人是否在场、物在谁手），逻辑校验看玩家状态与本体（火候、根基、门径、谁肯传授、伤势）；
       能力成长、物品获取、人际变化只能由此处从图谱拓扑推导得出。胜负未定之事（出手）由 Rule.stakes 圈出可裁区间，
       地下城主的提议经 combat.settle 钳进区间后才成为事件——大模型在这里有一票，但只能投给区间里的候选。
       每种动作一条 Rule（开闭：新动作 = 新 Rule + 注册一行；新的模糊动作 = 覆写 stakes 钩子），options 生成器复用 adjudicate 过滤出合法行为
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.domain.combat import CombatOutcome, CombatProposal, CombatRuling, Stakes, assess, settle
from app.domain.events import (
    ActionFailed,
    Conversed,
    DomainEvent,
    HealthChanged,
    ItemTransferred,
    Moved,
    PlayerDied,
    RelationChanged,
    SkillExecuted,
    SkillPracticed,
)
from app.domain.intent import ActionType, PlayerIntent
from app.domain.models import Attitude, RelationKind, Tier, Transmission
from app.domain.progression import (
    FOUNDATION,
    GAIN,
    MAX_HP,
    REST_GAIN,
    Guidance,
    Mastery,
    Vitality,
    effective_tier,
)
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
    source: str | None = None  # TAKE：物品原持有者；LEARN：传功点拨之人或所凭典籍（闭门苦练为 None）
    guidance: Guidance | None = None  # LEARN：入门，或精进凭的是什么


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
#  境界 —— 一门功夫拿得出几分本事，看火候：境界上限是功夫的，折扣是自己的
# ============================================================
def skill_tier(view: SkillView, state: PlayerState) -> Tier:
    mastery = state.mastery(view.id)
    return effective_tier(view.tier, mastery) if mastery is not None else Tier.NONE


def player_tier(state: PlayerState, snap: LocalSnapshot) -> Tier:
    tiers = [skill_tier(view, state) for view in snap.skills if view.id in state.skills]
    return max(tiers, key=lambda t: t.rank, default=Tier.NONE)


def best_skill(state: PlayerState, snap: LocalSnapshot) -> SkillView | None:
    def strength(s: SkillView) -> tuple[int, int, str]:
        mastery = state.mastery(s.id)
        return -skill_tier(s, state).rank, -(mastery.rank if mastery else -1), s.id

    known = sorted((s for s in snap.skills if s.id in state.skills), key=strength)
    return known[0] if known else None


# ============================================================
#  规则
# ============================================================
class Rule(ABC):
    @abstractmethod
    def adjudicate(self, intent: PlayerIntent, state: PlayerState, snap: LocalSnapshot) -> Verdict: ...

    def stakes(self, ok: Approval, state: PlayerState, snap: LocalSnapshot) -> Stakes | None:
        """胜负未定之事的可裁区间；结果确定的动作没有赌注（缺省）。"""
        return None

    @abstractmethod
    def consequences(
        self, ok: Approval, state: PlayerState, snap: LocalSnapshot, ruling: CombatRuling | None
    ) -> list[DomainEvent]: ...


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

    def consequences(
        self, ok: Approval, state: PlayerState, snap: LocalSnapshot, ruling: CombatRuling | None
    ) -> list[DomainEvent]:
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

    def consequences(
        self, ok: Approval, state: PlayerState, snap: LocalSnapshot, ruling: CombatRuling | None
    ) -> list[DomainEvent]:
        assert ok.target and ok.exit_label
        return [Moved(from_location_id=state.location_id, to_location_id=ok.target, exit_label=ok.exit_label)]


class TalkRule(Rule):
    def adjudicate(self, intent: PlayerIntent, state: PlayerState, snap: LocalSnapshot) -> Verdict:
        who = _present(intent.target_entity, snap)
        return who if isinstance(who, Rejection) else Approval(intent, target=who.id)

    def consequences(
        self, ok: Approval, state: PlayerState, snap: LocalSnapshot, ruling: CombatRuling | None
    ) -> list[DomainEvent]:
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

    def stakes(self, ok: Approval, state: PlayerState, snap: LocalSnapshot) -> Stakes:
        """出手的胜负不再一锤定音：境界差（火候折算后）、对方性情与自身伤势圈出可裁区间，交给地下城主在区间里定夺。"""
        assert ok.target
        foe = snap.character(ok.target)
        assert foe is not None
        art = snap.skill(ok.skill) if ok.skill else None
        return assess(
            defender_id=foe.id, skill_id=ok.skill, item_id=ok.item,
            attacker=skill_tier(art, state) if art else Tier.NONE, defender=foe.tier,
            disposition=foe.disposition, player_hp=state.hp,
        )

    def consequences(
        self, ok: Approval, state: PlayerState, snap: LocalSnapshot, ruling: CombatRuling | None
    ) -> list[DomainEvent]:
        assert ok.target and ruling is not None
        foe = snap.character(ok.target)
        assert foe is not None
        events: list[DomainEvent] = [
            SkillExecuted(skill_id=ok.skill, target_id=foe.id, item_id=ok.item, outcome=ruling.outcome)
        ]
        if ruling.hp_change:
            events.append(HealthChanged(delta=ruling.hp_change, cause=f"与{foe.name}交手", source_id=foe.id))
        if ruling.outcome is CombatOutcome.DEATH:
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

    def consequences(
        self, ok: Approval, state: PlayerState, snap: LocalSnapshot, ruling: CombatRuling | None
    ) -> list[DomainEvent]:
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

    def consequences(
        self, ok: Approval, state: PlayerState, snap: LocalSnapshot, ruling: CombatRuling | None
    ) -> list[DomainEvent]:
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
    修习分两段，由玩家此刻的火候决定走哪一段——入门前求门径，入门后求精进，同一个动作、同一种事件（SkillPracticed）：
      入门：可知 → 未失传 → 根基（修炼要求）→ 典籍 → 地点 → 传承（自悟凭典籍 / 师传须有肯教之人在场）；
      精进：未臻化境 → 根基（修炼要求）→ 凭什么练（点名的师父须肯教；肯教之人在场即名师点拨，自悟之功的典籍在身即参照典籍，否则闭门苦练）。
    根基（修炼要求）逐条核验：作根基的武学须练到略有小成 → 无相冲 → 境界够 → 未受重伤。
    """

    def adjudicate(self, intent: PlayerIntent, state: PlayerState, snap: LocalSnapshot) -> Verdict:
        wanted = intent.skill_used or intent.target_entity
        if not wanted:
            return Rejection("NO_TARGET", "欲修习何种武学？")
        art = resolve(wanted, snap.skills, _names)
        if art is None:
            return Rejection("UNKNOWN_SKILL", f"此情此景无从得知「{wanted}」的门径。")
        if art.id in state.skills:
            return self._deepen(intent, art, state, snap)
        return self._enter(intent, art, state, snap)

    @staticmethod
    def _foundation(art: SkillView, state: PlayerState, snap: LocalSnapshot) -> Rejection | None:
        p = art.practice
        shallow = [s for s in p.skills if (m := state.mastery(s)) is None or m.rank < FOUNDATION.rank]
        if shallow:
            return Rejection("MISSING_SKILL", f"修习{art.name}须先将{_list(snap, shallow)}练到{FOUNDATION.value}。")
        if clash := [s for s in p.conflicts if s in state.skills]:
            return Rejection("CONFLICT", f"{art.name}与你所习{_list(snap, clash)}相冲。")
        if player_tier(state, snap).rank < p.min_tier.rank:
            return Rejection("TIER_TOO_LOW", f"根基不足：修习{art.name}须先有{p.min_tier.value}境界。")
        if state.vitality.rank >= Vitality.WOUNDED.rank:
            return Rejection("WOUNDED", f"你{state.vitality.value}在身，强行运功恐走火入魔，须先调息疗伤。")
        return None

    def _enter(self, intent: PlayerIntent, art: SkillView, state: PlayerState, snap: LocalSnapshot) -> Verdict:
        a = art.acquisition
        if a.sealed:
            return Rejection("SEALED", f"{art.name}的门径已不可考，无从修习。")
        if weak := self._foundation(art, state, snap):
            return weak
        if lacking := [i for i in a.items if i not in state.inventory]:
            return Rejection("MISSING_ITEM", f"修习{art.name}须持有{_list(snap, lacking)}。")
        if a.location_id and a.location_id != state.location_id:
            return Rejection("WRONG_PLACE", f"{art.name}须在{snap.label(a.location_id)}方得门径。")
        if a.transmission is Transmission.SELF:
            return Approval(intent, skill=art.id, source=a.items[0], guidance=Guidance.ENTRY)
        teacher = _teacher(intent, art, snap)
        if isinstance(teacher, Rejection):
            return teacher
        return Approval(intent, skill=art.id, source=teacher.id, target=teacher.id, guidance=Guidance.ENTRY)

    def _deepen(self, intent: PlayerIntent, art: SkillView, state: PlayerState, snap: LocalSnapshot) -> Verdict:
        if state.mastery(art.id) is Mastery.PEAK:
            return Rejection("PEAK", f"你的{art.name}已臻{Mastery.PEAK.value}之境，再练也无可精进。")
        if weak := self._foundation(art, state, snap):
            return weak
        teacher = _teacher(intent, art, snap)
        if isinstance(teacher, CharacterView):
            return Approval(intent, skill=art.id, source=teacher.id, target=teacher.id, guidance=Guidance.TEACHER)
        if teacher.code == "UNWILLING" and _named_master(intent, art, snap) is not None:
            return teacher  # 点名求教而对方不肯：拒绝你的是你求的那个人，不悄悄改成闭门苦练
        a = art.acquisition
        if a.transmission is Transmission.SELF and all(i in state.inventory for i in a.items):
            return Approval(intent, skill=art.id, source=a.items[0], guidance=Guidance.MANUAL)
        return Approval(intent, skill=art.id, guidance=Guidance.ALONE)

    def consequences(
        self, ok: Approval, state: PlayerState, snap: LocalSnapshot, ruling: CombatRuling | None
    ) -> list[DomainEvent]:
        assert ok.skill and ok.guidance
        return [SkillPracticed(skill_id=ok.skill, proficiency_gained=GAIN[ok.guidance], source_id=ok.source)]


def _masters(art: SkillView, snap: LocalSnapshot) -> list[CharacterView]:
    return [c for c in snap.characters if art.id in c.skill_ids and not c.subdued]


def _named_master(intent: PlayerIntent, art: SkillView, snap: LocalSnapshot) -> CharacterView | None:
    return resolve(intent.target_entity, _masters(art, snap), _names) if intent.skill_used else None


def _teacher(intent: PlayerIntent, art: SkillView, snap: LocalSnapshot) -> CharacterView | Rejection:
    """在场且通晓此功、肯教你的人。玩家点名的师父优先：被拒时说的是他，肯教时也是他；他不肯而旁人肯，由肯教的人传。"""
    masters = _masters(art, snap)
    if not masters:
        return Rejection("NO_TEACHER", f"{art.name}须有通晓此功之人当面传授。")
    named = _named_master(intent, art, snap)
    candidates = sorted(masters, key=lambda c: c is not named)
    teacher = next((c for c in candidates if c.attitude is Attitude.FRIENDLY), None)
    if teacher is None:
        return Rejection("UNWILLING", f"{candidates[0].name}不肯将{art.name}传给素不相识之人。")
    return teacher


class RestRule(Rule):
    """调息疗伤：气血不满才有伤可疗；敌视你的人就在一旁且行动自如时，没人能安心闭目运气。"""

    def adjudicate(self, intent: PlayerIntent, state: PlayerState, snap: LocalSnapshot) -> Verdict:
        if state.hp >= MAX_HP:
            return Rejection("UNHURT", "你气血充盈，无伤可疗。")
        if foe := next((c for c in snap.characters if c.attitude is Attitude.HOSTILE and not c.subdued), None):
            return Rejection("UNSAFE", f"{foe.name}在侧虎视眈眈，你无法安心调息。")
        return Approval(intent)

    def consequences(
        self, ok: Approval, state: PlayerState, snap: LocalSnapshot, ruling: CombatRuling | None
    ) -> list[DomainEvent]:
        return [HealthChanged(delta=min(REST_GAIN, MAX_HP - state.hp), cause="调息疗伤")]


class InvalidRule(Rule):
    """违背世界观的操作永不获准：热兵器、法术、元游戏指令在这里撞上死线。"""

    def adjudicate(self, intent: PlayerIntent, state: PlayerState, snap: LocalSnapshot) -> Verdict:
        return Rejection("INVALID", intent.reason or "此举不合江湖天道。")

    def consequences(
        self, ok: Approval, state: PlayerState, snap: LocalSnapshot, ruling: CombatRuling | None
    ) -> list[DomainEvent]:
        raise AssertionError("INVALID 永不获准")


RULES: dict[ActionType, Rule] = {
    ActionType.OBSERVE: ObserveRule(),
    ActionType.MOVE: MoveRule(),
    ActionType.TALK: TalkRule(),
    ActionType.ATTACK: AttackRule(),
    ActionType.TAKE: TakeRule(),
    ActionType.GIVE: GiveRule(),
    ActionType.LEARN: LearnRule(),
    ActionType.REST: RestRule(),
    ActionType.INVALID: InvalidRule(),
}


# ============================================================
#  门面
# ============================================================
def adjudicate(intent: PlayerIntent, state: PlayerState, snap: LocalSnapshot) -> Verdict:
    return RULES[intent.action_type].adjudicate(intent, state, snap)


def stakes(intent: PlayerIntent, state: PlayerState, snap: LocalSnapshot) -> Stakes | None:
    """这一举动若获准，胜负是否未定、可裁区间几何。驳回的举动与结果确定的举动都没有赌注。"""
    verdict = adjudicate(intent, state, snap)
    if isinstance(verdict, Rejection):
        return None
    return RULES[intent.action_type].stakes(verdict, state, snap)


def decide(
    intent: PlayerIntent, state: PlayerState, snap: LocalSnapshot, proposal: CombatProposal | None = None
) -> list[DomainEvent]:
    """
    意图 → 定案 → 事件。驳回同样入账为 ActionFailed：失败也是历史。
    胜负未定之事，地下城主的提议（proposal）经 settle 钳进可裁区间；没有提议即取区间里的确定性裁决——离线也能玩。
    """
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
    rule = RULES[intent.action_type]
    at_stake = rule.stakes(verdict, state, snap)
    ruling = settle(at_stake, proposal) if at_stake is not None else None
    return rule.consequences(verdict, state, snap, ruling)
