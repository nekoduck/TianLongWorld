"""
[INPUT]: 依赖 app.domain.threads 的 Thread / fold / pursuing / THREADS_MAX，依赖 app.domain.aggregates 的 Player，依赖 app.domain.events，
         依赖 app.domain.rules 的 decide，依赖 tests/test_rules 的 scene / act
[OUTPUT]: 心事线索的单测：交涉不如愿开启并累积已试手段（不重复）与尝试次数、如愿了结；暗中未得手开启、得手了结（连同以那件东西为所求的线索）；
          求教被拒（UNWILLING）把 unlock 解析为所需人情且不计尝试次数——同一句驳回再来一遍线索原样不动；物已到手、得其点拨、
          结交到友善 / 化解到漠然即了结；至多 THREADS_MAX 条、满了请走最旧的；经 rules 的回合 11：以言辞求艺开出一条线索，下一招就知道还有哪些手段没试
[POS]: tests 的线索基线：线索只由事件折叠，重放即重算
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from app.domain.aggregates import Player, PlayerState
from app.domain.events import (
    ActionFailed,
    DomainEvent,
    ItemTransferred,
    Maneuvered,
    Parleyed,
    PlayerSpawned,
    RelationChanged,
    SkillPracticed,
)
from app.domain.intent import ActionType, Aim, Approach
from app.domain.models import Attitude
from app.domain.outcomes import CovertOutcome, SocialOutcome
from app.domain.rules import decide
from app.domain.threads import THREADS_MAX, Thread, fold, pursuing
from tests.test_rules import PID, act, scene

S, C, P = SocialOutcome, CovertOutcome, Approach


def replay(*events: DomainEvent) -> PlayerState:
    state = Player.replay([PlayerSpawned(player_id=PID, name="阿星", location_id="loc:无量山"), *events])
    assert state is not None
    return state


def parley(npc: str, outcome: SocialOutcome, approach: Approach = P.WORDS, aim: Aim = Aim.LEARN) -> Parleyed:
    return Parleyed(npc_id=npc, aim=aim, approach=approach, outcome=outcome)


def test_a_parley_that_falls_short_opens_a_thread_and_success_closes_it() -> None:
    state = replay(parley("chr:辛双清", S.NOTHING), parley("chr:辛双清", S.SOFTENED, P.FAVOR),
                   parley("chr:辛双清", S.REBUFFED))
    assert state.threads == (Thread(target="chr:辛双清", aim=Aim.LEARN, tried=(P.WORDS, P.FAVOR), progress="碰壁", attempts=3),)
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


def test_a_refusal_names_the_regard_it_needs_and_repeating_it_changes_nothing() -> None:
    refused = ActionFailed(action=ActionType.LEARN, target="段正淳", reason_code="UNWILLING", reason="交情尚浅", unlock="信赖")
    once = replay(refused)
    assert once.threads == (Thread(target="段正淳", aim=Aim.LEARN, tried=(P.PLAIN,), progress="UNWILLING",
                                   need=Attitude.TRUSTED),)
    assert replay(refused, refused) == once  # 世界不变：同一句驳回再来一遍，线索原样不动
    other = ActionFailed(action=ActionType.MOVE, target="少林寺", reason_code="NO_PATH", reason="无路")
    assert replay(other).threads == ()  # 只有不肯传功的驳回开线索


def test_threads_close_on_teaching_and_on_reaching_the_goal() -> None:
    taught = SkillPracticed(skill_id="art:无量剑法", proficiency_gained=10, source_id="chr:辛双清")
    assert replay(parley("chr:辛双清", S.NOTHING), taught).threads == ()
    befriend = parley("chr:段誉", S.NOTHING, aim=Aim.BEFRIEND)
    defuse = parley("chr:龚光杰", S.NOTHING, aim=Aim.DEFUSE)
    warmed = RelationChanged(character_id="chr:段誉", attitude=Attitude.FRIENDLY, cause="c")
    calmed = RelationChanged(character_id="chr:龚光杰", attitude=Attitude.WARY, cause="c")
    left = replay(befriend, defuse, warmed, calmed).threads
    assert [t.key for t in left] == [("chr:龚光杰", Aim.DEFUSE)]  # 化解要到漠然才算了，戒备还不够
    assert replay(defuse, RelationChanged(character_id="chr:龚光杰", attitude=Attitude.NEUTRAL, cause="c")).threads == ()


def test_at_most_six_threads_newest_first() -> None:
    state = replay(*(parley(f"chr:路人{n}", S.NOTHING, aim=Aim.BEFRIEND) for n in range(THREADS_MAX + 2)))
    assert len(state.threads) == THREADS_MAX
    assert state.threads[0].target == "chr:路人7" and pursuing(state.threads, "chr:路人1", Aim.BEFRIEND) is None
    touched = fold(state.threads, parley("chr:路人3", S.REBUFFED, aim=Aim.BEFRIEND), PID)
    assert touched[0].key == ("chr:路人3", Aim.BEFRIEND) and touched[0].attempts == 2


async def test_turn_eleven_a_plea_opens_a_thread_and_shows_what_is_left_to_try() -> None:
    """回合 11 的复现：以言辞向不肯传功的人求艺 → 走交涉、开出一条线索；下一招就知道言辞已试过。"""
    state, snap = await scene("loc:无量山")
    events = decide(act(ActionType.LEARN, skill_used="无量剑法", target_entity="辛双清", approach=P.WORDS), state, snap)
    after = replay(*events)
    thread = pursuing(after.threads, "chr:辛双清", Aim.LEARN)
    assert thread is not None and thread.tried == (P.WORDS,) and thread.progress == "无果"
    untried = [a for a in (P.WORDS, P.FAVOR) if a not in thread.tried]
    assert untried == [P.FAVOR]
