"""
[INPUT]: 依赖 app.domain.covert 的 assess_covert / settle_covert / effects / CovertStakes / CovertRuling，依赖 app.domain.rules 的 decide / stakes，
         依赖 app.domain.stakes 的 Proposal，依赖 tests/test_rules 的 scene / act / recast / reitem / ROOTED
[OUTPUT]: 暗中取物的单测：区间矩阵（境界差、失主戒心 −1、骗取信你之人 +1、失主被制住 +2；≤ −2 只有失手）按「无痕 / 未遂 / 败露 / 失手」连续切片、
          settle 出界取确定性裁决、效果（无痕易手、未遂只留 Maneuvered、败露易手且失主敌视、失手只结仇、已敌视不重复入账、永不伤人）；
          经 rules 端到端：潜行偷剑、计谋骗剑（败露是越出舒适区的得手：失主记两格疑心）、高一境稳稳得手、险物到手照样受伤而不致死、
          差两境以上（偷绝顶之人）只有失手、东西绝不到手
[POS]: tests 的暗中死线：被察觉才是代价；暗中行事永不致死
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import pytest

from app.domain.covert import CovertRuling, CovertStakes, assess_covert, effects, settle_covert
from app.domain.events import HealthChanged, ItemTransferred, Maneuvered, PlayerDied, RelationChanged
from app.domain.intent import ActionType, Approach
from app.domain.models import Attitude, Tier
from app.domain.outcomes import CovertOutcome, SocialOutcome
from app.domain.rules import decide, stakes
from app.domain.stakes import Proposal
from tests.test_rules import PID, ROOTED, act, owed, recast, reitem, scene

C = CovertOutcome
P = Approach


def filch(player: Tier, holder: Tier, attitude: Attitude = Attitude.NEUTRAL, approach: Approach = P.STEALTH,
          subdued: bool = False) -> CovertStakes:
    return assess_covert(target_id="chr:x", item_id="itm:y", approach=approach, attitude=attitude,
                         player=player, holder=holder, subdued=subdued)


@pytest.mark.parametrize(
    ("at_stake", "margin", "admissible", "canonical"),
    [
        (filch(Tier.FIRST, Tier.THIRD), 2, (C.CLEAN,), C.CLEAN),
        (filch(Tier.SECOND, Tier.THIRD), 1, (C.CLEAN, C.FOILED), C.CLEAN),
        (filch(Tier.THIRD, Tier.THIRD), 0, (C.CLEAN, C.FOILED, C.EXPOSED), C.FOILED),
        (filch(Tier.NONE, Tier.THIRD), -1, (C.FOILED, C.EXPOSED, C.CAUGHT), C.FOILED),
        (filch(Tier.NONE, Tier.SECOND), -2, (C.CAUGHT,), C.CAUGHT),  # 差两境：东西绝不会到手
        (filch(Tier.NONE, Tier.PEERLESS), -4, (C.CAUGHT,), C.CAUGHT),
        (filch(Tier.THIRD, Tier.THIRD, Attitude.WARY), -1, (C.FOILED, C.EXPOSED, C.CAUGHT), C.FOILED),  # 他正盯着你
        (filch(Tier.THIRD, Tier.THIRD, Attitude.FRIENDLY, P.GUILE), 1, (C.CLEAN, C.FOILED), C.CLEAN),  # 骗的是信你的人
        (filch(Tier.THIRD, Tier.THIRD, Attitude.FRIENDLY), 0, (C.CLEAN, C.FOILED, C.EXPOSED), C.FOILED),  # 偷不沾这份信任
        (filch(Tier.NONE, Tier.THIRD, subdued=True), 1, (C.CLEAN, C.FOILED), C.CLEAN),
    ],
)
def test_the_covert_envelope(at_stake: CovertStakes, margin: int, admissible: tuple[CovertOutcome, ...], canonical: CovertOutcome) -> None:
    assert (at_stake.margin, at_stake.admissible, at_stake.canonical) == (margin, admissible, canonical)
    order = list(CovertOutcome)
    indices = [order.index(o) for o in at_stake.admissible]
    assert indices == list(range(indices[0], indices[-1] + 1))  # 按「后果由轻到重」连续切片
    assert at_stake.contested is (len(admissible) > 1)


def test_settle_takes_only_outcomes_inside_the_rails() -> None:
    at_stake = filch(Tier.NONE, Tier.THIRD)
    assert settle_covert(at_stake, C.CAUGHT) == CovertRuling(C.CAUGHT, adopted=True)
    assert settle_covert(at_stake, C.CLEAN) == CovertRuling(C.FOILED, adopted=False)
    assert settle_covert(at_stake, SocialOutcome.GRANTED) == settle_covert(at_stake)


@pytest.mark.parametrize(
    ("outcome", "transferred", "hostile"),
    [(C.CLEAN, True, False), (C.FOILED, False, False), (C.EXPOSED, True, True), (C.CAUGHT, False, True)],
)
def test_being_noticed_is_the_price(outcome: CovertOutcome, transferred: bool, hostile: bool) -> None:
    at_stake = filch(Tier.NONE, Tier.THIRD)
    events = effects(at_stake, CovertRuling(outcome, adopted=True), PID)
    assert events[0] == Maneuvered(item_id="itm:y", target_id="chr:x", approach=P.STEALTH, outcome=outcome)
    assert any(isinstance(e, ItemTransferred) and e.to_holder == PID for e in events) is transferred
    grudges = [e for e in events if isinstance(e, RelationChanged)]
    assert bool(grudges) is hostile and all(g.attitude is Attitude.HOSTILE and g.basis == outcome.value for g in grudges)
    assert not any(isinstance(e, HealthChanged | PlayerDied) for e in events)
    already = filch(Tier.NONE, Tier.THIRD, Attitude.HOSTILE)
    assert not any(isinstance(e, RelationChanged) for e in effects(already, CovertRuling(outcome, True), PID))


async def test_stealing_the_sword_by_stealth_and_by_guile() -> None:
    state, snap = await scene("loc:无量山")
    sneak = act(ActionType.TAKE, target_entity="无量剑", approach=P.STEALTH)
    at_stake = stakes(sneak, state, snap)
    assert isinstance(at_stake, CovertStakes) and (at_stake.target_id, at_stake.item_id) == ("chr:左子穆", "itm:无量剑")
    assert decide(sneak, state, snap) == [
        Maneuvered(item_id="itm:无量剑", target_id="chr:左子穆", approach=P.STEALTH, outcome=C.FOILED)
    ]
    assert decide(sneak, state, snap, Proposal(C.EXPOSED)) == [
        Maneuvered(item_id="itm:无量剑", target_id="chr:左子穆", approach=P.STEALTH, outcome=C.EXPOSED),
        ItemTransferred(item_id="itm:无量剑", from_holder="chr:左子穆", to_holder=PID),
        RelationChanged(character_id="chr:左子穆", attitude=Attitude.HOSTILE, cause="撞破你行窃", basis="败露"),
        owed("chr:左子穆", "左子穆", "疑心", 2),  # 势均力敌还要得手：越出舒适区两格，付不出就记成疑心
    ]
    con = act(ActionType.TAKE, target_entity="无量剑", approach=P.GUILE)
    caught = decide(con, state, snap, Proposal(C.CAUGHT))
    assert caught[-1] == RelationChanged(character_id="chr:左子穆", attitude=Attitude.HOSTILE, cause="识破你的骗局", basis="失手")


async def test_a_better_hand_lifts_it_clean_and_a_hazard_still_bites() -> None:
    state, snap = await scene("loc:无量山", ROOTED)
    sneak = act(ActionType.TAKE, target_entity="无量剑", approach=P.STEALTH)
    assert decide(sneak, state, snap) == [
        Maneuvered(item_id="itm:无量剑", target_id="chr:左子穆", approach=P.STEALTH, outcome=C.CLEAN),
        ItemTransferred(item_id="itm:无量剑", from_holder="chr:左子穆", to_holder=PID),
    ]
    venom = reitem(snap, "itm:无量剑", hazard="剑上淬毒")
    assert decide(sneak, state, venom)[-1] == HealthChanged(delta=-15, cause="触到无量剑，剑上淬毒", source_id="itm:无量剑")
    wary = recast(snap, "chr:左子穆", attitude=Attitude.WARY)
    at_stake = stakes(sneak, state, wary)
    assert isinstance(at_stake, CovertStakes) and at_stake.margin == 0  # 二流对三流 +1，他正盯着你 −1


async def test_a_hopelessly_outclassed_thief_never_walks_off_with_it() -> None:
    """差了两境以上（不入流偷绝顶乔峰的打狗棒，差额 −4）：区间只有失手，不论地下城主提议什么，东西都不会到手——暗取不是越级取胜的后门。"""
    state, snap = await scene("loc:无锡城")
    for approach in (P.STEALTH, P.GUILE):
        lift = act(ActionType.TAKE, target_entity="打狗棒", approach=approach)
        at_stake = stakes(lift, state, snap)
        assert isinstance(at_stake, CovertStakes) and at_stake.margin == -4
        assert (at_stake.admissible, at_stake.canonical, at_stake.contested) == ((C.CAUGHT,), C.CAUGHT, False)
        for outcome in (None, *C):
            events = decide(lift, state, snap, Proposal(outcome) if outcome else None)
            assert not any(isinstance(e, ItemTransferred) for e in events), events
            assert events[0] == Maneuvered(item_id="itm:打狗棒", target_id="chr:乔峰", approach=approach, outcome=C.CAUGHT)
