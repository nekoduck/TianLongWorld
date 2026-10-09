"""
[INPUT]: 依赖 app.domain.social 的 assess_social / settle_social / effects / SocialStakes，依赖 app.domain.rules 的 decide / stakes，
         依赖 app.domain.stakes 的 Proposal，依赖 app.domain.aggregates 的 Player，依赖 app.domain.snapshot 的 FactView，依赖 app.domain.lore 的 FactUnlock，
         依赖 tests/test_rules 的 scene / act / recast / ROOTED
[OUTPUT]: 交涉硬轨的单测：区间矩阵（交情 / 性情 / 威逼看境界差 / 筹码 / 难度 / 纠缠不休 / 虚张声势）、硬约束剔格（够不着无如愿、言辞人情不翻脸、
          仁厚不翻脸、剔掉的确定性裁决落到最近一格）、settle 出界取确定性裁决、效果表（阶梯每次至多一档、交涉止于友善、威逼得逞畏而不服、翻脸直落敌视、
          永不伤人）；经 rules 的端到端：言辞结交、打探只学到此人所知且 unlock 落了地的见闻（话题收窄、已知不再学）、言辞讨要、
          以言辞求艺遇 UNWILLING 改走交涉而从不传功（如愿后寻常再求才传）、二流以上求艺永无如愿、威逼被制住之人交出兵器、借势的靠山与见闻筹码、同一手段纠缠扣分；
          Parleyed 带所图的标的 subject_id；筹码只认 known=True 的见闻（知情人不在场照样能用）、打探只认 known=False 者；
          取他人之物以格子的所图为准（言辞说「夺」照样是讨要）；威逼图不来结交 / 化解 / 求艺（改为打探，无可打探则无如愿）
[POS]: tests 的交涉死线：大模型只能在这里圈出的区间里挑，交涉永远推不到信赖、永远不致死
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import pytest

from app.domain.combat import CombatOutcome
from app.domain.events import (
    FactLearned,
    HealthChanged,
    ItemTransferred,
    Parleyed,
    PlayerDied,
    RelationChanged,
    SkillExecuted,
    SkillPracticed,
)
from app.domain.intent import ActionType, Aim, Approach
from app.domain.lore import FactUnlock
from app.domain.models import Attitude, Disposition
from app.domain.outcomes import CovertOutcome, SocialOutcome
from app.domain.rules import decide, normalized, stakes
from app.domain.snapshot import FactView, LocalSnapshot
from app.domain.social import SocialRuling, SocialStakes, assess_social, effects, settle_social
from app.domain.stakes import Proposal
from tests.test_rules import PID, ROOTED, act, recast, scene

S = SocialOutcome
P = Approach


def assess(attitude: Attitude = Attitude.NEUTRAL, approach: Approach = P.WORDS, **kw: object) -> SocialStakes:
    fields: dict[str, object] = {"npc_id": "chr:x", "aim": Aim.BEFRIEND, "approach": approach, "attitude": attitude,
                                 "disposition": Disposition.NEUTRAL, **kw}
    return assess_social(**fields)  # type: ignore[arg-type]


# ============================================================
#  区间
# ============================================================
@pytest.mark.parametrize(
    ("at_stake", "admissible", "canonical"),
    [
        (assess(Attitude.TRUSTED), (S.GRANTED, S.SOFTENED), S.GRANTED),
        (assess(Attitude.FRIENDLY), (S.GRANTED, S.SOFTENED, S.NOTHING), S.SOFTENED),
        (assess(Attitude.NEUTRAL), (S.SOFTENED, S.NOTHING, S.REBUFFED), S.NOTHING),
        (assess(Attitude.WARY), (S.NOTHING, S.REBUFFED), S.REBUFFED),
        (assess(Attitude.HOSTILE), (S.REBUFFED,), S.REBUFFED),  # 言辞不激人翻脸
        (assess(Attitude.WARY, P.GUILE), (S.NOTHING, S.REBUFFED, S.FALLOUT), S.REBUFFED),
        (assess(Attitude.HOSTILE, P.GUILE), (S.REBUFFED, S.FALLOUT), S.REBUFFED),
        (assess(Attitude.TRUSTED, P.FORCE, edge=-1), (S.NOTHING, S.REBUFFED, S.FALLOUT), S.REBUFFED),  # 威逼看实力不看交情
        (assess(Attitude.HOSTILE, P.FORCE, edge=2), (S.GRANTED, S.SOFTENED), S.GRANTED),
        (assess(Attitude.NEUTRAL, P.FORCE, edge=-2, disposition=Disposition.RUTHLESS), (S.REBUFFED, S.FALLOUT), S.FALLOUT),
        (assess(Attitude.NEUTRAL, P.FORCE, edge=-2, disposition=Disposition.MERCIFUL), (S.NOTHING, S.REBUFFED), S.REBUFFED),  # 仁厚不翻脸
        (assess(Attitude.NEUTRAL, P.FORCE, edge=-1, subdued=True), (S.GRANTED, S.SOFTENED, S.NOTHING), S.SOFTENED),  # 被制住的人好说话
        (assess(Attitude.HOSTILE, P.LEVERAGE), (S.REBUFFED, S.FALLOUT), S.REBUFFED),  # 无势可借：虚张声势
        (assess(Attitude.NEUTRAL, P.LEVERAGE, leverage_ids=("chr:a", "fact:b", "fact:c")), (S.GRANTED, S.SOFTENED), S.GRANTED),
        (assess(Attitude.FRIENDLY, difficulty=2), (S.NOTHING, S.REBUFFED), S.REBUFFED),
        (assess(Attitude.FRIENDLY, repeated=True), (S.SOFTENED, S.NOTHING, S.REBUFFED), S.NOTHING),
        (assess(Attitude.TRUSTED, reachable=False), (S.SOFTENED,), S.SOFTENED),  # 够不着：无如愿，确定性裁决落到最近一格
        (assess(Attitude.FRIENDLY, reachable=False), (S.SOFTENED, S.NOTHING), S.SOFTENED),
    ],
)
def test_the_social_envelope(at_stake: SocialStakes, admissible: tuple[SocialOutcome, ...], canonical: SocialOutcome) -> None:
    assert (at_stake.admissible, at_stake.canonical) == (admissible, canonical)
    assert at_stake.contested is (len(admissible) > 1) and at_stake.target_id == "chr:x"
    order = list(SocialOutcome)
    indices = [order.index(o) for o in at_stake.admissible]
    assert indices == sorted(indices) and indices == list(range(indices[0], indices[-1] + 1))  # 连续切片，由好到坏


def test_settle_takes_only_outcomes_inside_the_rails() -> None:
    at_stake = assess(Attitude.NEUTRAL)
    assert settle_social(at_stake, S.SOFTENED) == SocialRuling(S.SOFTENED, adopted=True)
    assert settle_social(at_stake, S.GRANTED) == SocialRuling(S.NOTHING, adopted=False)  # 出界取确定性裁决
    assert settle_social(at_stake, CombatOutcome.SUCCESS) == SocialRuling(S.NOTHING, adopted=False)
    assert settle_social(at_stake, CovertOutcome.CLEAN) == settle_social(at_stake, None)


# ============================================================
#  效果：阶梯每次至多一档、止于友善、翻脸直落敌视、永不伤人
# ============================================================
def regard_after(at_stake: SocialStakes, outcome: SocialOutcome) -> Attitude:
    events = effects(at_stake, SocialRuling(outcome, adopted=True), PID)
    assert isinstance(events[0], Parleyed) and events[0].outcome is outcome
    assert not any(isinstance(e, HealthChanged | PlayerDied) for e in events)
    changed = [e for e in events if isinstance(e, RelationChanged)]
    return changed[-1].attitude if changed else at_stake.attitude


@pytest.mark.parametrize(
    ("attitude", "approach", "aim", "outcome", "after"),
    [
        (Attitude.NEUTRAL, P.WORDS, Aim.BEFRIEND, S.GRANTED, Attitude.FRIENDLY),
        (Attitude.NEUTRAL, P.FAVOR, Aim.BEFRIEND, S.SOFTENED, Attitude.FRIENDLY),
        (Attitude.FRIENDLY, P.WORDS, Aim.BEFRIEND, S.GRANTED, Attitude.FRIENDLY),  # 言辞止于友善
        (Attitude.TRUSTED, P.WORDS, Aim.BEFRIEND, S.GRANTED, Attitude.TRUSTED),  # 也从不把信赖之人往下拉
        (Attitude.HOSTILE, P.WORDS, Aim.DEFUSE, S.GRANTED, Attitude.WARY),  # 一次只一档
        (Attitude.HOSTILE, P.LEVERAGE, Aim.DEFUSE, S.SOFTENED, Attitude.WARY),
        (Attitude.NEUTRAL, P.GUILE, Aim.BEFRIEND, S.SOFTENED, Attitude.NEUTRAL),  # 诡计松动只换来口风
        (Attitude.NEUTRAL, P.FORCE, Aim.BEFRIEND, S.GRANTED, Attitude.WARY),  # 威逼得逞：畏而不服
        (Attitude.WARY, P.FORCE, Aim.BEFRIEND, S.GRANTED, Attitude.WARY),  # 不低于戒备
        (Attitude.NEUTRAL, P.WORDS, Aim.PROBE, S.GRANTED, Attitude.NEUTRAL),  # 打探如愿换来的是见闻，不是交情
        (Attitude.FRIENDLY, P.WORDS, Aim.BEFRIEND, S.NOTHING, Attitude.FRIENDLY),
        (Attitude.FRIENDLY, P.WORDS, Aim.BEFRIEND, S.REBUFFED, Attitude.FRIENDLY),  # 碰壁人情不变
        (Attitude.FRIENDLY, P.FORCE, Aim.BEFRIEND, S.FALLOUT, Attitude.HOSTILE),  # 翻脸直落敌视
    ],
)
def test_the_ladder_moves_one_step_at_most(
    attitude: Attitude, approach: Approach, aim: Aim, outcome: SocialOutcome, after: Attitude
) -> None:
    at_stake = assess_social(npc_id="chr:x", aim=aim, approach=approach, attitude=attitude, disposition=Disposition.NEUTRAL)
    assert regard_after(at_stake, outcome) is after


def test_a_plea_to_learn_never_teaches() -> None:
    """求艺：如愿恰够上门槛（友善）；松动至多到门槛下一档；从不产出 SkillPracticed。"""
    plea = assess_social(npc_id="chr:x", aim=Aim.LEARN, approach=P.WORDS, attitude=Attitude.NEUTRAL,
                         disposition=Disposition.NEUTRAL, subject_id="art:x", need=Attitude.FRIENDLY)
    assert regard_after(plea, S.GRANTED) is Attitude.FRIENDLY
    assert regard_after(plea, S.SOFTENED) is Attitude.NEUTRAL
    lofty = assess_social(npc_id="chr:x", aim=Aim.LEARN, approach=P.WORDS, attitude=Attitude.NEUTRAL,
                          disposition=Disposition.NEUTRAL, need=Attitude.TRUSTED)
    assert regard_after(lofty, S.SOFTENED) is Attitude.FRIENDLY
    for outcome in S:
        assert not any(isinstance(e, SkillPracticed) for e in effects(plea, SocialRuling(outcome, True), PID))


# ============================================================
#  经 rules 端到端
# ============================================================
async def test_kind_words_win_over_the_merciful() -> None:
    state, snap = await scene("loc:大理城")
    hello = act(ActionType.TALK, target_entity="段誉", approach=P.WORDS)
    at_stake = stakes(hello, state, snap)
    assert isinstance(at_stake, SocialStakes) and at_stake.aim is Aim.BEFRIEND and at_stake.score == 1  # 漠然 0 + 仁厚 1
    assert decide(hello, state, snap) == [
        Parleyed(npc_id="chr:段誉", aim=Aim.BEFRIEND, approach=P.WORDS, outcome=S.SOFTENED),
        RelationChanged(character_id="chr:段誉", attitude=Attitude.FRIENDLY, cause="为你言辞所动", basis="松动"),
    ]
    assert decide(hello, state, snap, Proposal(S.NOTHING)) == [
        Parleyed(npc_id="chr:段誉", aim=Aim.BEFRIEND, approach=P.WORDS, outcome=S.NOTHING)
    ]


def with_facts(snap: LocalSnapshot) -> LocalSnapshot:
    facts = (
        FactView(id="fact:比剑", text="东西二宗五年一比剑", subject_ids=("chr:左子穆", "chr:辛双清"), knower_ids=("chr:左子穆",)),
        FactView(id="fact:剑法", text="左子穆剑法得自师门", subject_ids=("chr:左子穆",), knower_ids=("chr:左子穆",),
                 unlock=FactUnlock(kind="TEACHING", target_id="art:无量剑法")),
        FactView(id="fact:悬空", text="一桩落不了地的事", subject_ids=("chr:左子穆",), knower_ids=("chr:左子穆",),
                 unlock=FactUnlock(kind="TEACHING", target_id="art:不存在")),
        FactView(id="fact:西宗", text="西宗另有隐秘", subject_ids=("chr:辛双清",), knower_ids=("chr:辛双清",)),
    )
    labels = {**snap.labels, **{f.id: f.text for f in facts}}
    return snap.model_copy(update={"facts": facts, "labels": labels})


async def test_probing_learns_only_what_the_present_knower_knows() -> None:
    friend = RelationChanged(character_id="chr:左子穆", attitude=Attitude.FRIENDLY, cause="c")
    state, snap = await scene("loc:无量山", friend)
    snap = with_facts(snap)
    ask = act(ActionType.TALK, target_entity="左子穆", approach=P.WORDS, topic="辛双清")
    at_stake = stakes(ask, state, snap)
    assert isinstance(at_stake, SocialStakes) and at_stake.aim is Aim.PROBE and at_stake.subject_id == "fact:比剑"
    events = decide(ask, state, snap, Proposal(S.GRANTED))
    assert events == [
        Parleyed(npc_id="chr:左子穆", aim=Aim.PROBE, approach=P.WORDS, outcome=S.GRANTED, subject_id="fact:比剑"),
        FactLearned(fact_id="fact:比剑", source_id="chr:左子穆"),
    ]  # 辛双清的「西宗另有隐秘」不是左子穆所知；话题收窄到牵涉辛双清的那几条
    state, snap = await scene("loc:无量山", friend, FactLearned(fact_id="fact:比剑", source_id="chr:左子穆"))
    snap = with_facts(snap)
    again = stakes(act(ActionType.TALK, target_entity="左子穆", approach=P.WORDS, aim=Aim.PROBE), state, snap)
    assert isinstance(again, SocialStakes) and again.subject_id == "fact:剑法"  # 已知的不再学；unlock 落不了地的永不入选
    told = stakes(ask, state, snap)
    assert isinstance(told, SocialStakes) and told.subject_id is None and S.GRANTED not in told.admissible


async def test_asking_for_an_item_hands_it_over_only_when_granted() -> None:
    trusted = RelationChanged(character_id="chr:左子穆", attitude=Attitude.TRUSTED, cause="c")
    state, snap = await scene("loc:无量山", trusted)
    beg = act(ActionType.TAKE, target_entity="无量剑", approach=P.WORDS)
    at_stake = stakes(beg, state, snap)
    assert isinstance(at_stake, SocialStakes) and at_stake.aim is Aim.ASK and at_stake.score == 1  # 信赖 2 − 物主所珍 1
    assert decide(beg, state, snap, Proposal(S.GRANTED)) == [
        Parleyed(npc_id="chr:左子穆", aim=Aim.ASK, approach=P.WORDS, outcome=S.GRANTED, subject_id="itm:无量剑"),
        ItemTransferred(item_id="itm:无量剑", from_holder="chr:左子穆", to_holder=PID),
    ]
    assert not any(isinstance(e, ItemTransferred) for e in decide(beg, state, snap))  # 确定性裁决是松动


async def test_pleading_to_learn_parleys_and_never_teaches() -> None:
    """以言辞求艺遇 UNWILLING：改走交涉，产出 Parleyed（可附人情 +1），不传功；如愿之后寻常再求，才走修习的两道门。"""
    state, snap = await scene("loc:无量山")
    plea = act(ActionType.LEARN, skill_used="无量剑法", target_entity="辛双清", approach=P.WORDS)
    at_stake = stakes(plea, state, snap)
    assert isinstance(at_stake, SocialStakes) and (at_stake.npc_id, at_stake.aim, at_stake.need) == (
        "chr:辛双清", Aim.LEARN, Attitude.FRIENDLY)
    assert decide(plea, state, snap) == [
        Parleyed(npc_id="chr:辛双清", aim=Aim.LEARN, approach=P.WORDS, outcome=S.NOTHING, subject_id="art:无量剑法")
    ]
    kind = recast(snap, "chr:辛双清", disposition=Disposition.MERCIFUL)
    events = decide(plea, state, kind, Proposal(S.GRANTED))
    assert events == [
        Parleyed(npc_id="chr:辛双清", aim=Aim.LEARN, approach=P.WORDS, outcome=S.GRANTED, subject_id="art:无量剑法"),
        RelationChanged(character_id="chr:辛双清", attitude=Attitude.FRIENDLY, cause="为你言辞所动", basis="如愿"),
    ]
    state, snap = await scene("loc:无量山", *events)
    assert decide(act(ActionType.LEARN, skill_used="无量剑法", target_entity="辛双清"), state, snap) == [
        SkillPracticed(skill_id="art:无量剑法", proficiency_gained=10, source_id="chr:辛双清")
    ]


async def test_arts_above_third_rate_can_never_be_pleaded_into() -> None:
    friend = RelationChanged(character_id="chr:段正淳", attitude=Attitude.FRIENDLY, cause="c")
    state, snap = await scene("loc:大理城", friend, ROOTED)
    plea = act(ActionType.LEARN, skill_used="一阳指", target_entity="段正淳", approach=P.FAVOR)
    at_stake = stakes(plea, state, snap)
    assert isinstance(at_stake, SocialStakes) and at_stake.need is Attitude.TRUSTED and S.GRANTED not in at_stake.admissible
    for outcome in S:
        events = decide(plea, state, snap, Proposal(outcome))
        assert not any(isinstance(e, SkillPracticed) for e in events)
        assert all(e.attitude is not Attitude.TRUSTED for e in events if isinstance(e, RelationChanged))


async def test_a_subdued_man_hands_over_his_sword_under_threat() -> None:
    win = SkillExecuted(skill_id="art:北冥神功", target_id="chr:左子穆", outcome=CombatOutcome.SUCCESS)
    state, snap = await scene("loc:无量山", ROOTED, win)
    threat = act(ActionType.TALK, target_entity="左子穆", approach=P.FORCE, aim=Aim.ASK, topic="无量剑")
    at_stake = stakes(threat, state, snap)
    assert isinstance(at_stake, SocialStakes) and at_stake.admissible == (S.GRANTED, S.SOFTENED)
    assert decide(threat, state, snap) == [
        Parleyed(npc_id="chr:左子穆", aim=Aim.ASK, approach=P.FORCE, outcome=S.GRANTED, subject_id="itm:无量剑"),
        ItemTransferred(item_id="itm:无量剑", from_holder="chr:左子穆", to_holder=PID),
        RelationChanged(character_id="chr:左子穆", attitude=Attitude.WARY, cause="被你威逼，畏而不服", basis="如愿"),
    ]


async def test_leverage_comes_from_present_patrons_and_known_facts() -> None:
    friend = RelationChanged(character_id="chr:左子穆", attitude=Attitude.FRIENDLY, cause="c")
    state, snap = await scene("loc:无量山", friend, FactLearned(fact_id="fact:把柄", source_id="chr:辛双清"))
    hold = FactView(id="fact:把柄", text="龚光杰曾输给西宗", subject_ids=("chr:龚光杰",), knower_ids=("chr:辛双清",),
                    unlock=FactUnlock(kind="LEVERAGE", target_id="chr:龚光杰"), known=True)
    snap = snap.model_copy(update={"facts": (hold,)})
    lean = act(ActionType.TALK, target_entity="龚光杰", approach=P.LEVERAGE)
    at_stake = stakes(lean, state, snap)
    assert isinstance(at_stake, SocialStakes) and at_stake.leverage_ids == ("chr:左子穆", "fact:把柄")  # 师父在侧、手握把柄
    assert decide(lean, state, snap)[0] == Parleyed(
        npc_id="chr:龚光杰", aim=Aim.BEFRIEND, approach=P.LEVERAGE, outcome=at_stake.canonical, leverage_ids=("chr:左子穆", "fact:把柄"))
    words = stakes(act(ActionType.TALK, target_entity="龚光杰", approach=P.WORDS), state, snap)
    assert isinstance(words, SocialStakes) and words.leverage_ids == ()  # 言辞只吃心事（MOTIVE），不吃把柄与靠山


async def test_pestering_with_the_same_approach_only_makes_it_worse() -> None:
    state, snap = await scene("loc:大理城")
    hello = act(ActionType.TALK, target_entity="段誉", approach=P.WORDS)
    first = stakes(hello, state, snap)
    rebuffed = Parleyed(npc_id="chr:段誉", aim=Aim.BEFRIEND, approach=P.WORDS, outcome=S.REBUFFED)
    state, snap = await scene("loc:大理城", rebuffed)
    again, other = stakes(hello, state, snap), stakes(act(ActionType.TALK, target_entity="段誉", approach=P.FAVOR), state, snap)
    assert isinstance(first, SocialStakes) and isinstance(again, SocialStakes) and isinstance(other, SocialStakes)
    assert again.score == first.score - 1 and other.score == first.score  # 换个手段就不算纠缠


async def test_leverage_learned_from_one_man_works_on_another_when_the_informant_is_gone() -> None:
    """从辛双清处听来的把柄，辛双清不在场照样能拿来压龚光杰：筹码只认快照里 known=True 的见闻（图谱把主体在场的已知见闻带进来）。"""
    learned = FactLearned(fact_id="fact:把柄", source_id="chr:辛双清")
    state, snap = await scene("loc:无量山", learned)
    hold = FactView(id="fact:把柄", text="龚光杰曾输给西宗", subject_ids=("chr:龚光杰",), knower_ids=("chr:辛双清",),
                    unlock=FactUnlock(kind="LEVERAGE", target_id="chr:龚光杰"), known=True)
    gone = tuple(c for c in snap.characters if c.id != "chr:辛双清")
    absent = snap.model_copy(update={"characters": gone, "facts": (hold,), "labels": {**snap.labels, hold.id: hold.text}})
    for approach in (P.LEVERAGE, P.FORCE):
        at_stake = stakes(act(ActionType.TALK, target_entity="龚光杰", approach=approach), state, absent)
        assert isinstance(at_stake, SocialStakes) and "fact:把柄" in at_stake.leverage_ids
    rumour = absent.model_copy(update={"facts": (hold.model_copy(update={"known": False}),)})
    unknown = stakes(act(ActionType.TALK, target_entity="龚光杰", approach=P.LEVERAGE), state, rumour)
    assert isinstance(unknown, SocialStakes) and "fact:把柄" not in unknown.leverage_ids  # 还不知道的事作不了筹码


async def test_probing_only_draws_on_what_you_do_not_know_yet() -> None:
    """打探只认 known=False 且此人正是知情人的见闻：图谱标了已知的那条不再入选。"""
    friend = RelationChanged(character_id="chr:左子穆", attitude=Attitude.FRIENDLY, cause="c")
    state, snap = await scene("loc:无量山", friend)
    snap = with_facts(snap)
    marked = tuple(f.model_copy(update={"known": f.id == "fact:比剑"}) for f in snap.facts)
    at_stake = stakes(act(ActionType.TALK, target_entity="左子穆", approach=P.WORDS, aim=Aim.PROBE), state,
                      snap.model_copy(update={"facts": marked}))
    assert isinstance(at_stake, SocialStakes) and at_stake.subject_id == "fact:剑法"


async def test_sweet_words_cannot_seize_an_item_the_cell_names_the_aim() -> None:
    """取他人之物以格子为准：好言相求而说要「夺」，照样是讨要（物主所珍难度照算），如愿即交到你手上；暗中下手说要「讨」，照样是夺物。"""
    friend = RelationChanged(character_id="chr:左子穆", attitude=Attitude.FRIENDLY, cause="c")
    state, snap = await scene("loc:无量山", friend)
    snap = recast(snap, "chr:左子穆", disposition=Disposition.MERCIFUL)
    grab = act(ActionType.TAKE, target_entity="无量剑", approach=P.WORDS, aim=Aim.SEIZE)
    at_stake = stakes(grab, state, snap)
    assert isinstance(at_stake, SocialStakes) and at_stake.aim is Aim.ASK and at_stake.subject_id == "itm:无量剑"
    assert at_stake.score == 1  # 友善 1 + 仁厚 1 − 物主所珍 1
    assert decide(grab, state, snap, Proposal(S.GRANTED)) == [
        Parleyed(npc_id="chr:左子穆", aim=Aim.ASK, approach=P.WORDS, outcome=S.GRANTED, subject_id="itm:无量剑"),
        ItemTransferred(item_id="itm:无量剑", from_holder="chr:左子穆", to_holder=PID),
    ]
    sneak = act(ActionType.TAKE, target_entity="无量剑", approach=P.STEALTH, aim=Aim.ASK)
    assert normalized(sneak, state, snap).aim is Aim.SEIZE


async def test_a_threat_buys_neither_friendship_nor_teaching() -> None:
    """威逼（TALK×武力）图不来结交、化解、求艺：一律改为打探；无可打探之事则区间里没有如愿——不会出现「如愿结交」而人情反降。"""
    state, snap = await scene("loc:无量山", ROOTED)  # 二流压三流的辛双清
    for aim, topic in ((None, None), (Aim.BEFRIEND, None), (Aim.DEFUSE, None), (Aim.LEARN, "无量剑法")):
        threat = act(ActionType.TALK, target_entity="辛双清", approach=P.FORCE, aim=aim, topic=topic)
        assert normalized(threat, state, snap).aim in (None, Aim.PROBE)
        at_stake = stakes(threat, state, snap)
        assert isinstance(at_stake, SocialStakes) and at_stake.aim is Aim.PROBE and S.GRANTED not in at_stake.admissible
        for outcome in S:
            events = decide(threat, state, snap, Proposal(outcome))
            assert not any(isinstance(e, SkillPracticed | FactLearned) for e in events)
            assert all(e.outcome is not S.GRANTED for e in events if isinstance(e, Parleyed))
    state, snap = await scene("loc:无量山", ROOTED)
    snap = with_facts(snap)  # 左子穆知道些事：威逼得逞是吐出一件见闻，人却畏而不服
    events = decide(act(ActionType.TALK, target_entity="左子穆", approach=P.FORCE), state, snap, Proposal(S.GRANTED))
    assert events == [
        Parleyed(npc_id="chr:左子穆", aim=Aim.PROBE, approach=P.FORCE, outcome=S.GRANTED, subject_id="fact:比剑"),
        FactLearned(fact_id="fact:比剑", source_id="chr:左子穆"),
        RelationChanged(character_id="chr:左子穆", attitude=Attitude.WARY, cause="被你威逼，畏而不服", basis="如愿"),
    ]
