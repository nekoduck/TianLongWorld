"""
[INPUT]: 依赖 app.domain.rules 的 decide / adjudicate / stakes / resolve，依赖 app.domain.combat 的 assess / settle / CombatProposal，
         依赖 app.domain.aggregates 的 Player，依赖 InMemoryWorldGraph 生成快照，依赖 tests/world 的 WORLD
[OUTPUT]: 裁决规则的单测：每种动作的放行与驳回、模糊裁决的可裁区间与定案钳位、人情涟漪、火候折算境界、
          修习的入门与精进逐条核验、调息疗伤、天降神兵此路不通
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
    DomainEvent,
    EventEnvelope,
    HealthChanged,
    ItemTransferred,
    Moved,
    PlayerDied,
    PlayerSpawned,
    RelationChanged,
    SkillExecuted,
    SkillPracticed,
)
from app.domain.intent import ActionType, PlayerIntent
from app.domain.models import Attitude, Disposition, Tier
from app.domain.progression import MAX_HP, REST_GAIN
from app.domain.rules import Approval, Rejection, adjudicate, decide, resolve, stakes
from app.domain.snapshot import LocalSnapshot
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


def act(kind: ActionType, **fields: str) -> PlayerIntent:
    return PlayerIntent(action_type=kind, **fields)


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
    assert events[-1] == Moved(from_location_id="loc:无量山", to_location_id="loc:大理城", exit_label="南下")  # 逃脱是真的逃
    lenient = decide(attack, state, snap, CombatProposal(Out.MINOR_WOUND, -15))
    assert lenient[0].outcome is Out.MINOR_WOUND and lenient[1] == HealthChanged(  # type: ignore[attr-defined]
        delta=-15, cause="与龚光杰交手", source_id="chr:龚光杰")
    assert not any(isinstance(e, Moved) for e in lenient)  # 轻伤只是退开，人还在原地


async def test_a_severe_escape_retreats_the_way_you_came() -> None:
    came = Moved(from_location_id="loc:无量玉洞", to_location_id="loc:无量山", exit_label="攀上")
    state, snap = await scene("loc:无量玉洞", came)
    events = decide(act(ActionType.ATTACK, target_entity="龚光杰"), state, snap)
    assert events[-1] == Moved(from_location_id="loc:无量山", to_location_id="loc:无量玉洞", exit_label="崖下")


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
    assert changes == {  # 南海鳄神与之无关，不动
        "chr:左子穆": Attitude.HOSTILE, "chr:龚光杰": Attitude.HOSTILE, "chr:辛双清": Attitude.FRIENDLY,
    }


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
        RelationChanged(character_id="chr:段正淳", attitude=Attitude.FRIENDLY, cause="物归原主"),
    ]
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
    trusted = RelationChanged(character_id="chr:段正淳", attitude=Attitude.FRIENDLY, cause="物归原主")
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
    assert decide(act(ActionType.REST), state, snap) == [HealthChanged(delta=REST_GAIN, cause="调息疗伤")]
    nearly, snap = await scene("loc:大理城", HealthChanged(delta=-5, cause="磕碰"))
    assert decide(act(ActionType.REST), nearly, snap) == [HealthChanged(delta=5, cause="调息疗伤")]  # 不溢出上限


async def test_invalid_never_passes() -> None:
    state, snap = await scene("loc:大理城")
    verdict = adjudicate(PlayerIntent.invalid("北宋没有手枪"), state, snap)
    assert verdict == Rejection("INVALID", "北宋没有手枪")
