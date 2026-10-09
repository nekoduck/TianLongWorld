"""
[INPUT]: 依赖 app.domain.threads 的 Thread / fold / pursuing / THREADS_MAX，依赖 app.domain.aggregates 的 Player，依赖 app.domain.events，
         依赖 app.domain.rules 的 decide / stakes，依赖 tests/test_rules 的 scene / act，读实录事件流 tests/fixtures/live_session_events.jsonl
[OUTPUT]: 心事线索的单测：交涉不如愿开启并累积已试手段（不重复）、尝试次数与所图的标的（subject_id）、如愿了结；暗中未得手开启、得手了结（连同以那件东西为所求的线索）；
          求教被拒（UNWILLING）按落了地的师父与武学立键、把 unlock 解析为所需人情且不计尝试次数——同一句驳回再来一遍线索与次序都原样不动；
          旧账的驳回（没有 target_id）不开线索（实录事件流重放后没有僵尸线索）；图的是一样东西却没有标的、结交 / 化解而人情早已达成都不开线索；
          标的到手、那门武学修习了一次（不论凭谁）、那条见闻已知、结交到友善 / 化解到漠然即了结；至多 THREADS_MAX 条、满了请走最旧的；
          经 rules 的回合 11：以言辞求艺开出一条带标的的线索，下一招就知道还有哪些手段没试；寻常求教被拒 → 结交到友善 → 学成，线索了结
[POS]: tests 的线索基线：线索只由事件折叠，重放即重算
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import json
from pathlib import Path

from app.domain.aggregates import Player, PlayerState
from app.domain.events import (
    ActionFailed,
    DomainEvent,
    FactLearned,
    ItemTransferred,
    Maneuvered,
    Parleyed,
    PlayerSpawned,
    RelationChanged,
    SkillPracticed,
    decode_event,
)
from app.domain.intent import ActionType, Aim, Approach
from app.domain.models import Attitude
from app.domain.outcomes import CovertOutcome, SocialOutcome
from app.domain.rules import decide, stakes
from app.domain.social import SocialStakes
from app.domain.stakes import Proposal
from app.domain.threads import THREADS_MAX, Thread, fold, pursuing
from tests.test_rules import PID, act, scene

S, C, P = SocialOutcome, CovertOutcome, Approach
ART = "art:无量剑法"
_SUBJECT = {Aim.LEARN: ART, Aim.ASK: "itm:无量剑", Aim.PROBE: "fact:比剑"}
SESSION = Path(__file__).parent / "fixtures" / "live_session_events.jsonl"


def replay(*events: DomainEvent) -> PlayerState:
    state = Player.replay([PlayerSpawned(player_id=PID, name="阿星", location_id="loc:无量山"), *events])
    assert state is not None
    return state


def parley(npc: str, outcome: SocialOutcome, approach: Approach = P.WORDS, aim: Aim = Aim.LEARN,
           subject: str | None = None) -> Parleyed:
    return Parleyed(npc_id=npc, aim=aim, approach=approach, outcome=outcome, subject_id=subject or _SUBJECT.get(aim))


def refusal(teacher: str | None = "chr:段正淳", art: str | None = "art:一阳指") -> ActionFailed:
    return ActionFailed(action=ActionType.LEARN, target="段正淳", reason_code="UNWILLING", reason="交情尚浅", unlock="信赖",
                        target_id=teacher, subject_id=art)


def test_a_parley_that_falls_short_opens_a_thread_and_success_closes_it() -> None:
    state = replay(parley("chr:辛双清", S.NOTHING), parley("chr:辛双清", S.SOFTENED, P.FAVOR),
                   parley("chr:辛双清", S.REBUFFED))
    assert state.threads == (Thread(target="chr:辛双清", aim=Aim.LEARN, subject=ART, tried=(P.WORDS, P.FAVOR),
                                    progress="碰壁", attempts=3),)
    assert replay(parley("chr:辛双清", S.SOFTENED), parley("chr:辛双清", S.GRANTED, P.FAVOR)).threads == ()
    assert state.attempts == {"chr:辛双清": 3} and state.recent_approaches == (P.WORDS, P.FAVOR, P.WORDS)


def test_a_foiled_theft_opens_a_thread_until_the_item_is_in_hand() -> None:
    foiled = Maneuvered(item_id="itm:无量剑", target_id="chr:左子穆", approach=P.STEALTH, outcome=C.FOILED)
    state = replay(foiled)
    assert state.threads == (Thread(target="chr:左子穆", aim=Aim.SEIZE, subject="itm:无量剑", tried=(P.STEALTH,),
                                    progress="未遂", attempts=1),)
    assert replay(foiled, Maneuvered(item_id="itm:无量剑", target_id="chr:左子穆", approach=P.GUILE,
                                     outcome=C.EXPOSED)).threads == ()
    begged = parley("chr:左子穆", S.SOFTENED, aim=Aim.ASK)
    handed = ItemTransferred(item_id="itm:无量剑", from_holder="chr:左子穆", to_holder=PID)
    assert len(replay(foiled, begged).threads) == 2 and replay(foiled, begged, handed).threads == ()  # 到手即了：不论经哪一路
    other = ItemTransferred(item_id="itm:玉佩", from_holder="chr:左子穆", to_holder=PID)
    assert len(replay(foiled, begged, other).threads) == 2  # 他给的是别的东西：所求未了


def test_a_refusal_names_the_regard_it_needs_and_repeating_it_changes_nothing() -> None:
    once = replay(refusal())
    assert once.threads == (Thread(target="chr:段正淳", aim=Aim.LEARN, subject="art:一阳指", tried=(P.PLAIN,),
                                   progress="UNWILLING", need=Attitude.TRUSTED),)
    assert replay(refusal(), refusal()) == once  # 世界不变：同一句驳回再来一遍，线索原样不动
    other = ActionFailed(action=ActionType.MOVE, target="少林寺", reason_code="NO_PATH", reason="无路")
    assert replay(other).threads == ()  # 只有不肯传功的驳回开线索


def test_a_repeated_refusal_does_not_reorder_the_threads() -> None:
    """同一句驳回夹着别的线索再来一遍：线索的次序也原样不动（世界不变，菜单逐字不变）。"""
    between = parley("chr:辛双清", S.NOTHING, aim=Aim.BEFRIEND)
    assert replay(refusal(), between, refusal()) == replay(refusal(), between)


def test_legacy_refusals_without_ids_open_no_thread() -> None:
    """旧账的驳回只有玩家的原话（target="木婉清"），没有落了地的 id：不开线索，免得开出一条永远了结不了的僵尸线索。"""
    assert replay(refusal(teacher=None, art=None)).threads == ()
    raw = [json.loads(line) for line in SESSION.read_text(encoding="utf-8").splitlines() if line.strip()]
    events = [decode_event({k: v for k, v in r.items() if k != "v"}) for r in raw]
    assert any(isinstance(e, ActionFailed) and e.reason_code == "UNWILLING" for e in events)
    state = Player.replay(events)
    assert state is not None and state.threads == ()


def test_threads_close_on_learning_the_art_from_anyone_and_on_reaching_the_goal() -> None:
    for source in ("chr:辛双清", "chr:左子穆", "itm:无量剑谱", None):  # 不论凭谁、凭什么学到手
        taught = SkillPracticed(skill_id=ART, proficiency_gained=10, source_id=source)
        assert replay(parley("chr:辛双清", S.NOTHING), taught).threads == ()
    other_art = SkillPracticed(skill_id="art:北冥神功", proficiency_gained=10, source_id="chr:辛双清")
    assert len(replay(parley("chr:辛双清", S.NOTHING), other_art).threads) == 1  # 他教的是别的功夫：所求未了
    befriend = parley("chr:段誉", S.NOTHING, aim=Aim.BEFRIEND)
    defuse = parley("chr:龚光杰", S.NOTHING, aim=Aim.DEFUSE)
    hostile = RelationChanged(character_id="chr:龚光杰", attitude=Attitude.HOSTILE, cause="c")
    warmed = RelationChanged(character_id="chr:段誉", attitude=Attitude.FRIENDLY, cause="c")
    calmed = RelationChanged(character_id="chr:龚光杰", attitude=Attitude.WARY, cause="c")
    left = replay(hostile, befriend, defuse, warmed, calmed).threads
    assert [t.key for t in left] == [("chr:龚光杰", Aim.DEFUSE)]  # 化解要到漠然才算了，戒备还不够
    neutral = RelationChanged(character_id="chr:龚光杰", attitude=Attitude.NEUTRAL, cause="c")
    assert replay(hostile, defuse, neutral).threads == ()


def test_a_probe_closes_once_the_fact_is_known() -> None:
    probed = parley("chr:左子穆", S.NOTHING, aim=Aim.PROBE)
    assert [t.subject for t in replay(probed).threads] == ["fact:比剑"]
    assert replay(probed, FactLearned(fact_id="fact:比剑", source_id="chr:辛双清")).threads == ()


def test_no_thread_without_a_subject_or_once_the_goal_is_met() -> None:
    """图的是一样东西却没有落了地的标的：无从追索；结交 / 化解而人情早已达成：无可再图——都不开线索。"""
    for aim in (Aim.PROBE, Aim.ASK, Aim.LEARN):
        assert replay(Parleyed(npc_id="chr:辛双清", aim=aim, approach=P.WORDS, outcome=S.NOTHING)).threads == ()
    friend = RelationChanged(character_id="chr:辛双清", attitude=Attitude.FRIENDLY, cause="c")
    trusted = RelationChanged(character_id="chr:辛双清", attitude=Attitude.TRUSTED, cause="c")
    for regard in (friend, trusted):
        assert replay(regard, parley("chr:辛双清", S.SOFTENED, aim=Aim.BEFRIEND)).threads == ()
        assert replay(regard, parley("chr:辛双清", S.NOTHING, aim=Aim.DEFUSE)).threads == ()
    assert replay(parley("chr:辛双清", S.NOTHING, aim=Aim.DEFUSE)).threads == ()  # 漠然即无怨可解
    assert len(replay(parley("chr:辛双清", S.NOTHING, aim=Aim.BEFRIEND)).threads) == 1  # 漠然而图结交：确有可图


async def test_chatting_up_a_friend_opens_no_pursuit() -> None:
    """已是友善再以言辞攀谈（所图推断为结交）：区间里没有如愿，但也不该留下一条永远了结不了的结交线索。"""
    friend = RelationChanged(character_id="chr:辛双清", attitude=Attitude.FRIENDLY, cause="c")
    state, snap = await scene("loc:无量山", friend)
    talk = act(ActionType.TALK, target_entity="辛双清", approach=P.WORDS)
    at_stake = stakes(talk, state, snap)
    assert isinstance(at_stake, SocialStakes) and at_stake.aim is Aim.BEFRIEND
    for outcome in at_stake.admissible:
        assert replay(friend, *decide(talk, state, snap, Proposal(outcome))).threads == ()


def test_at_most_six_threads_newest_first() -> None:
    state = replay(*(parley(f"chr:路人{n}", S.NOTHING, aim=Aim.BEFRIEND) for n in range(THREADS_MAX + 2)))
    assert len(state.threads) == THREADS_MAX
    assert state.threads[0].target == "chr:路人7" and pursuing(state.threads, "chr:路人1", Aim.BEFRIEND) is None
    touched = fold(state.threads, parley("chr:路人3", S.REBUFFED, aim=Aim.BEFRIEND), PID)
    assert touched[0].key == ("chr:路人3", Aim.BEFRIEND) and touched[0].attempts == 2


async def test_turn_eleven_a_plea_opens_a_thread_and_shows_what_is_left_to_try() -> None:
    """回合 11 的复现：以言辞向不肯传功的人求艺 → 走交涉、开出一条带标的的线索；下一招就知道言辞已试过。"""
    state, snap = await scene("loc:无量山")
    events = decide(act(ActionType.LEARN, skill_used="无量剑法", target_entity="辛双清", approach=P.WORDS), state, snap)
    after = replay(*events)
    thread = pursuing(after.threads, "chr:辛双清", Aim.LEARN)
    assert thread is not None and thread.tried == (P.WORDS,) and thread.progress == "无果"
    assert thread.subject == ART and thread.need is None  # 标的随 Parleyed.subject_id 入账：状态栏写得出「求艺 · 无量剑法」
    untried = [a for a in (P.WORDS, P.FAVOR) if a not in thread.tried]
    assert untried == [P.FAVOR]


async def test_a_plain_refusal_keys_on_ids_and_closes_once_learned() -> None:
    """寻常求教被拒 → 线索按落了地的师父与武学立键；结交到友善、学成之后线索了结（不再有 '辛双清' 与 'chr:辛双清' 对不上的僵尸）。"""
    state, snap = await scene("loc:无量山")
    refused = decide(act(ActionType.LEARN, skill_used="无量剑法", target_entity="辛双清"), state, snap)
    assert isinstance(refused[0], ActionFailed) and (refused[0].target_id, refused[0].subject_id) == ("chr:辛双清", ART)
    opened = replay(*refused)
    assert [(t.target, t.subject, t.need) for t in opened.threads] == [("chr:辛双清", ART, Attitude.FRIENDLY)]
    friend = RelationChanged(character_id="chr:辛双清", attitude=Attitude.FRIENDLY, cause="c")
    state, snap = await scene("loc:无量山", *refused, friend)
    learned = decide(act(ActionType.LEARN, skill_used="无量剑法", target_entity="辛双清"), state, snap)
    assert isinstance(learned[0], SkillPracticed)
    assert replay(*refused, friend, *learned).threads == ()
