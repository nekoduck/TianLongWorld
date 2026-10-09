"""
[INPUT]: 依赖 rules/base 的 Rule / Approval / Rejection / Verdict / resolve / names / listed / present / menace / skill_tier / player_tier / best_skill / fighting_tier，
         依赖 rules/parley 的 parley / required_regard（求艺走交涉），依赖 domain/reputation 的 ripple（人情涟漪），
         依赖 domain/events 的 SkillExecuted / HealthChanged / PlayerDied / Moved / SkillPracticed / ItemTransferred / DomainEvent，
         依赖 domain/models 的 Attitude / Tier / Transmission，依赖 domain/combat 的 Stakes / assess / CombatRuling / CombatOutcome，
         依赖 domain/stakes 的 AnyStakes / Ruling，依赖 domain/approach 的 Route，
         依赖 domain/progression 的 FOUNDATION / Guidance / Mastery / Vitality / gain，依赖 domain/intent 的 PlayerIntent / Approach / Aim，
         依赖 domain/snapshot 的 LocalSnapshot / CharacterView / ExitView / SkillView；PlayerState 仅作类型标注
[OUTPUT]: 对外提供 AttackRule（出手：可裁区间 + 定案 → SkillExecuted（带手段）/ HealthChanged / PlayerDied / 人情涟漪 / 重伤逃脱）、
          strike_stakes() / strike()（出手与夺物共用的区间与定案）、LearnRule（修习：入门走获取要求、精进走修炼要求；
          以言辞 / 人情求教而师父不肯 → 交涉·求艺）、retreat()（重伤逃脱的去处）、required_regard()（求教某功须有的人情）
[POS]: rules 包里管"武"的两条：出手与修习。
       出手的胜负由 combat.assess 圈出可裁区间（对方此世带伤即境界折一档：狭路相逢的落败者一日之内好欺负）、地下城主在区间里挑；定案为重伤时追加夺路而逃的 Moved（fleeing）：先走来路，
       其次去处没有仇人（hostile_ahead=False）的出路，逃离过的险地永不作退路，条条不通才留在原地。计谋出手同样不越级（区间不变，只记手段）。
       夺物（TAKE×武力）与出手同一个区间：得手即追加易手。此情此景里根本没有的武功名（自拟招式）只是笔墨，照常以看家本领出手。
       人情涟漪交给 reputation（只认开篇羁绊、恢复「敌人之敌」、缘由写明称谓）。
       修习：根基逐条核验（作根基之功的火候、相冲、境界、伤势）；求教的门槛按武学境界比人情 rank——三流（及不入流）须友善，二流及以上须信赖；
       点名的师父不肯即拒绝而不悄悄改成苦练，拒绝理由照实写人情；以言辞 / 人情相求而师父不肯，不再落为驳回，改走交涉（所图求艺）：
       产出 Parleyed（可附人情 +1），从不传功——下一回合人情够了再求，才走修习的两道门。
       一切门槛都过了，敌视者却在侧且行动自如：UNSAFE（与调息同一道门）；交涉不受此限（说话不必静心）
[PROTOCOL]: 变更时更新此头部，然后检查 rules/CLAUDE.md
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.domain.approach import Route
from app.domain.combat import CombatOutcome, CombatRuling, Stakes, assess
from app.domain.events import (
    DomainEvent,
    HealthChanged,
    ItemTransferred,
    Moved,
    PlayerDied,
    SkillExecuted,
    SkillPracticed,
)
from app.domain.intent import Aim, Approach, PlayerIntent
from app.domain.models import Attitude, Tier, Transmission
from app.domain.progression import FOUNDATION, Guidance, Mastery, Vitality, gain
from app.domain.reputation import ripple
from app.domain.rules.base import (
    Approval,
    Rejection,
    Rule,
    Verdict,
    best_skill,
    fighting_tier,
    listed,
    menace,
    names,
    player_tier,
    present,
    resolve,
    skill_tier,
)
from app.domain.rules.parley import parley, required_regard
from app.domain.snapshot import CharacterView, ExitView, LocalSnapshot, SkillView
from app.domain.stakes import AnyStakes, Ruling

if TYPE_CHECKING:
    from app.domain.aggregates import PlayerState

_ASKING = frozenset({Approach.WORDS, Approach.FAVOR})  # 以言辞 / 人情求教：师父不肯即改走交涉


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
        return Approval(intent, target=who.id, skill=skill.id if skill else None, item=item.id if item else None,
                        route=Route.COMBAT, aim=intent.aim or Aim.SUBDUE)

    def stakes(self, ok: Approval, state: PlayerState, snap: LocalSnapshot) -> Stakes:
        return strike_stakes(ok, state, snap)

    def consequences(
        self, ok: Approval, state: PlayerState, snap: LocalSnapshot, ruling: Ruling | None
    ) -> list[DomainEvent]:
        assert isinstance(ruling, CombatRuling)
        return strike(ok, state, snap, ruling)


def strike_stakes(ok: Approval, state: PlayerState, snap: LocalSnapshot, seize: str | None = None) -> Stakes:
    """
    出手的胜负不再一锤定音：境界差（火候折算后；对方此世带伤折一档）、对方性情与自身伤势圈出可裁区间，交给地下城主在区间里定夺。夺物同一个区间。
    """
    assert ok.target
    foe = snap.character(ok.target)
    assert foe is not None
    art = snap.skill(ok.skill) if ok.skill else None
    return assess(
        defender_id=foe.id, skill_id=ok.skill, item_id=None if seize else ok.item,
        attacker=skill_tier(art, state) if art else Tier.NONE, defender=fighting_tier(foe),
        disposition=foe.disposition, player_hp=state.hp, seize_id=seize,
    )


def strike(
    ok: Approval, state: PlayerState, snap: LocalSnapshot, ruling: CombatRuling, seize: str | None = None
) -> list[DomainEvent]:
    """定案 → SkillExecuted（带手段）+ 气血 [+ 身死] / 夺物得手的易手 / 人情涟漪 / 重伤夺路而逃。"""
    assert ok.target
    foe = snap.character(ok.target)
    assert foe is not None
    events: list[DomainEvent] = [SkillExecuted(
        skill_id=ok.skill, target_id=foe.id, item_id=None if seize else ok.item, outcome=ruling.outcome,
        approach=ok.intent.approach,
    )]
    if ruling.hp_change:
        events.append(HealthChanged(delta=ruling.hp_change, cause=f"与{foe.name}交手", source_id=foe.id))
    if ruling.outcome is CombatOutcome.DEATH:
        events.append(PlayerDied(cause=f"冒犯{foe.name}，当场毙命", killer_id=foe.id))
        return events
    if seize and ruling.outcome is CombatOutcome.SUCCESS:
        events.append(ItemTransferred(item_id=seize, from_holder=foe.id, to_holder=state.player_id))
    events += ripple(foe, state, snap)
    if ruling.outcome is CombatOutcome.SEVERE_WOUND and (way := retreat(state, snap)) is not None:
        events.append(
            Moved(from_location_id=state.location_id, to_location_id=way.to_id, exit_label=way.label, fleeing=True)
        )
    return events


def retreat(state: PlayerState, snap: LocalSnapshot) -> ExitView | None:
    """
    重伤逃脱是真的逃：先沿来路退回；来路已断或那头站着仇人，就走去处没有仇人的出路（快照里出路恒按固定键排序）。
    逃离过的险地不是退路——那里的仇人不会挪窝；去处站着仇人的出路也不是。条条出路都通向险地，才留在原地。
    """
    ways = [e for e in snap.exits if e.to_id not in state.fled_from and not e.hostile_ahead]
    return next((e for e in ways if e.to_id == state.came_from), ways[0] if ways else None)


# ============================================================
#  修习
# ============================================================
class LearnRule(Rule):
    """
    修习分两段，由玩家此刻的火候决定走哪一段——入门前求门径，入门后求精进，同一个动作、同一种事件（SkillPracticed）：
      入门：可知 → 未失传 → 根基（修炼要求）→ 典籍 → 地点 → 传承（自悟凭典籍 / 师传须有肯教之人在场）；
      精进：未臻化境 → 根基（修炼要求）→ 凭什么练（点名的师父须肯教；肯教之人在场即名师点拨，自悟之功的典籍在身即参照典籍，否则闭门苦练）。
    根基（修炼要求）逐条核验：作根基的武学须练到略有小成 → 无相冲 → 境界够 → 未受重伤。
    以言辞 / 人情相求而师父不肯（UNWILLING）：改走交涉（所图求艺），由交情最深或点名的那位师父裁量——不传功，只可能松口。
    一切门槛都过了，敌视你的人却在一旁且行动自如：无从静心修习（UNSAFE，与调息同一道门）；交涉不受此限。
    """

    def adjudicate(self, intent: PlayerIntent, state: PlayerState, snap: LocalSnapshot) -> Verdict:
        wanted = intent.skill_used or intent.target_entity
        if not wanted:
            return Rejection("NO_TARGET", "欲修习何种武学？")
        art = resolve(wanted, snap.skills, names)
        if art is None:
            return Rejection("UNKNOWN_SKILL", f"此情此景无从得知「{wanted}」的门径。")
        verdict = self._deepen(intent, art, state, snap) if art.id in state.skills else self._enter(intent, art, state, snap)
        if isinstance(verdict, Approval) and verdict.route is Route.FIXED and (foe := menace(snap)) is not None:
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
        teacher, refusal = _teacher(intent, art, state, snap)
        if refusal is None:
            assert teacher is not None
            return Approval(intent, skill=art.id, source=teacher.id, target=teacher.id, guidance=Guidance.ENTRY)
        return _plead(intent, art, teacher, refusal)

    def _deepen(self, intent: PlayerIntent, art: SkillView, state: PlayerState, snap: LocalSnapshot) -> Verdict:
        if state.mastery(art.id) is Mastery.PEAK:
            return Rejection("PEAK", f"你的{art.name}已臻{Mastery.PEAK.value}之境，再练也无可精进。")
        if weak := self._foundation(art, state, snap):
            return weak
        teacher, refusal = _teacher(intent, art, state, snap)
        if refusal is None:
            assert teacher is not None
            return Approval(intent, skill=art.id, source=teacher.id, target=teacher.id, guidance=Guidance.TEACHER)
        if refusal.code == "UNWILLING" and (_named_master(intent, art, snap) is not None or intent.approach in _ASKING):
            return _plead(intent, art, teacher, refusal)  # 点名求教或开口相求而对方不肯：拒绝你的是那个人，不悄悄改成闭门苦练
        a = art.acquisition
        if a.transmission is Transmission.SELF and a.items and all(i in state.inventory for i in a.items):
            return Approval(intent, skill=art.id, source=a.items[0], guidance=Guidance.MANUAL)
        return Approval(intent, skill=art.id, guidance=Guidance.ALONE)

    def stakes(self, ok: Approval, state: PlayerState, snap: LocalSnapshot) -> AnyStakes | None:
        return parley(ok, state, snap) if ok.route is Route.SOCIAL else None

    def consequences(
        self, ok: Approval, state: PlayerState, snap: LocalSnapshot, ruling: Ruling | None
    ) -> list[DomainEvent]:
        assert ok.route is Route.FIXED and ok.skill and ok.guidance  # 交涉·求艺的定案由门面经 parley.settled 落为事件
        art = snap.skill(ok.skill)
        assert art is not None
        return [SkillPracticed(skill_id=ok.skill, proficiency_gained=gain(ok.guidance, art.tier), source_id=ok.source)]


def _plead(intent: PlayerIntent, art: SkillView, teacher: CharacterView | None, refusal: Rejection) -> Verdict:
    """师父不肯：寻常相求即驳回；以言辞 / 人情相求则改走交涉（所图求艺），对象是驳回所说的那位师父。"""
    if refusal.code != "UNWILLING" or teacher is None or intent.approach not in _ASKING:
        return refusal
    return Approval(intent, target=teacher.id, skill=art.id, route=Route.SOCIAL, aim=Aim.LEARN)


def _masters(art: SkillView, snap: LocalSnapshot) -> list[CharacterView]:
    return [c for c in snap.characters if art.id in c.skill_ids and not c.subdued]


def _named_master(intent: PlayerIntent, art: SkillView, snap: LocalSnapshot) -> CharacterView | None:
    return resolve(intent.target_entity, _masters(art, snap), names) if intent.skill_used else None


def _teacher(
    intent: PlayerIntent, art: SkillView, state: PlayerState, snap: LocalSnapshot
) -> tuple[CharacterView | None, Rejection | None]:
    """
    在场且通晓此功、肯教你的人（人情 rank 够得上 required_regard）。玩家点名的师父优先：被拒时说的是他，肯教时也是他；他不肯而旁人肯，由肯教的人传；
    没点名而人人不肯时，驳回说的是交情最深的那位——离肯教最近的人。返回 (那位师父, 驳回)：肯教时驳回为 None，无人可教时师父为 None。
    驳回理由照实写人情：敌视者写明结怨的缘由（PlayerState.attitude_causes），戒备者是提防，漠然者只是素无交情——你们也许刚说过话，并非素不相识；
    友善而功高者是交情尚浅。unlock 写明要到哪一档人情才肯传，target_id / subject_id 是那位师父与那门武学（心事线索按它们立键）。
    """
    masters = _masters(art, snap)
    if not masters:
        return None, Rejection("NO_TEACHER", f"{art.name}须有通晓此功之人当面传授。")
    named = _named_master(intent, art, snap)
    candidates = sorted(masters, key=lambda c: (c is not named, -c.attitude.rank))  # 点名者优先，其次交情最深者
    need = required_regard(art)
    teacher = next((c for c in candidates if c.attitude.rank >= need.rank), None)
    if teacher is not None:
        return teacher, None
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
    return who, Rejection("UNWILLING", reason, unlock=need.value, target_id=who.id, subject_id=art.id)


__all__ = ["AttackRule", "LearnRule", "required_regard", "retreat", "strike", "strike_stakes"]
