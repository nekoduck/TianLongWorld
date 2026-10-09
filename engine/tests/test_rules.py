"""
[INPUT]: 依赖 app.domain.rules 的 decide / adjudicate / stakes / resolve，依赖 app.domain.combat 的 assess / settle / CombatProposal，
         依赖 app.domain.stakes 的 Proposal / risk_of / route_of，依赖 app.domain.aggregates 的 Player，依赖 InMemoryWorldGraph 生成快照，依赖 tests/world 的 WORLD
[OUTPUT]: scene / act / recast / restock / reitem 助手（别的领域用例复用）；
          裁决规则的单测：每种动作的放行与驳回、模糊裁决的可裁区间与定案钳位、人情涟漪（只认开篇羁绊，「敌人之敌」升一档）、火候折算境界、
          修习的入门与精进逐条核验、仇人在侧不得修习、求教被拒的理由照实写人情、调息疗伤（source="rest"）、天降神兵此路不通；
          P1：人情按 rank 比——求教门槛随武学境界（三流须友善、二流及以上须信赖，驳回带 unlock）、物归原主直升信赖（已信赖不重复；
          从物主本人手里偷来、讨来、夺来、取来的再还只是易手，经第三人转手也洗不白）、
          戒备不是仇人（不拦调息与修习）、服药（ItemConsumed + HealthChanged(source="item")，不在行囊 / 无用法 / 无伤可疗即驳回）、
          驳回入账带上所图与手段、rules 拆包后旧的导入路径照旧可用；
          P1 阶段 B：取物的物性闸门（不可携带 NOT_PORTABLE 带 unlock、险物到手即受伤且留一口气）、他人之物按手段分三路（寻常驳回并提示、武力夺物同出手区间且得手即易手、
          言辞讨要、潜行偷取）、重伤逃脱避开去处有仇人的出路、SkillExecuted 记手段、Conversed 记落了地的话题、stakes 门面返回三路赌注之一、
          通用 Proposal 与 CombatProposal 定案一致、风险档只看最坏一端
[POS]: tests 的逻辑死线：能力成长、物品获取、人际变化只能由图谱拓扑推导；地下城主只能在领域圈出的区间里挑结局——这里逐条钉死
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.domain.aggregates import Player, PlayerState
from app.domain.combat import CombatOutcome, CombatProposal, assess, settle
from app.domain.events import (
    ActionFailed,
    Conversed,
    DomainEvent,
    EventEnvelope,
    HealthChanged,
    ItemConsumed,
    ItemTransferred,
    Moved,
    PlayerDied,
    PlayerSpawned,
    RelationChanged,
    SkillExecuted,
    SkillPracticed,
)
from app.domain.intent import ActionType, Aim, Approach, PlayerIntent
from app.domain.models import Attitude, Disposition, ItemUse, Tier
from app.domain.outcomes import CovertOutcome, SocialOutcome
from app.domain.progression import MAX_HP, REST_GAIN
from app.domain.rules import Approval, Rejection, adjudicate, decide, resolve, stakes
from app.domain.snapshot import ExitView, ItemView, LocalSnapshot
from app.domain.stakes import Proposal, Risk, risk_of, route_of
from app.infrastructure.persistence.memory_graph import InMemoryWorldGraph
from tests.world import WORLD

PID = "ply:rules"
Out = CombatOutcome


async def scene(at: str, *events: DomainEvent, aptitude: float = 1.0) -> tuple[PlayerState, LocalSnapshot]:
    history = [PlayerSpawned(player_id=PID, name="阿星", location_id=at, aptitude=aptitude), *events]
    envelopes = [
        EventEnvelope(stream_id=PID, version=i, event_id=uuid4(), recorded_at=datetime.now(UTC), event=e)  # type: ignore[arg-type]
        for i, e in enumerate(history, start=1)
    ]
    graph = InMemoryWorldGraph()
    await graph.seed(WORLD)
    await graph.project(PID, envelopes)
    return Player.from_history(PID, envelopes).state, await graph.local_snapshot(PID)


def act(kind: ActionType, **fields: object) -> PlayerIntent:
    return PlayerIntent(action_type=kind, **fields)  # type: ignore[arg-type]


def recast(snap: LocalSnapshot, cid: str, **update: object) -> LocalSnapshot:
    """改写快照里一位在场者的视图字段（性情、羁绊的 era / lead……）：阶段 B 的图谱实现之外，领域用例自己摆场面。"""
    people = tuple(c.model_copy(update=update) if c.id == cid else c for c in snap.characters)
    return snap.model_copy(update={"characters": people})


def reitem(snap: LocalSnapshot, iid: str, **update: object) -> LocalSnapshot:
    return snap.model_copy(update={"items": tuple(i.model_copy(update=update) if i.id == iid else i for i in snap.items)})


def restock(snap: LocalSnapshot, *items: ItemView) -> LocalSnapshot:
    return snap.model_copy(update={"items": (*snap.items, *items)})


def failure(events: list[DomainEvent]) -> ActionFailed:
    assert len(events) == 1 and isinstance(events[0], ActionFailed), events
    return events[0]


def practiced(skill: str, points: int, source: str | None = None) -> SkillPracticed:
    return SkillPracticed(skill_id=skill, proficiency_gained=points, source_id=source)


# ============================================================
#  名称落地
# ============================================================
def test_resolve_prefers_exact_and_refuses_to_guess() -> None:
    names = {"甲": ("段誉", "段公子"), "乙": ("段正淳", "段王爷")}

    def pick(name: str) -> str | None:
        return resolve(name, names, lambda k: names[k])

    assert pick("段公子") == "甲"
    assert pick("那位段王爷大人") == "乙"
    assert pick("段") is None  # 单字不猜
    assert pick("姓段的") is None


async def test_titles_resolve_to_the_true_name() -> None:
    state, snap = await scene("loc:大理城")
    verdict = adjudicate(act(ActionType.TALK, target_entity="恶贯满盈"), state, snap)
    assert isinstance(verdict, Approval) and verdict.target == "chr:段延庆"  # 喊称号，落到本名


# ============================================================
#  移动 / 静观 / 交谈
# ============================================================
async def test_move_only_along_connects_to() -> None:
    state, snap = await scene("loc:无量山")
    assert decide(act(ActionType.MOVE, target_entity="大理城"), state, snap) == [
        Moved(from_location_id="loc:无量山", to_location_id="loc:大理城", exit_label="南下")
    ]
    failed = failure(decide(act(ActionType.MOVE, target_entity="无锡城"), state, snap))
    assert failed.reason_code == "NO_PATH" and "南下（大理城）" in failed.reason


async def test_observe_is_a_pure_query() -> None:
    state, snap = await scene("loc:无量山")
    assert decide(act(ActionType.OBSERVE), state, snap) == []


async def test_talk_needs_presence_and_the_dead_are_not_present() -> None:
    state, snap = await scene("loc:无锡城")
    assert isinstance(adjudicate(act(ActionType.TALK, target_entity="乔帮主"), state, snap), Approval)
    assert failure(decide(act(ActionType.TALK, target_entity="汪剑通"), state, snap)).reason_code == "NOT_PRESENT"


# ============================================================
#  模糊裁决：领域圈区间，地下城主在区间里挑
# ============================================================
@pytest.mark.parametrize(
    ("attacker", "defender", "disposition", "hp", "admissible", "canonical"),
    [
        (Tier.FIRST, Tier.THIRD, Disposition.NEUTRAL, 100, (Out.SUCCESS,), Out.SUCCESS),
        (Tier.FIRST, Tier.FIRST, Disposition.RUTHLESS, 100, (Out.SUCCESS, Out.STALEMATE, Out.MINOR_WOUND), Out.STALEMATE),
        (Tier.NONE, Tier.THIRD, Disposition.NEUTRAL, 100, (Out.STALEMATE, Out.MINOR_WOUND, Out.SEVERE_WOUND), Out.MINOR_WOUND),
        (Tier.NONE, Tier.THIRD, Disposition.RUTHLESS, 100, (Out.MINOR_WOUND, Out.SEVERE_WOUND), Out.SEVERE_WOUND),
        (Tier.NONE, Tier.THIRD, Disposition.RUTHLESS, 10, (Out.SEVERE_WOUND, Out.DEATH), Out.DEATH),
        (Tier.NONE, Tier.SECOND, Disposition.RUTHLESS, 100, (Out.SEVERE_WOUND,), Out.SEVERE_WOUND),
        (Tier.NONE, Tier.SECOND, Disposition.RUTHLESS, 40, (Out.SEVERE_WOUND, Out.DEATH), Out.DEATH),
        (Tier.NONE, Tier.FIRST, Disposition.RUTHLESS, 100, (Out.SEVERE_WOUND, Out.DEATH), Out.DEATH),
        (Tier.NONE, Tier.PEERLESS, Disposition.NEUTRAL, 100, (Out.MINOR_WOUND, Out.SEVERE_WOUND), Out.SEVERE_WOUND),
        (Tier.NONE, Tier.FIRST, Disposition.MERCIFUL, 100, (Out.MINOR_WOUND, Out.SEVERE_WOUND), Out.MINOR_WOUND),
    ],
)
def test_combat_envelope(
    attacker: Tier, defender: Tier, disposition: Disposition, hp: int,
    admissible: tuple[CombatOutcome, ...], canonical: CombatOutcome,
) -> None:
    at_stake = assess(defender_id="chr:x", skill_id=None, item_id=None, attacker=attacker, defender=defender,
                      disposition=disposition, player_hp=hp)
    assert at_stake.admissible == admissible and at_stake.canonical is canonical
    assert at_stake.contested is (len(admissible) > 1)


def test_settle_clamps_the_game_master_into_the_rails() -> None:
    at_stake = assess(defender_id="chr:龚光杰", skill_id=None, item_id=None, attacker=Tier.NONE, defender=Tier.THIRD,
                      disposition=Disposition.RUTHLESS, player_hp=100)
    assert settle(at_stake) == settle(at_stake, None)  # 缺席即确定性裁决
    assert settle(at_stake).outcome is Out.SEVERE_WOUND and settle(at_stake).hp_change == -58 and not settle(at_stake).adopted
    softer = settle(at_stake, CombatProposal(Out.MINOR_WOUND, -12))
    assert (softer.outcome, softer.hp_change, softer.adopted) == (Out.MINOR_WOUND, -12, True)
    greedy = settle(at_stake, CombatProposal(Out.SUCCESS, 0))  # 越级取胜不在区间里
    assert (greedy.outcome, greedy.hp_change, greedy.adopted) == (Out.SEVERE_WOUND, -58, False)
    assert settle(at_stake, CombatProposal(Out.MINOR_WOUND, 99)).hp_change == -25  # 正数按扣减理解，钳进轻伤的区间
    frail = replace(at_stake, player_hp=30)
    assert settle(frail, CombatProposal(Out.SEVERE_WOUND, -70)).hp_change == -29  # 非毙命至少留一口气


async def test_bare_handed_against_ruthless_gong_guangjie_ends_in_a_severe_escape() -> None:
    state, snap = await scene("loc:无量山")
    attack = act(ActionType.ATTACK, target_entity="龚光杰")
    at_stake = stakes(attack, state, snap)
    assert at_stake is not None and at_stake.contested and at_stake.admissible == (Out.MINOR_WOUND, Out.SEVERE_WOUND)
    events = decide(attack, state, snap)
    assert events[:2] == [
        SkillExecuted(skill_id=None, target_id="chr:龚光杰", outcome=Out.SEVERE_WOUND),
        HealthChanged(delta=-58, cause="与龚光杰交手", source_id="chr:龚光杰"),
    ]
    assert not any(isinstance(e, PlayerDied) for e in events)  # 重伤逃脱，不是二极管式的毙命
    assert events[-1] == Moved(  # 逃脱是真的逃
        from_location_id="loc:无量山", to_location_id="loc:大理城", exit_label="南下", fleeing=True
    )
    lenient = decide(attack, state, snap, CombatProposal(Out.MINOR_WOUND, -15))
    assert lenient[0].outcome is Out.MINOR_WOUND and lenient[1] == HealthChanged(  # type: ignore[attr-defined]
        delta=-15, cause="与龚光杰交手", source_id="chr:龚光杰")
    assert not any(isinstance(e, Moved) for e in lenient)  # 轻伤只是退开，人还在原地


async def test_a_severe_escape_retreats_the_way_you_came() -> None:
    came = Moved(from_location_id="loc:无量玉洞", to_location_id="loc:无量山", exit_label="攀上")
    state, snap = await scene("loc:无量玉洞", came)
    events = decide(act(ActionType.ATTACK, target_entity="龚光杰"), state, snap)
    assert events[-1] == Moved(from_location_id="loc:无量山", to_location_id="loc:无量玉洞", exit_label="崖下", fleeing=True)


async def test_a_second_escape_never_runs_back_to_the_foes_fled_from() -> None:
    """连败两场：第二次夺路不能沿来路逃回第一场的仇家面前（仇人不会挪窝）——另寻出路；条条都通险地，才留在原地。"""
    went = Moved(from_location_id="loc:大理城", to_location_id="loc:无量山", exit_label="北上")
    fled = Moved(from_location_id="loc:无量山", to_location_id="loc:大理城", exit_label="南下", fleeing=True)
    state, snap = await scene("loc:大理城", went, fled)
    assert state.came_from == "loc:无量山" and state.fled_from == {"loc:无量山"}
    events = decide(act(ActionType.ATTACK, target_entity="段正淳"), state, snap, CombatProposal(Out.SEVERE_WOUND, -30))
    assert events[0].outcome is Out.SEVERE_WOUND  # type: ignore[attr-defined]
    assert events[-1] == Moved(from_location_id="loc:大理城", to_location_id="loc:无锡城", exit_label="东去", fleeing=True)
    cornered = Moved(from_location_id="loc:大理城", to_location_id="loc:无锡城", exit_label="东去", fleeing=True)
    state, snap = await scene("loc:无锡城", cornered)  # 无锡城唯一的出路「西归」通往刚逃离的大理城
    events = decide(act(ActionType.ATTACK, target_entity="乔峰"), state, snap)
    assert events[0].outcome is Out.SEVERE_WOUND  # type: ignore[attr-defined]
    assert not any(isinstance(e, Moved) for e in events)


async def test_extreme_recklessness_is_still_fatal_unless_the_master_softens_it() -> None:
    state, snap = await scene("loc:无量山")
    attack = act(ActionType.ATTACK, target_entity="南海鳄神")  # 不入流对一流狠辣：以卵击石
    events = decide(attack, state, snap)
    assert [type(e).__name__ for e in events] == ["SkillExecuted", "HealthChanged", "PlayerDied"]
    assert events[1] == HealthChanged(delta=-MAX_HP, cause="与南海鳄神交手", source_id="chr:南海鳄神")
    assert events[2] == PlayerDied(cause="冒犯南海鳄神，当场毙命", killer_id="chr:南海鳄神")
    spared = decide(attack, state, snap, CombatProposal(Out.SEVERE_WOUND, -60))
    assert not any(isinstance(e, PlayerDied) for e in spared)  # 地下城主可以手下留情——区间里本就有重伤逃脱


async def test_a_neutral_grandmaster_does_not_kill_on_first_offense() -> None:
    state, snap = await scene("loc:无锡城")
    events = decide(act(ActionType.ATTACK, target_entity="乔峰"), state, snap)
    assert events[0].outcome is Out.SEVERE_WOUND and not any(isinstance(e, PlayerDied) for e in events)  # type: ignore[attr-defined]
    wounded, snap = await scene("loc:无锡城", HealthChanged(delta=-80, cause="与乔峰交手", source_id="chr:乔峰"))
    again = decide(act(ActionType.ATTACK, target_entity="乔峰"), wounded, snap)
    assert again[0].outcome is Out.SEVERE_WOUND  # type: ignore[attr-defined]
    assert not any(isinstance(e, PlayerDied) for e in again)  # 中庸之人不下杀手，奄奄一息再犯也只是重伤
    assert wounded.hp + sum(e.delta for e in again if isinstance(e, HealthChanged)) == 1


async def test_attack_ripples_along_has_relation_to_witnesses_only() -> None:
    state, snap = await scene("loc:无量山")
    events = decide(act(ActionType.ATTACK, target_entity="左子穆"), state, snap)
    assert events[0] == SkillExecuted(skill_id=None, target_id="chr:左子穆", outcome=Out.MINOR_WOUND)
    changes = {e.character_id: e.attitude for e in events if isinstance(e, RelationChanged)}
    # 南海鳄神与之无关，不动；辛双清是左子穆开篇就有的仇家（东西宗相争）：敌人之敌，升一档
    assert changes == {"chr:左子穆": Attitude.HOSTILE, "chr:龚光杰": Attitude.HOSTILE, "chr:辛双清": Attitude.FRIENDLY}
    causes = {e.character_id: (e.cause, e.basis) for e in events if isinstance(e, RelationChanged)}
    assert causes["chr:龚光杰"] == ("你打伤其师父左子穆", "师徒") and causes["chr:左子穆"] == ("遭你出手相攻", "交手")


async def test_merciful_kin_turns_hostile_but_spares_you() -> None:
    state, snap = await scene("loc:大理城")
    events = decide(act(ActionType.ATTACK, target_entity="段正淳"), state, snap)
    assert events[0].outcome is Out.MINOR_WOUND  # type: ignore[attr-defined]
    assert {e.character_id for e in events if isinstance(e, RelationChanged)} == {"chr:段正淳", "chr:段誉"}


async def test_mastery_not_the_manual_decides_the_tier() -> None:
    scroll = ItemTransferred(item_id="itm:北冥神功卷轴", from_holder="loc:无量玉洞", to_holder=PID)
    novice, snap = await scene("loc:无量山", scroll, practiced("art:北冥神功", 10, "itm:北冥神功卷轴"))
    at_stake = stakes(act(ActionType.ATTACK, target_entity="左子穆"), novice, snap)
    assert at_stake is not None and at_stake.attacker_tier is Tier.THIRD and at_stake.contested  # 一流功夫，初窥门径：三流
    adept, snap = await scene("loc:无量山", scroll, practiced("art:北冥神功", 20, "itm:北冥神功卷轴"))
    events = decide(act(ActionType.ATTACK, target_entity="左子穆"), adept, snap)
    assert events[0] == SkillExecuted(skill_id="art:北冥神功", target_id="chr:左子穆", outcome=Out.SUCCESS)
    holding, snap = await scene("loc:无量山", scroll)
    assert decide(act(ActionType.ATTACK, target_entity="左子穆"), holding, snap)[0].skill_id is None  # type: ignore[attr-defined]


async def test_weapons_do_not_change_tier_and_unknown_arts_are_refused() -> None:
    state, snap = await scene("loc:无量山", ItemTransferred(item_id="itm:玉佩", from_holder="loc:无量山", to_holder=PID))
    events = decide(act(ActionType.ATTACK, target_entity="左子穆", item_used="玉佩"), state, snap)
    assert events[0].outcome is Out.MINOR_WOUND and events[0].item_id == "itm:玉佩"  # type: ignore[attr-defined]
    refused = failure(decide(act(ActionType.ATTACK, target_entity="左子穆", skill_used="无量剑法"), state, snap))
    assert refused.reason_code == "NOT_KNOWN" and "无量剑法" in refused.reason  # 此地确有此功，只是你不会
    flourish = decide(act(ActionType.ATTACK, target_entity="左子穆", skill_used="黑虎掏心"), state, snap)
    assert isinstance(flourish[0], SkillExecuted) and flourish[0].skill_id is None  # 自拟的招式名只是笔墨，照常徒手出手
    assert failure(decide(act(ActionType.ATTACK, target_entity="左子穆", item_used="倚天剑"), state, snap)
                   ).reason_code == "NOT_CARRIED"


async def test_only_contested_actions_have_stakes() -> None:
    state, snap = await scene("loc:无量山")
    assert stakes(act(ActionType.MOVE, target_entity="大理城"), state, snap) is None
    assert stakes(act(ActionType.ATTACK, target_entity="乔峰"), state, snap) is None  # 驳回的举动没有赌注


# ============================================================
#  取物 / 赠物
# ============================================================
async def test_take_from_ground_but_not_from_a_free_hand() -> None:
    state, snap = await scene("loc:无量山")
    assert decide(act(ActionType.TAKE, target_entity="段家玉佩"), state, snap) == [
        ItemTransferred(item_id="itm:玉佩", from_holder="loc:无量山", to_holder=PID)
    ]
    assert failure(decide(act(ActionType.TAKE, target_entity="无量剑"), state, snap)).reason_code == "HELD_BY_OTHER"
    assert failure(decide(act(ActionType.TAKE, target_entity="打狗棒"), state, snap)).reason_code == "NOT_PRESENT"


async def test_take_from_subdued_and_never_twice() -> None:
    win = SkillExecuted(skill_id="art:北冥神功", target_id="chr:左子穆", outcome=Out.SUCCESS)
    state, snap = await scene("loc:无量山", practiced("art:北冥神功", 20, "itm:北冥神功卷轴"), win)
    assert decide(act(ActionType.TAKE, target_entity="无量剑"), state, snap)[0] == ItemTransferred(
        item_id="itm:无量剑", from_holder="chr:左子穆", to_holder=PID
    )
    taken = ItemTransferred(item_id="itm:无量剑", from_holder="chr:左子穆", to_holder=PID)
    state, snap = await scene("loc:无量山", win, taken)
    assert failure(decide(act(ActionType.TAKE, target_entity="无量剑"), state, snap)).reason_code == "ALREADY_CARRIED"


async def test_returning_an_item_to_its_owner_earns_trust() -> None:
    state, snap = await scene("loc:大理城", ItemTransferred(item_id="itm:玉佩", from_holder="loc:无量山", to_holder=PID))
    events = decide(act(ActionType.GIVE, target_entity="段王爷", item_used="玉佩"), state, snap)
    assert events == [
        ItemTransferred(item_id="itm:玉佩", from_holder=PID, to_holder="chr:段正淳"),
        RelationChanged(character_id="chr:段正淳", attitude=Attitude.TRUSTED, cause="物归原主", basis="物归原主"),
    ]  # 信赖的封闭清单：物归原主不论此前几档，直升信赖
    gift = decide(act(ActionType.GIVE, target_entity="段誉", item_used="玉佩"), state, snap)
    assert len(gift) == 1  # 送给不是物主的人：只是易手，不生人情


# ============================================================
#  修习：入门看获取要求，精进看修炼要求，火候靠一次次累积
# ============================================================
SCROLL = ItemTransferred(item_id="itm:北冥神功卷轴", from_holder="loc:无量玉洞", to_holder=PID)
LEAVE = Moved(from_location_id="loc:无量玉洞", to_location_id="loc:无量山", exit_label="攀上")
DROP = ItemTransferred(item_id="itm:北冥神功卷轴", from_holder=PID, to_holder="loc:无量山")


async def test_entry_by_self_study_needs_text_place_and_foundation() -> None:
    state, snap = await scene("loc:无量玉洞")
    assert failure(decide(act(ActionType.LEARN, skill_used="北冥神功"), state, snap)).reason_code == "UNKNOWN_SKILL"
    state, snap = await scene("loc:无量玉洞", SCROLL)
    assert decide(act(ActionType.LEARN, skill_used="北冥神功"), state, snap) == [
        practiced("art:北冥神功", 5, "itm:北冥神功卷轴")  # 一流之功难练：入门所得折半
    ]
    shallow = failure(decide(act(ActionType.LEARN, skill_used="凌波微步"), state, snap))
    assert shallow.reason_code == "MISSING_SKILL" and "略有小成" in shallow.reason
    state, snap = await scene("loc:无量玉洞", SCROLL, LEAVE)
    failed = failure(decide(act(ActionType.LEARN, skill_used="北冥神功"), state, snap))
    assert failed.reason_code == "WRONG_PLACE" and "无量玉洞" in failed.reason


async def test_deepening_by_manual_then_alone_and_foundation_unlocks() -> None:
    entered = practiced("art:北冥神功", 10, "itm:北冥神功卷轴")
    state, snap = await scene("loc:无量玉洞", SCROLL, entered, LEAVE)  # 精进不必回到琅嬛福地
    assert decide(act(ActionType.LEARN, skill_used="北冥神功"), state, snap) == [
        practiced("art:北冥神功", 5, "itm:北冥神功卷轴")  # 参照典籍（一流之功，所得折半）
    ]
    state, snap = await scene("loc:无量玉洞", SCROLL, entered, LEAVE, DROP)
    assert decide(act(ActionType.LEARN, skill_used="北冥神功"), state, snap) == [practiced("art:北冥神功", 2)]  # 闭门苦练
    state, snap = await scene("loc:无量玉洞", SCROLL, entered, practiced("art:北冥神功", 10, "itm:北冥神功卷轴"))
    assert decide(act(ActionType.LEARN, skill_used="凌波微步"), state, snap) == [
        practiced("art:凌波微步", 7, "itm:北冥神功卷轴")  # 北冥神功略有小成，根基到了；二流之功入门 10 ÷ 1.5
    ]
    dull, snap = await scene("loc:无量玉洞", SCROLL, entered, practiced("art:北冥神功", 10), aptitude=0.8)
    assert failure(decide(act(ActionType.LEARN, skill_used="凌波微步"), dull, snap)).reason_code == "MISSING_SKILL"


async def test_conflicts_peak_and_wounds_stop_practice() -> None:
    state, snap = await scene("loc:无量玉洞", SCROLL)
    poisoned = replace(state, practice={"art:化功大法": 10})
    assert failure(decide(act(ActionType.LEARN, skill_used="北冥神功"), poisoned, snap)).reason_code == "CONFLICT"
    peak = replace(state, practice={"art:北冥神功": 200})
    assert failure(decide(act(ActionType.LEARN, skill_used="北冥神功"), peak, snap)).reason_code == "PEAK"
    hurt = replace(state, hp=40)
    failed = failure(decide(act(ActionType.LEARN, skill_used="北冥神功"), hurt, snap))
    assert failed.reason_code == "WOUNDED" and "调息" in failed.reason


async def test_a_teacher_must_be_present_willing_and_you_must_be_ready() -> None:
    state, snap = await scene("loc:无量山")
    assert failure(decide(act(ActionType.LEARN, skill_used="无量剑法"), state, snap)).reason_code == "UNWILLING"
    refused = failure(decide(act(ActionType.LEARN, skill_used="无量剑法", target_entity="辛双清"), state, snap))
    assert refused.reason.startswith("辛双清不肯")  # 拒绝你的是你求的那个人（真实大模型联调时抓到的错位）
    friend = RelationChanged(character_id="chr:辛双清", attitude=Attitude.FRIENDLY, cause="敌人之敌")
    state, snap = await scene("loc:无量山", friend)
    assert decide(act(ActionType.LEARN, skill_used="无量剑法", target_entity="左子穆"), state, snap) == [
        practiced("art:无量剑法", 10, "chr:辛双清")  # 点名的人不肯教，肯教的人在场
    ]
    state, snap = await scene("loc:无量山", friend, practiced("art:无量剑法", 10, "chr:辛双清"))
    assert decide(act(ActionType.LEARN, skill_used="无量剑法"), state, snap) == [
        practiced("art:无量剑法", 15, "chr:辛双清")  # 名师点拨，精进最快（三流之功不打折）
    ]
    trusted = RelationChanged(character_id="chr:段正淳", attitude=Attitude.TRUSTED, cause="物归原主")
    state, snap = await scene("loc:大理城", trusted)
    failed = failure(decide(act(ActionType.LEARN, skill_used="一阳指"), state, snap))
    assert failed.reason_code == "TIER_TOO_LOW" and "二流" in failed.reason
    rooted = practiced("art:北冥神功", 20, "itm:北冥神功卷轴")  # 一流内功略有小成：二流
    state, snap = await scene("loc:大理城", trusted, rooted)
    assert decide(act(ActionType.LEARN, skill_used="一阳指"), state, snap) == [practiced("art:一阳指", 5, "chr:段正淳")]


async def test_a_named_master_who_refuses_is_not_silently_replaced_by_solitude() -> None:
    state, snap = await scene("loc:无量山", practiced("art:无量剑法", 10, "chr:辛双清"))
    refused = failure(decide(act(ActionType.LEARN, skill_used="无量剑法", target_entity="左子穆"), state, snap))
    assert refused.reason.startswith("左子穆不肯")
    assert decide(act(ActionType.LEARN, skill_used="无量剑法"), state, snap) == [practiced("art:无量剑法", 5)]


async def test_no_study_while_a_foe_looms() -> None:
    """仇人在侧无从静心修习（与调息同一道门）：入门、名师点拨、参照典籍都一样；仇人被制住或你离开了，照常修习。"""
    friend = RelationChanged(character_id="chr:辛双清", attitude=Attitude.FRIENDLY, cause="你替她解围")
    grudge = RelationChanged(character_id="chr:龚光杰", attitude=Attitude.HOSTILE, cause="遭你出手相攻")
    state, snap = await scene("loc:无量山", friend, grudge)
    unsafe = failure(decide(act(ActionType.LEARN, skill_used="无量剑法"), state, snap))
    assert unsafe.reason_code == "UNSAFE" and unsafe.reason == "龚光杰在侧虎视眈眈，你无法静心修习。"
    scroll = ItemTransferred(item_id="itm:北冥神功卷轴", from_holder="loc:无量玉洞", to_holder=PID)
    entered = practiced("art:北冥神功", 10, "itm:北冥神功卷轴")
    state, snap = await scene("loc:无量山", scroll, entered, grudge)
    assert failure(decide(act(ActionType.LEARN, skill_used="北冥神功"), state, snap)).reason_code == "UNSAFE"
    subdued = SkillExecuted(skill_id=None, target_id="chr:龚光杰", outcome=Out.SUCCESS)
    state, snap = await scene("loc:无量山", friend, grudge, subdued)
    assert decide(act(ActionType.LEARN, skill_used="无量剑法"), state, snap) == [practiced("art:无量剑法", 10, "chr:辛双清")]


async def test_a_refusal_speaks_of_the_actual_grudge_or_its_absence() -> None:
    """求教被拒的理由照实写人情：仇人写明结怨的缘由，漠然者只是素无交情——绝不说「素不相识」（实录回合 11）。"""
    state, snap = await scene("loc:无量山")
    cold = failure(decide(act(ActionType.LEARN, skill_used="无量剑法", target_entity="辛双清"), state, snap))
    assert cold.reason == "辛双清不肯将无量剑法传给素无交情之人。"
    grudge = RelationChanged(character_id="chr:左子穆", attitude=Attitude.HOSTILE, cause="师徒龚光杰受你攻击")
    state, snap = await scene("loc:无量山", grudge)
    hostile = failure(decide(act(ActionType.LEARN, skill_used="无量剑法", target_entity="左子穆"), state, snap))
    assert hostile.reason_code == "UNWILLING"  # 向仇人本人求教：驳回说的是那段恩怨，而不是笼统的虎视眈眈
    assert hostile.reason == "左子穆不肯将无量剑法传给仇人（师徒龚光杰受你攻击）。"
    assert "素不相识" not in cold.reason + hostile.reason


async def test_a_known_art_whose_text_is_lost_can_still_be_practiced_alone() -> None:
    """失传的自悟之功（sealed、典籍不可考）对已入门者——旧账里学会的、或换蓝图后才封存的——仍可闭门苦练，而不是让裁决崩溃。"""
    from app.domain.models import Acquisition, Transmission

    lost = WORLD.model_copy(update={"martial_arts": tuple(
        a.model_copy(update={"acquisition": Acquisition(transmission=Transmission.SELF, sealed=True)})
        if a.id == "art:北冥神功" else a for a in WORLD.martial_arts
    )})
    history = [PlayerSpawned(player_id=PID, name="阿星", location_id="loc:无量山"), practiced("art:北冥神功", 45)]
    envelopes = [
        EventEnvelope(stream_id=PID, version=i, event_id=uuid4(), recorded_at=datetime.now(UTC), event=e)  # type: ignore[arg-type]
        for i, e in enumerate(history, start=1)
    ]
    graph = InMemoryWorldGraph()
    await graph.seed(lost)
    await graph.project(PID, envelopes)
    state, snap = Player.from_history(PID, envelopes).state, await graph.local_snapshot(PID)
    assert decide(act(ActionType.LEARN, skill_used="北冥神功"), state, snap) == [practiced("art:北冥神功", 2)]


async def test_no_heaven_sent_arts() -> None:
    """六脉神剑无人可教、无典可凭：此情此景根本无从得知——天降神兵此路不通。"""
    state, snap = await scene("loc:大理城")
    assert failure(decide(act(ActionType.LEARN, skill_used="六脉神剑"), state, snap)).reason_code == "UNKNOWN_SKILL"


# ============================================================
#  调息
# ============================================================
async def test_rest_heals_only_the_hurt_and_only_in_safety() -> None:
    state, snap = await scene("loc:无量山")
    assert failure(decide(act(ActionType.REST), state, snap)).reason_code == "UNHURT"
    bruised = HealthChanged(delta=-58, cause="与龚光杰交手", source_id="chr:龚光杰")
    grudge = RelationChanged(character_id="chr:龚光杰", attitude=Attitude.HOSTILE, cause="遭你出手相攻")
    state, snap = await scene("loc:无量山", bruised, grudge)
    unsafe = failure(decide(act(ActionType.REST), state, snap))
    assert unsafe.reason_code == "UNSAFE" and unsafe.reason.startswith("龚光杰")
    away = Moved(from_location_id="loc:无量山", to_location_id="loc:大理城", exit_label="南下")
    state, snap = await scene("loc:无量山", bruised, grudge, away)
    assert decide(act(ActionType.REST), state, snap) == [HealthChanged(delta=REST_GAIN, cause="调息疗伤", source="rest")]
    nearly, snap = await scene("loc:大理城", HealthChanged(delta=-5, cause="磕碰"))
    assert decide(act(ActionType.REST), nearly, snap) == [  # 不溢出上限
        HealthChanged(delta=5, cause="调息疗伤", source="rest")
    ]


async def test_invalid_never_passes() -> None:
    state, snap = await scene("loc:大理城")
    verdict = adjudicate(PlayerIntent.invalid("北宋没有手枪"), state, snap)
    assert verdict == Rejection("INVALID", "北宋没有手枪")


# ============================================================
#  P1：人情只比 rank
# ============================================================
ROOTED = practiced("art:北冥神功", 20, "itm:北冥神功卷轴")  # 一流内功略有小成：二流，够得上一阳指的修炼门槛


@pytest.mark.parametrize(
    ("attitude", "allowed", "reason"),
    [
        (Attitude.FRIENDLY, False, "段正淳与你交情尚浅，一阳指非信赖之人不传。"),
        (Attitude.NEUTRAL, False, "段正淳不肯将一阳指传给素无交情之人。"),
        (Attitude.WARY, False, "段正淳对你心存戒备，不肯将一阳指相传。"),
        (Attitude.TRUSTED, True, ""),
    ],
)
async def test_arts_above_third_rate_are_taught_only_to_the_trusted(attitude: Attitude, allowed: bool, reason: str) -> None:
    """二流及以上的武学非信赖之人不传；驳回写明交情的实情，unlock 写明要到哪一档。"""
    regard = RelationChanged(character_id="chr:段正淳", attitude=attitude, cause="某事")
    state, snap = await scene("loc:大理城", regard, ROOTED)
    events = decide(act(ActionType.LEARN, skill_used="一阳指", target_entity="段正淳"), state, snap)  # 段延庆也会，点名求教
    if allowed:
        assert events == [practiced("art:一阳指", 5, "chr:段正淳")]
        return
    failed = failure(events)
    assert (failed.reason_code, failed.reason, failed.unlock) == ("UNWILLING", reason, "信赖")


async def test_third_rate_arts_need_only_friendship() -> None:
    """三流之功，友善之人便肯教；信赖当然也肯；漠然者不教，unlock 写友善。"""
    for attitude in (Attitude.FRIENDLY, Attitude.TRUSTED):
        state, snap = await scene("loc:无量山", RelationChanged(character_id="chr:辛双清", attitude=attitude, cause="c"))
        assert decide(act(ActionType.LEARN, skill_used="无量剑法"), state, snap) == [
            practiced("art:无量剑法", 10, "chr:辛双清")
        ]
    state, snap = await scene("loc:无量山")
    assert failure(decide(act(ActionType.LEARN, skill_used="无量剑法"), state, snap)).unlock == "友善"


async def test_returning_an_item_jumps_straight_to_trust_and_only_once() -> None:
    """物归原主是信赖封闭清单上唯一的一条：不论此前几档（哪怕敌视）直升信赖；已是信赖就只是易手。"""
    jade = ItemTransferred(item_id="itm:玉佩", from_holder="loc:无量山", to_holder=PID)
    grudge = RelationChanged(character_id="chr:段正淳", attitude=Attitude.HOSTILE, cause="遭你出手相攻")
    state, snap = await scene("loc:大理城", jade, grudge)
    events = decide(act(ActionType.GIVE, target_entity="段正淳", item_used="玉佩"), state, snap)
    assert events[1] == RelationChanged(character_id="chr:段正淳", attitude=Attitude.TRUSTED, cause="物归原主", basis="物归原主")
    trusted = RelationChanged(character_id="chr:段正淳", attitude=Attitude.TRUSTED, cause="物归原主")
    state, snap = await scene("loc:大理城", jade, trusted)
    assert len(decide(act(ActionType.GIVE, target_entity="段正淳", item_used="玉佩"), state, snap)) == 1


async def test_handing_back_what_you_took_from_the_owner_is_not_restoring_it() -> None:
    """
    物归原主只认真正的「归」：从物主本人手里偷来、讨来、夺来、从被制住的他身上取来的东西再还给他，只是易手，不升信赖——
    否则弱者偷了再还就能把仇人刷成信赖、换来二流以上的传功。经第三人转一道手也洗不白；东西若是从别人手里得来的，照旧直升信赖。
    """
    give = act(ActionType.GIVE, target_entity="左子穆", item_used="无量剑")
    sword = "itm:无量剑"
    friend = RelationChanged(character_id="chr:左子穆", attitude=Attitude.FRIENDLY, cause="c")
    beaten = SkillExecuted(skill_id="art:北冥神功", target_id="chr:左子穆", outcome=CombatOutcome.SUCCESS)

    async def took(*setup: DomainEvent, intent: PlayerIntent, proposal: object = None, kind: bool = False) -> list[DomainEvent]:
        state, snap = await scene("loc:无量山", *setup)
        if kind:
            snap = recast(snap, "chr:左子穆", disposition=Disposition.MERCIFUL)
        events = decide(intent, state, snap, proposal)  # type: ignore[arg-type]
        assert ItemTransferred(item_id=sword, from_holder="chr:左子穆", to_holder=PID) in events, events
        return [*setup, *events]

    histories = [
        await took(intent=act(ActionType.TAKE, target_entity="无量剑", approach=Approach.STEALTH),
                   proposal=Proposal(CovertOutcome.EXPOSED)),  # 偷来（败露）
        await took(ROOTED, intent=act(ActionType.TAKE, target_entity="无量剑", approach=Approach.STEALTH)),  # 偷来（无痕）
        await took(friend, intent=act(ActionType.TAKE, target_entity="无量剑", approach=Approach.WORDS),
                   proposal=Proposal(SocialOutcome.GRANTED), kind=True),  # 讨来
        await took(ROOTED, intent=act(ActionType.TAKE, target_entity="无量剑", approach=Approach.FORCE),
                   proposal=CombatProposal(outcome=CombatOutcome.SUCCESS, hp_change=0)),  # 夺来
        await took(ROOTED, beaten, intent=act(ActionType.TAKE, target_entity="无量剑")),  # 从被制住的他身上取来
    ]
    laundered = [ItemTransferred(item_id=sword, from_holder=PID, to_holder="chr:龚光杰"),
                 ItemTransferred(item_id=sword, from_holder="chr:龚光杰", to_holder=PID)]
    for history in (*histories, [*histories[0], *laundered]):
        state, snap = await scene("loc:无量山", *history)
        assert state.taken_from[sword] == "chr:左子穆" and state.attitude_of("chr:左子穆") is not Attitude.TRUSTED
        assert decide(give, state, snap) == [ItemTransferred(item_id=sword, from_holder=PID, to_holder="chr:左子穆")]
    found = ItemTransferred(item_id=sword, from_holder="chr:龚光杰", to_holder=PID)  # 从旁人手里得来：是真的物归原主
    state, snap = await scene("loc:无量山", found)
    assert decide(give, state, snap)[1] == RelationChanged(character_id="chr:左子穆", attitude=Attitude.TRUSTED,
                                                          cause="物归原主", basis="物归原主")


async def test_wariness_is_not_a_grudge() -> None:
    """戒备不是仇人：调息与修习只怕敌视且行动自如的人。"""
    wary = RelationChanged(character_id="chr:龚光杰", attitude=Attitude.WARY, cause="师徒左子穆受你攻击")
    bruised = HealthChanged(delta=-30, cause="与龚光杰交手", source_id="chr:龚光杰")
    state, snap = await scene("loc:无量山", bruised, wary)
    assert decide(act(ActionType.REST), state, snap) == [HealthChanged(delta=REST_GAIN, cause="调息疗伤", source="rest")]
    friend = RelationChanged(character_id="chr:辛双清", attitude=Attitude.FRIENDLY, cause="c")
    state, snap = await scene("loc:无量山", wary, friend)
    assert decide(act(ActionType.LEARN, skill_used="无量剑法"), state, snap) == [practiced("art:无量剑法", 10, "chr:辛双清")]


async def test_using_a_remedy_from_the_pack() -> None:
    """服药：须在行囊里、须有用法、须有伤可疗；用掉写 ItemConsumed，疗伤写 HealthChanged(source="item")，每档药力回 REST_GAIN。"""
    salve = ItemView(id="itm:金创药", name="金创药", holder_id=PID, use=ItemUse(effect="疗伤", potency=2))
    jade = ItemTransferred(item_id="itm:玉佩", from_holder="loc:无量山", to_holder=PID)
    bruised = HealthChanged(delta=-70, cause="与龚光杰交手", source_id="chr:龚光杰")
    state, snap = await scene("loc:无量山", jade, bruised)
    snap = snap.model_copy(update={"items": (*snap.items, salve)})
    assert decide(act(ActionType.USE, item_used="金创药"), state, snap) == [
        ItemConsumed(item_id="itm:金创药", effect="疗伤"),
        HealthChanged(delta=2 * REST_GAIN, cause="服用金创药", source_id="itm:金创药", source="item"),
    ]
    assert failure(decide(act(ActionType.USE, item_used="玉佩"), state, snap)).reason_code == "NO_USE"
    assert failure(decide(act(ActionType.USE, item_used="通天草"), state, snap)).reason_code == "NOT_CARRIED"
    hale, snap = await scene("loc:无量山")
    snap = snap.model_copy(update={"items": (*snap.items, salve)})
    assert failure(decide(act(ActionType.USE, item_used="金创药"), hale, snap)).reason_code == "UNHURT"


async def test_a_refusal_records_what_was_attempted() -> None:
    state, snap = await scene("loc:无量山")
    asked = act(ActionType.TAKE, target_entity="无量剑", aim=Aim.ASK)
    failed = failure(decide(asked, state, snap))
    assert (failed.reason_code, failed.aim, failed.approach, failed.unlock) == ("HELD_BY_OTHER", Aim.ASK, Approach.PLAIN, "强夺、讨要或暗取")
    begged = failure(decide(act(ActionType.LEARN, skill_used="六脉神剑", approach=Approach.FAVOR), state, snap))
    assert (begged.reason_code, begged.aim, begged.approach) == ("UNKNOWN_SKILL", None, Approach.FAVOR)


def test_the_rules_package_keeps_the_old_import_surface() -> None:
    """拆包之后，从 app.domain.rules 能导入的东西一样不少；每种动作都有一条规则。"""
    from app.domain import rules

    for name in ("adjudicate", "decide", "stakes", "resolve", "skill_tier", "player_tier", "best_skill",
                 "Approval", "Rejection", "Verdict", "Rule", "RULES", "_retreat"):
        assert hasattr(rules, name), name
    assert set(rules.RULES) == set(ActionType)
    assert Rejection("INVALID", "x") == Rejection("INVALID", "x", unlock="")


async def test_unnamed_refusals_speak_of_the_closest_master() -> None:
    """没点名而人人不肯：驳回说的是交情最深的那位（段正淳已友善，段延庆漠然）。"""
    friend = RelationChanged(character_id="chr:段正淳", attitude=Attitude.FRIENDLY, cause="c")
    state, snap = await scene("loc:大理城", friend, ROOTED)
    assert failure(decide(act(ActionType.LEARN, skill_used="一阳指"), state, snap)).reason.startswith("段正淳与你交情尚浅")


# ============================================================
#  P1 阶段 B：取物分路、物性闸门、逃向无仇之处、手段与话题入账、三路赌注的门面
# ============================================================
async def test_what_cannot_be_carried_cannot_be_taken_by_any_means() -> None:
    state, snap = await scene("loc:无量山")
    snap = reitem(snap, "itm:玉佩", portable=False)
    for approach in (Approach.PLAIN, Approach.STEALTH):
        failed = failure(decide(act(ActionType.TAKE, target_entity="玉佩", approach=approach), state, snap))
        assert (failed.reason_code, failed.unlock) == ("NOT_PORTABLE", "就地察看")
    assert stakes(act(ActionType.TAKE, target_entity="玉佩"), state, snap) is None


async def test_a_hazard_hurts_the_hand_that_takes_it_but_never_kills() -> None:
    state, snap = await scene("loc:无量山")
    snap = reitem(snap, "itm:玉佩", hazard="剧毒")
    assert decide(act(ActionType.TAKE, target_entity="玉佩"), state, snap) == [
        ItemTransferred(item_id="itm:玉佩", from_holder="loc:无量山", to_holder=PID),
        HealthChanged(delta=-15, cause="触到玉佩，剧毒", source_id="itm:玉佩"),
    ]
    frail, snap = await scene("loc:无量山", HealthChanged(delta=-95, cause="与龚光杰交手", source_id="chr:龚光杰"))
    snap = reitem(snap, "itm:玉佩", hazard="剧毒")
    events = decide(act(ActionType.TAKE, target_entity="玉佩"), frail, snap)
    assert events[-1] == HealthChanged(delta=-4, cause="触到玉佩，剧毒", source_id="itm:玉佩")  # 留一口气
    assert not any(isinstance(e, PlayerDied) for e in events)


async def test_anothers_item_is_routed_by_approach() -> None:
    """他人手中之物：寻常驳回并提示换手段；武力走出手区间（夺物）；言辞 / 人情 / 借势走交涉（讨要）；计谋 / 潜行走暗中。"""
    state, snap = await scene("loc:无量山")
    routes = {
        a: route_of(stakes(act(ActionType.TAKE, target_entity="无量剑", approach=a), state, snap)).value for a in Approach
    }
    assert routes == {Approach.PLAIN: "定", Approach.FORCE: "战", Approach.WORDS: "交", Approach.FAVOR: "交",
                      Approach.GUILE: "暗", Approach.STEALTH: "暗", Approach.LEVERAGE: "交"}
    seize = stakes(act(ActionType.TAKE, target_entity="无量剑", approach=Approach.FORCE), state, snap)
    attack = stakes(act(ActionType.ATTACK, target_entity="左子穆"), state, snap)
    assert seize is not None and attack is not None and seize.admissible == attack.admissible  # 夺物与出手同一个区间
    assert seize.seize_id == "itm:无量剑" and seize.item_id is None  # type: ignore[union-attr]


async def test_seizing_by_force_hands_over_the_item_only_on_success() -> None:
    state, snap = await scene("loc:无量山", ROOTED)  # 二流对三流：技高一筹，稳稳制住
    events = decide(act(ActionType.TAKE, target_entity="无量剑", approach=Approach.FORCE), state, snap)
    assert events[0] == SkillExecuted(skill_id="art:北冥神功", target_id="chr:左子穆", outcome=Out.SUCCESS, approach=Approach.FORCE)
    assert events[2] == ItemTransferred(item_id="itm:无量剑", from_holder="chr:左子穆", to_holder=PID)  # 交手的气血之后、人情涟漪之前
    novice, snap = await scene("loc:无量山")
    lost = decide(act(ActionType.TAKE, target_entity="无量剑", approach=Approach.FORCE), novice, snap)
    assert lost[0].outcome is Out.MINOR_WOUND and not any(isinstance(e, ItemTransferred) for e in lost)  # type: ignore[attr-defined]


async def test_a_subdued_holder_cannot_stop_you_whatever_the_approach() -> None:
    win = SkillExecuted(skill_id="art:北冥神功", target_id="chr:左子穆", outcome=Out.SUCCESS)
    state, snap = await scene("loc:无量山", ROOTED, win)
    sneak = act(ActionType.TAKE, target_entity="无量剑", approach=Approach.STEALTH)
    assert stakes(sneak, state, snap) is None
    assert decide(sneak, state, snap) == [ItemTransferred(item_id="itm:无量剑", from_holder="chr:左子穆", to_holder=PID)]


async def test_a_severe_escape_avoids_exits_where_a_foe_stands() -> None:
    came = Moved(from_location_id="loc:无量玉洞", to_location_id="loc:无量山", exit_label="攀上")
    state, snap = await scene("loc:无量玉洞", came)
    guarded = tuple(e.model_copy(update={"hostile_ahead": e.to_id == "loc:无量玉洞"}) for e in snap.exits)
    events = decide(act(ActionType.ATTACK, target_entity="龚光杰"), state, snap.model_copy(update={"exits": guarded}))
    assert events[-1] == Moved(from_location_id="loc:无量山", to_location_id="loc:大理城", exit_label="南下", fleeing=True)
    walled = tuple(e.model_copy(update={"hostile_ahead": True}) for e in snap.exits)
    events = decide(act(ActionType.ATTACK, target_entity="龚光杰"), state, snap.model_copy(update={"exits": walled}))
    assert events[0].outcome is Out.SEVERE_WOUND and not any(isinstance(e, Moved) for e in events)  # type: ignore[attr-defined]
    assert ExitView(label="x", to_id="loc:x", to_name="x").hostile_ahead is False


async def test_the_approach_and_the_topic_go_on_record() -> None:
    state, snap = await scene("loc:无量山")
    feint = decide(act(ActionType.ATTACK, target_entity="左子穆", approach=Approach.GUILE), state, snap)
    plain = decide(act(ActionType.ATTACK, target_entity="左子穆"), state, snap)
    assert feint[0].approach is Approach.GUILE and feint[0].outcome is plain[0].outcome  # type: ignore[attr-defined]  # 计谋不越级
    assert decide(act(ActionType.TALK, target_entity="左子穆", topic="辛双清"), state, snap) == [
        Conversed(npc_id="chr:左子穆", topic_id="chr:辛双清")
    ]
    assert decide(act(ActionType.TALK, target_entity="左子穆", topic="少林寺"), state, snap) == [Conversed(npc_id="chr:左子穆")]


async def test_the_facade_returns_one_of_three_stakes_and_a_generic_proposal_settles_combat() -> None:
    state, snap = await scene("loc:无量山")
    attack = act(ActionType.ATTACK, target_entity="龚光杰")
    assert decide(attack, state, snap, Proposal(Out.MINOR_WOUND, -15)) == decide(attack, state, snap, CombatProposal(Out.MINOR_WOUND, -15))
    assert decide(attack, state, snap, Proposal(SocialOutcome.GRANTED)) == decide(attack, state, snap)  # 别的路线的结局即出界
    assert risk_of(stakes(attack, state, snap)) is Risk.RISKY
    assert risk_of(stakes(act(ActionType.ATTACK, target_entity="南海鳄神"), state, snap)) is Risk.GRAVE
    assert risk_of(stakes(act(ActionType.MOVE, target_entity="南下"), state, snap)) is Risk.SAFE
    assert risk_of(stakes(act(ActionType.TAKE, target_entity="无量剑", approach=Approach.STEALTH), state, snap)) is Risk.RISKY
