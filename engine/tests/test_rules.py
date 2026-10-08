"""
[INPUT]: 依赖 app.domain.rules 的 decide / adjudicate / duel，依赖 app.domain.aggregates 的 Player，依赖 InMemoryWorldGraph 生成快照，依赖 tests/world 的 WORLD
[OUTPUT]: 裁决规则的单测：每种动作的放行与驳回、对决矩阵、人情涟漪、修习门槛的逐条核验、天降神兵此路不通
[POS]: tests 的逻辑死线：能力成长、物品获取、人际变化只能由图谱拓扑推导——这里逐条钉死
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.domain.aggregates import Player, PlayerState
from app.domain.events import (
    ActionFailed,
    CombatOutcome,
    DomainEvent,
    EventEnvelope,
    ItemTransferred,
    Moved,
    PlayerDied,
    PlayerSpawned,
    RelationChanged,
    SkillExecuted,
    SkillLearned,
)
from app.domain.intent import ActionType, PlayerIntent
from app.domain.models import Attitude, Disposition, Tier
from app.domain.rules import Approval, Rejection, adjudicate, decide, duel, resolve
from app.domain.snapshot import LocalSnapshot
from app.infrastructure.persistence.memory_graph import InMemoryWorldGraph
from tests.world import WORLD

PID = "ply:rules"


async def scene(at: str, *events: DomainEvent) -> tuple[PlayerState, LocalSnapshot]:
    history = [PlayerSpawned(player_id=PID, name="阿星", location_id=at), *events]
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
#  对决
# ============================================================
@pytest.mark.parametrize(
    ("attacker", "defender", "disposition", "outcome"),
    [
        (Tier.FIRST, Tier.THIRD, Disposition.NEUTRAL, CombatOutcome.PREVAILED),
        (Tier.FIRST, Tier.FIRST, Disposition.RUTHLESS, CombatOutcome.STALEMATE),
        (Tier.NONE, Tier.THIRD, Disposition.NEUTRAL, CombatOutcome.REPELLED),
        (Tier.NONE, Tier.THIRD, Disposition.RUTHLESS, CombatOutcome.FATAL),
        (Tier.NONE, Tier.PEERLESS, Disposition.NEUTRAL, CombatOutcome.FATAL),
        (Tier.NONE, Tier.FIRST, Disposition.MERCIFUL, CombatOutcome.REPELLED),
    ],
)
def test_duel_matrix(attacker: Tier, defender: Tier, disposition: Disposition, outcome: CombatOutcome) -> None:
    assert duel(attacker, defender, disposition) is outcome


async def test_offending_a_grandmaster_is_fatal() -> None:
    state, snap = await scene("loc:无锡城")
    events = decide(act(ActionType.ATTACK, target_entity="乔峰"), state, snap)
    assert isinstance(events[0], SkillExecuted) and events[0].outcome is CombatOutcome.FATAL
    assert events[1] == PlayerDied(cause="冒犯乔峰，当场毙命", killer_id="chr:乔峰")


async def test_attack_ripples_along_has_relation_to_witnesses_only() -> None:
    state, snap = await scene("loc:无量山")
    events = decide(act(ActionType.ATTACK, target_entity="左子穆"), state, snap)
    assert events[0] == SkillExecuted(skill_id=None, target_id="chr:左子穆", outcome=CombatOutcome.REPELLED)
    changes = {e.character_id: e.attitude for e in events if isinstance(e, RelationChanged)}
    assert changes == {"chr:左子穆": Attitude.HOSTILE, "chr:辛双清": Attitude.FRIENDLY}  # 南海鳄神与之无关，不动


async def test_merciful_kin_turns_hostile_but_spares_you() -> None:
    state, snap = await scene("loc:大理城")
    events = decide(act(ActionType.ATTACK, target_entity="段正淳"), state, snap)
    assert events[0].outcome is CombatOutcome.REPELLED  # type: ignore[attr-defined]
    assert {e.character_id for e in events if isinstance(e, RelationChanged)} == {"chr:段正淳", "chr:段誉"}


async def test_weapons_do_not_change_tier_and_unknown_arts_are_refused() -> None:
    state, snap = await scene("loc:无量山", ItemTransferred(item_id="itm:玉佩", from_holder="loc:无量山", to_holder=PID))
    events = decide(act(ActionType.ATTACK, target_entity="左子穆", item_used="玉佩"), state, snap)
    assert events[0].outcome is CombatOutcome.REPELLED and events[0].item_id == "itm:玉佩"  # type: ignore[attr-defined]
    assert failure(decide(act(ActionType.ATTACK, target_entity="左子穆", skill_used="降龙十八掌"), state, snap)
                   ).reason_code == "NOT_KNOWN"
    assert failure(decide(act(ActionType.ATTACK, target_entity="左子穆", item_used="倚天剑"), state, snap)
                   ).reason_code == "NOT_CARRIED"


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
    win = SkillExecuted(skill_id="art:北冥神功", target_id="chr:左子穆", outcome=CombatOutcome.PREVAILED)
    state, snap = await scene("loc:无量山", SkillLearned(skill_id="art:北冥神功", source_id="itm:北冥神功卷轴"), win)
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
#  修习：逐条门槛
# ============================================================
SCROLL = ItemTransferred(item_id="itm:北冥神功卷轴", from_holder="loc:无量玉洞", to_holder=PID)


async def test_self_study_needs_text_place_and_order() -> None:
    state, snap = await scene("loc:无量玉洞")
    assert failure(decide(act(ActionType.LEARN, skill_used="北冥神功"), state, snap)).reason_code == "UNKNOWN_SKILL"
    state, snap = await scene("loc:无量玉洞", SCROLL)
    assert decide(act(ActionType.LEARN, skill_used="北冥神功"), state, snap) == [
        SkillLearned(skill_id="art:北冥神功", source_id="itm:北冥神功卷轴")
    ]
    assert failure(decide(act(ActionType.LEARN, skill_used="凌波微步"), state, snap)).reason_code == "MISSING_SKILL"
    away = Moved(from_location_id="loc:无量玉洞", to_location_id="loc:无量山", exit_label="攀上")
    state, snap = await scene("loc:无量玉洞", SCROLL, away)
    failed = failure(decide(act(ActionType.LEARN, skill_used="北冥神功"), state, snap))
    assert failed.reason_code == "WRONG_PLACE" and "无量玉洞" in failed.reason


async def test_conflicting_arts_and_already_known() -> None:
    state, snap = await scene("loc:无量玉洞", SCROLL)
    poisoned = replace(state, skills=frozenset({"art:化功大法"}))
    assert failure(decide(act(ActionType.LEARN, skill_used="北冥神功"), poisoned, snap)).reason_code == "CONFLICT"
    learned = replace(state, skills=frozenset({"art:北冥神功"}))
    assert failure(decide(act(ActionType.LEARN, skill_used="北冥神功"), learned, snap)).reason_code == "ALREADY_KNOWN"


async def test_a_teacher_must_be_present_willing_and_you_must_be_ready() -> None:
    state, snap = await scene("loc:无量山")
    assert failure(decide(act(ActionType.LEARN, skill_used="无量剑法"), state, snap)).reason_code == "UNWILLING"
    friend = RelationChanged(character_id="chr:辛双清", attitude=Attitude.FRIENDLY, cause="敌人之敌")
    state, snap = await scene("loc:无量山", friend)
    assert decide(act(ActionType.LEARN, skill_used="无量剑法", target_entity="左子穆"), state, snap) == [
        SkillLearned(skill_id="art:无量剑法", source_id="chr:辛双清")  # 点名的人不肯教，肯教的人在场
    ]
    trusted = RelationChanged(character_id="chr:段正淳", attitude=Attitude.FRIENDLY, cause="物归原主")
    state, snap = await scene("loc:大理城", trusted)
    failed = failure(decide(act(ActionType.LEARN, skill_used="一阳指"), state, snap))
    assert failed.reason_code == "TIER_TOO_LOW" and "二流" in failed.reason


async def test_no_heaven_sent_arts() -> None:
    """六脉神剑无人可教、无典可凭：此情此景根本无从得知——天降神兵此路不通。"""
    state, snap = await scene("loc:大理城")
    assert failure(decide(act(ActionType.LEARN, skill_used="六脉神剑"), state, snap)).reason_code == "UNKNOWN_SKILL"


async def test_invalid_never_passes() -> None:
    state, snap = await scene("loc:大理城")
    verdict = adjudicate(PlayerIntent.invalid("北宋没有手枪"), state, snap)
    assert verdict == Rejection("INVALID", "北宋没有手枪")
