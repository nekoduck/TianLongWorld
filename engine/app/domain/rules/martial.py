"""
[INPUT]: 依赖 rules/base 的 Rule / Approval / Rejection / Verdict / resolve / names / listed / present / menace / skill_tier / player_tier / best_skill，
         依赖 domain/events 的 SkillExecuted / HealthChanged / PlayerDied / RelationChanged / Moved / SkillPracticed / DomainEvent，
         依赖 domain/models 的 Attitude / RelationKind / Tier / Transmission，依赖 domain/combat 的 Stakes / assess / CombatRuling / CombatOutcome，
         依赖 domain/progression 的 FOUNDATION / Guidance / Mastery / Vitality / gain，依赖 domain/intent 的 PlayerIntent，
         依赖 domain/snapshot 的 LocalSnapshot / CharacterView / ExitView / SkillView；PlayerState 仅作类型标注
[OUTPUT]: 对外提供 AttackRule（出手：可裁区间 + 定案 → SkillExecuted / HealthChanged / PlayerDied / 人情涟漪 / 重伤逃脱）、
          LearnRule（修习：入门走获取要求、精进走修炼要求）、retreat()（重伤逃脱的去处）、required_regard()（求教某功须有的人情）
[POS]: rules 包里管"武"的两条：出手与修习。
       出手的胜负由 combat.assess 圈出可裁区间、地下城主在区间里挑；定案为重伤时追加沿来路退回的 Moved（fleeing），逃离过的险地永不作退路；
       此情此景里根本没有的武功名（自拟招式）只是笔墨，照常以看家本领出手。人情涟漪沿 HAS_RELATION 一跳、只波及在场目睹者，
       「敌人之敌 → 好感」暂停（蓝图的仇敌边多是后文才结下的；阶段 B 的 reputation 只认 era=开篇 的羁绊，在那个范围内恢复）。
       修习：根基逐条核验（作根基之功的火候、相冲、境界、伤势）；求教的门槛按武学境界比人情 rank——三流（及不入流）须友善，二流及以上须信赖；
       点名的师父不肯即拒绝而不悄悄改成苦练，拒绝理由照实写人情（敌视写明结怨缘由、戒备写提防、漠然是素无交情、友善而功高是交情尚浅）；
       一切门槛都过了，敌视者却在侧且行动自如：UNSAFE（与调息同一道门）
[PROTOCOL]: 变更时更新此头部，然后检查 rules/CLAUDE.md
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.domain.combat import CombatOutcome, CombatRuling, Stakes, assess
from app.domain.events import (
    DomainEvent,
    HealthChanged,
    Moved,
    PlayerDied,
    RelationChanged,
    SkillExecuted,
    SkillPracticed,
)
from app.domain.intent import PlayerIntent
from app.domain.models import Attitude, RelationKind, Tier, Transmission
from app.domain.progression import FOUNDATION, Guidance, Mastery, Vitality, gain
from app.domain.rules.base import (
    Approval,
    Rejection,
    Rule,
    Verdict,
    best_skill,
    listed,
    menace,
    names,
    player_tier,
    present,
    resolve,
    skill_tier,
)
from app.domain.snapshot import CharacterView, ExitView, LocalSnapshot, SkillView

if TYPE_CHECKING:
    from app.domain.aggregates import PlayerState


# ============================================================
#  出手
# ============================================================
class AttackRule(Rule):
    def adjudicate(self, intent: PlayerIntent, state: PlayerState, snap: LocalSnapshot) -> Verdict:
        who = present(intent.target_entity, snap)
        if isinstance(who, Rejection):
            return who
        if who.subdued:
            return Rejection("ALREADY_SUBDUED", f"{who.name}已被你制住，无还手之力。")
        skill = best_skill(state, snap)
        if intent.skill_used and (named := resolve(intent.skill_used, snap.skills, names)) is not None:
            if named.id not in state.skills:
                return Rejection("NOT_KNOWN", f"你并不会「{named.name}」。")
            skill = named
        # 此情此景里根本没有的武功名（「黑虎掏心」）只是玩家的笔墨：照常以看家本领出手，而不是白白驳回一回合
        item = None
        if intent.item_used:
            item = resolve(intent.item_used, snap.inventory, names)
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
        events += _ripple(foe, state, snap)
        if ruling.outcome is CombatOutcome.SEVERE_WOUND and (way := retreat(state, snap)) is not None:
            events.append(
                Moved(from_location_id=state.location_id, to_location_id=way.to_id, exit_label=way.label, fleeing=True)
            )
        return events


def retreat(state: PlayerState, snap: LocalSnapshot) -> ExitView | None:
    """
    重伤逃脱是真的逃：沿来路退回；投胎之地或来路已断，就走第一条出路（快照里出路恒按固定键排序）。
    逃离过的险地不是退路——那里的仇人不会挪窝，连败两场不能把人送回第一场的仇家面前；条条出路都通向险地，才留在原地。
    """
    ways = [e for e in snap.exits if e.to_id not in state.fled_from]
    return next((e for e in ways if e.to_id == state.came_from), ways[0] if ways else None)


def _ripple(foe: CharacterView, state: PlayerState, snap: LocalSnapshot) -> list[DomainEvent]:
    """
    人情的涟漪只沿图谱的 HAS_RELATION 边走一跳，且只波及在场目睹之人：
    受害者记恨，与其休戚与共者记恨；其仇家暂不因此生好感（见下）。
    """
    verdicts: list[tuple[str, Attitude, str]] = [(foe.id, Attitude.HOSTILE, "遭你出手相攻")]
    for other in snap.characters:
        if other.id == foe.id or other.subdued:
            continue
        kind = other.bond_with(foe.id) or foe.bond_with(other.id)
        if kind is RelationKind.ENEMY:
            # 「敌人之敌 → 好感」暂停：蓝图的仇敌边多半是后文才结下的（龚光杰掌掴段誉、干光豪追杀段誉），
            # 沿它翻转态度就是让 T=0 的人提前记起未来的恩怨。阶段 B 的 reputation.py 只认 era=开篇 的羁绊，届时在那个范围内恢复
            continue
        if kind is not None:
            verdicts.append((other.id, Attitude.HOSTILE, f"{kind.value}{foe.name}受你攻击"))
    return [
        RelationChanged(character_id=cid, attitude=att, cause=cause)
        for cid, att, cause in verdicts
        if state.attitude_of(cid) is not att
    ]


# ============================================================
#  修习
# ============================================================
class LearnRule(Rule):
    """
    修习分两段，由玩家此刻的火候决定走哪一段——入门前求门径，入门后求精进，同一个动作、同一种事件（SkillPracticed）：
      入门：可知 → 未失传 → 根基（修炼要求）→ 典籍 → 地点 → 传承（自悟凭典籍 / 师传须有肯教之人在场）；
      精进：未臻化境 → 根基（修炼要求）→ 凭什么练（点名的师父须肯教；肯教之人在场即名师点拨，自悟之功的典籍在身即参照典籍，否则闭门苦练）。
    根基（修炼要求）逐条核验：作根基的武学须练到略有小成 → 无相冲 → 境界够 → 未受重伤。
    一切门槛都过了，敌视你的人却在一旁且行动自如：无从静心修习（UNSAFE，与调息同一道门）。
    """

    def adjudicate(self, intent: PlayerIntent, state: PlayerState, snap: LocalSnapshot) -> Verdict:
        wanted = intent.skill_used or intent.target_entity
        if not wanted:
            return Rejection("NO_TARGET", "欲修习何种武学？")
        art = resolve(wanted, snap.skills, names)
        if art is None:
            return Rejection("UNKNOWN_SKILL", f"此情此景无从得知「{wanted}」的门径。")
        verdict = self._deepen(intent, art, state, snap) if art.id in state.skills else self._enter(intent, art, state, snap)
        if isinstance(verdict, Approval) and (foe := menace(snap)) is not None:
            # 与调息同理：仇人在侧无从静心参悟。放在其余门槛之后——向仇人本人求教，驳回理由说的是那段恩怨
            return Rejection("UNSAFE", f"{foe.name}在侧虎视眈眈，你无法静心修习。")
        return verdict

    @staticmethod
    def _foundation(art: SkillView, state: PlayerState, snap: LocalSnapshot) -> Rejection | None:
        p = art.practice
        shallow = [s for s in p.skills if (m := state.mastery(s)) is None or m.rank < FOUNDATION.rank]
        if shallow:
            return Rejection("MISSING_SKILL", f"修习{art.name}须先将{listed(snap, shallow)}练到{FOUNDATION.value}。")
        if clash := [s for s in p.conflicts if s in state.skills]:
            return Rejection("CONFLICT", f"{art.name}与你所习{listed(snap, clash)}相冲。")
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
            return Rejection("MISSING_ITEM", f"修习{art.name}须持有{listed(snap, lacking)}。")
        if a.location_id and a.location_id != state.location_id:
            return Rejection("WRONG_PLACE", f"{art.name}须在{snap.label(a.location_id)}方得门径。")
        if a.transmission is Transmission.SELF:
            return Approval(intent, skill=art.id, source=a.items[0], guidance=Guidance.ENTRY)
        teacher = _teacher(intent, art, state, snap)
        if isinstance(teacher, Rejection):
            return teacher
        return Approval(intent, skill=art.id, source=teacher.id, target=teacher.id, guidance=Guidance.ENTRY)

    def _deepen(self, intent: PlayerIntent, art: SkillView, state: PlayerState, snap: LocalSnapshot) -> Verdict:
        if state.mastery(art.id) is Mastery.PEAK:
            return Rejection("PEAK", f"你的{art.name}已臻{Mastery.PEAK.value}之境，再练也无可精进。")
        if weak := self._foundation(art, state, snap):
            return weak
        teacher = _teacher(intent, art, state, snap)
        if isinstance(teacher, CharacterView):
            return Approval(intent, skill=art.id, source=teacher.id, target=teacher.id, guidance=Guidance.TEACHER)
        if teacher.code == "UNWILLING" and _named_master(intent, art, snap) is not None:
            return teacher  # 点名求教而对方不肯：拒绝你的是你求的那个人，不悄悄改成闭门苦练
        a = art.acquisition
        if a.transmission is Transmission.SELF and a.items and all(i in state.inventory for i in a.items):
            return Approval(intent, skill=art.id, source=a.items[0], guidance=Guidance.MANUAL)
        return Approval(intent, skill=art.id, guidance=Guidance.ALONE)

    def consequences(
        self, ok: Approval, state: PlayerState, snap: LocalSnapshot, ruling: CombatRuling | None
    ) -> list[DomainEvent]:
        assert ok.skill and ok.guidance
        art = snap.skill(ok.skill)
        assert art is not None
        return [SkillPracticed(skill_id=ok.skill, proficiency_gained=gain(ok.guidance, art.tier), source_id=ok.source)]


def required_regard(art: SkillView) -> Attitude:
    """求教的门槛：三流（及不入流）的功夫，友善之人便肯教；二流及以上，非信赖之人不传。"""
    return Attitude.FRIENDLY if art.tier.rank <= Tier.THIRD.rank else Attitude.TRUSTED


def _masters(art: SkillView, snap: LocalSnapshot) -> list[CharacterView]:
    return [c for c in snap.characters if art.id in c.skill_ids and not c.subdued]


def _named_master(intent: PlayerIntent, art: SkillView, snap: LocalSnapshot) -> CharacterView | None:
    return resolve(intent.target_entity, _masters(art, snap), names) if intent.skill_used else None


def _teacher(intent: PlayerIntent, art: SkillView, state: PlayerState, snap: LocalSnapshot) -> CharacterView | Rejection:
    """
    在场且通晓此功、肯教你的人（人情 rank 够得上 required_regard）。玩家点名的师父优先：被拒时说的是他，肯教时也是他；他不肯而旁人肯，由肯教的人传；
    没点名而人人不肯时，驳回说的是交情最深的那位——离肯教最近的人。
    驳回理由照实写人情：敌视者写明结怨的缘由（PlayerState.attitude_causes），戒备者是提防，漠然者只是素无交情——你们也许刚说过话，并非素不相识；
    友善而功高者是交情尚浅。unlock 写明要到哪一档人情才肯传。
    """
    masters = _masters(art, snap)
    if not masters:
        return Rejection("NO_TEACHER", f"{art.name}须有通晓此功之人当面传授。")
    named = _named_master(intent, art, snap)
    candidates = sorted(masters, key=lambda c: (c is not named, -c.attitude.rank))  # 点名者优先，其次交情最深者
    need = required_regard(art)
    teacher = next((c for c in candidates if c.attitude.rank >= need.rank), None)
    if teacher is not None:
        return teacher
    who = candidates[0]
    match who.attitude:
        case Attitude.HOSTILE:
            grudge = state.attitude_causes.get(who.id) or "与你结怨"
            reason = f"{who.name}不肯将{art.name}传给仇人（{grudge}）。"
        case Attitude.WARY:
            reason = f"{who.name}对你心存戒备，不肯将{art.name}相传。"
        case Attitude.NEUTRAL:
            reason = f"{who.name}不肯将{art.name}传给素无交情之人。"
        case _:
            reason = f"{who.name}与你交情尚浅，{art.name}非信赖之人不传。"
    return Rejection("UNWILLING", reason, unlock=need.value)
