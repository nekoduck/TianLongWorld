"""
[INPUT]: 依赖 app.domain 的 models / events / aggregates，依赖 tests/world 的 WORLD
[OUTPUT]: 本体完整性、事件不可变与 JSONB 往返、聚合根纯函数折叠的单测
[POS]: tests 的领域地基：蓝图是最后一道闸门（悬空引用 / 成环 / 无处安放的物品一律拒收）；
       "当前状态 = reduce(evolve, 历史)"——不查状态表，只凭事件流重算位置与行囊
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.domain.aggregates import Player, evolve
from app.domain.events import (
    EVENT_ADAPTER,
    ActionFailed,
    CombatOutcome,
    Conversed,
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
from app.domain.models import (
    Attitude,
    Item,
    Location,
    MartialArt,
    Prerequisites,
    Tier,
    Transmission,
    WorldBlueprint,
    prerequisite_cycle,
)
from app.errors import PlayerDeadError, UnknownPlayerError
from tests.world import WORLD

PID = "ply:test"


# ============================================================
#  本体
# ============================================================
def test_tier_is_ordinal_not_numeric() -> None:
    assert [t.rank for t in Tier] == [0, 1, 2, 3, 4]
    assert Tier.PEERLESS.rank > Tier.FIRST.rank > Tier.NONE.rank


def test_world_fixture_is_consistent() -> None:
    assert len(WORLD.locations) == 4 and len(WORLD.martial_arts) == 7


def test_blueprint_rejects_dangling_exit() -> None:
    with pytest.raises(ValidationError, match="不存在的LOCATION"):
        WorldBlueprint(locations=(Location(id="loc:甲", name="甲", exits={"北": "loc:乙"}),))


def test_blueprint_rejects_prerequisite_cycle() -> None:
    a = MartialArt(id="art:甲", name="甲", prerequisites=Prerequisites(skills=("art:乙",)))
    b = MartialArt(id="art:乙", name="乙", prerequisites=Prerequisites(skills=("art:甲",)))
    with pytest.raises(ValidationError, match="前置成环"):
        WorldBlueprint(martial_arts=(a, b))
    assert prerequisite_cycle({"x": ("y",), "y": ()}) == []


def test_item_must_exist_somewhere_and_self_study_needs_text() -> None:
    with pytest.raises(ValidationError, match="不存在于世界之中"):
        Item(id="itm:虚空", name="虚空")
    with pytest.raises(ValidationError, match="自悟"):
        Prerequisites(transmission=Transmission.SELF)
    lost = Item(id="itm:玉佩", name="玉佩", owner_id="chr:段正淳", location_id="loc:无量山")
    assert lost.canon_holder == "loc:无量山"  # 失物：物主不在身边，物理所在优先


def test_intent_normalizes_instead_of_rejecting() -> None:
    intent = PlayerIntent(action_type="ATTACK", target_entity="  ", item_used="剑" * 50, narrative_style=None)
    assert intent.target_entity is None and len(intent.item_used or "") == 24 and intent.narrative_style == ""


# ============================================================
#  事件
# ============================================================
ALL_EVENTS = [
    PlayerSpawned(player_id=PID, name="阿星", location_id="loc:无量山"),
    Moved(from_location_id="loc:无量山", to_location_id="loc:大理城", exit_label="南下"),
    ItemTransferred(item_id="itm:玉佩", from_holder="loc:无量山", to_holder=PID),
    SkillLearned(skill_id="art:无量剑法", source_id="chr:辛双清"),
    SkillExecuted(skill_id=None, target_id="chr:左子穆", outcome=CombatOutcome.REPELLED),
    Conversed(npc_id="chr:段誉"),
    RelationChanged(character_id="chr:段正淳", attitude=Attitude.FRIENDLY, cause="物归原主"),
    ActionFailed(action=ActionType.MOVE, target="少林寺", reason_code="NO_PATH", reason="无路"),
    PlayerDied(cause="冒犯", killer_id="chr:南海鳄神"),
]


@pytest.mark.parametrize("event", ALL_EVENTS, ids=lambda e: type(e).__name__)
def test_events_round_trip_through_json(event: object) -> None:
    raw = EVENT_ADAPTER.dump_json(event)  # type: ignore[arg-type]
    assert EVENT_ADAPTER.validate_json(raw) == event


def test_events_are_immutable() -> None:
    with pytest.raises(ValidationError):
        ALL_EVENTS[1].to_location_id = "loc:少林寺"  # type: ignore[attr-defined,misc]


# ============================================================
#  聚合根：只凭事件流重算 Location 与 Inventory
# ============================================================
def _history(*events: object) -> list[EventEnvelope]:
    return [
        EventEnvelope(stream_id=PID, version=i, event_id=uuid4(), recorded_at=datetime.now(UTC), event=e)  # type: ignore[arg-type]
        for i, e in enumerate(events, start=1)
    ]


def test_location_and_inventory_are_folded_from_history() -> None:
    player = Player.from_history(PID, _history(
        PlayerSpawned(player_id=PID, name="阿星", location_id="loc:无量山"),
        ItemTransferred(item_id="itm:玉佩", from_holder="loc:无量山", to_holder=PID),
        Moved(from_location_id="loc:无量山", to_location_id="loc:无量玉洞", exit_label="崖下"),
        ItemTransferred(item_id="itm:北冥神功卷轴", from_holder="loc:无量玉洞", to_holder=PID),
        Moved(from_location_id="loc:无量玉洞", to_location_id="loc:无量山", exit_label="攀上"),
        Moved(from_location_id="loc:无量山", to_location_id="loc:大理城", exit_label="南下"),
        ItemTransferred(item_id="itm:玉佩", from_holder=PID, to_holder="chr:段正淳"),
    ))
    assert player.version == 7
    assert player.state.location_id == "loc:大理城"
    assert player.state.inventory == {"itm:北冥神功卷轴"}
    assert player.state.item_holders["itm:玉佩"] == "chr:段正淳"  # 世界相对原著的偏离也在状态里


def test_replay_is_a_pure_reduce() -> None:
    events = [e for e in ALL_EVENTS if not isinstance(e, PlayerDied)]
    once, twice = Player.replay(events), Player.replay(events)
    assert once == twice and once is not twice
    assert once is not None and once.subdued == frozenset() and once.skills == {"art:无量剑法"}


def test_stream_must_start_with_spawn_and_be_contiguous() -> None:
    with pytest.raises(ValueError, match="PlayerSpawned"):
        evolve(None, Conversed(npc_id="chr:段誉"))
    broken = _history(ALL_EVENTS[0], ALL_EVENTS[1])
    broken[1] = broken[1].model_copy(update={"version": 3})
    with pytest.raises(ValueError, match="断裂"):
        Player.from_history(PID, broken)


def test_unknown_and_dead_players_cannot_act() -> None:
    with pytest.raises(UnknownPlayerError):
        _ = Player(PID).state
    dead = Player.from_history(PID, _history(ALL_EVENTS[0], ALL_EVENTS[-1]))
    with pytest.raises(PlayerDeadError, match="已经死了"):
        dead.ensure_alive()
