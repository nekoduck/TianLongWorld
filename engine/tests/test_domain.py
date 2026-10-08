"""
[INPUT]: 依赖 app.domain 的 models / events / aggregates / progression，依赖 tests/world 的 WORLD
[OUTPUT]: 本体完整性、事件不可变与 JSONB 往返及旧账上抛、渐进式状态的折算、聚合根纯函数折叠的单测
[POS]: tests 的领域地基：蓝图是最后一道闸门（悬空引用 / 根基成环 / 人物主键不是本名一律拒收，下落不明的物品合法存在）；
       "当前状态 = reduce(evolve, 历史)"——不查状态表，只凭事件流重算位置、行囊、火候与气血；拿到秘籍不等于学会
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.domain.aggregates import Player, evolve
from app.domain.combat import CombatOutcome
from app.domain.events import (
    EVENT_ADAPTER,
    LEGACY_MASTERY_POINTS,
    ActionFailed,
    Conversed,
    EventEnvelope,
    HealthChanged,
    ItemTransferred,
    Moved,
    PlayerDied,
    PlayerSpawned,
    RelationChanged,
    SkillExecuted,
    SkillPracticed,
    decode_event,
)
from app.domain.intent import ActionType, PlayerIntent
from app.domain.models import (
    Acquisition,
    Attitude,
    Character,
    Item,
    Location,
    MartialArt,
    Practice,
    Provenance,
    Tier,
    Transmission,
    WorldBlueprint,
    prerequisite_cycle,
)
from app.domain.progression import (
    MAX_HP,
    Mastery,
    Vitality,
    aptitude_for,
    effective_tier,
    mastery_of,
    vitality,
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
    assert len(WORLD.locations) == 4 and len(WORLD.characters) == 9 and len(WORLD.martial_arts) == 7


def test_character_key_is_the_true_name_and_titles_still_resolve() -> None:
    yanqing = next(c for c in WORLD.characters if c.id == "chr:段延庆")
    assert yanqing.name == "段延庆" and yanqing.names == ("段延庆", "恶贯满盈", "延庆太子")
    usurper = Character(id="chr:恶贯满盈", true_name="段延庆", titles=("恶贯满盈",))
    with pytest.raises(ValidationError, match="主键须是本名"):
        WorldBlueprint(characters=(usurper,))  # 称号再响，也不能篡位成主键


def test_blueprint_rejects_dangling_exit() -> None:
    with pytest.raises(ValidationError, match="不存在的LOCATION"):
        WorldBlueprint(locations=(Location(id="loc:甲", name="甲", exits={"北": "loc:乙"}),))


def test_blueprint_rejects_foundation_cycle() -> None:
    a = MartialArt(id="art:甲", name="甲", practice=Practice(skills=("art:乙",)))
    b = MartialArt(id="art:乙", name="乙", practice=Practice(skills=("art:甲",)))
    with pytest.raises(ValidationError, match="根基成环"):
        WorldBlueprint(martial_arts=(a, b))
    assert prerequisite_cycle({"x": ("y",), "y": ()}) == []


def test_acquisition_and_practice_are_separate_gates() -> None:
    art = next(a for a in WORLD.martial_arts if a.id == "art:凌波微步")
    assert art.acquisition.items == ("itm:北冥神功卷轴",) and art.acquisition.location_id == "loc:无量玉洞"
    assert art.practice.skills == ("art:北冥神功",)  # 门径在琅嬛福地的卷轴里，根基是北冥神功
    with pytest.raises(ValidationError, match="自悟"):
        Acquisition(transmission=Transmission.SELF)
    dangling = MartialArt(id="art:甲", name="甲", acquisition=Acquisition(items=("itm:无此物",)))
    with pytest.raises(ValidationError, match="不存在的ITEM"):
        WorldBlueprint(martial_arts=(dangling,))


def test_items_may_be_lost_but_never_misplaced() -> None:
    misplaced = Item(id="itm:玉佩", name="玉佩", owner_id="chr:段正淳", location_id="loc:无量山")
    assert misplaced.canon_holder == "loc:无量山"  # 失物：物主不在身边，物理所在优先
    orphan = Item(id="itm:谱诀", name="谱诀")  # 原著提到它，却没写它在哪：下落不明，等自愈代理安放
    assert orphan.lost and orphan.canon_holder is None and orphan.provenance is Provenance.CANON
    assert WorldBlueprint(items=(orphan,)).items == (orphan,)


def test_intent_normalizes_instead_of_rejecting() -> None:
    intent = PlayerIntent(action_type="ATTACK", target_entity="  ", item_used="剑" * 50, narrative_style=None)
    assert intent.target_entity is None and len(intent.item_used or "") == 24 and intent.narrative_style == ""


# ============================================================
#  事件
# ============================================================
ALL_EVENTS = [
    PlayerSpawned(player_id=PID, name="阿星", location_id="loc:无量山", aptitude=1.2),
    Moved(from_location_id="loc:无量山", to_location_id="loc:大理城", exit_label="南下"),
    ItemTransferred(item_id="itm:玉佩", from_holder="loc:无量山", to_holder=PID),
    SkillPracticed(skill_id="art:无量剑法", proficiency_gained=10, source_id="chr:辛双清"),
    SkillExecuted(skill_id=None, target_id="chr:左子穆", outcome=CombatOutcome.MINOR_WOUND),
    HealthChanged(delta=-18, cause="与左子穆交手", source_id="chr:左子穆"),
    Conversed(npc_id="chr:段誉"),
    RelationChanged(character_id="chr:段正淳", attitude=Attitude.FRIENDLY, cause="物归原主"),
    ActionFailed(action=ActionType.MOVE, target="少林寺", reason_code="NO_PATH", reason="无路"),
    PlayerDied(cause="冒犯", killer_id="chr:南海鳄神"),
]


@pytest.mark.parametrize("event", ALL_EVENTS, ids=lambda e: type(e).__name__)
def test_events_round_trip_through_json(event: object) -> None:
    raw = EVENT_ADAPTER.dump_json(event)  # type: ignore[arg-type]
    assert EVENT_ADAPTER.validate_json(raw) == event


@pytest.mark.parametrize("event", ALL_EVENTS, ids=lambda e: type(e).__name__)
def test_decode_event_is_the_single_read_path(event: object) -> None:
    assert decode_event(EVENT_ADAPTER.dump_json(event)) == event  # type: ignore[arg-type]


def test_legacy_vocabulary_is_upcast_on_read() -> None:
    learned = decode_event('{"type": "SkillLearned", "skill_id": "art:一阳指", "source_id": "chr:段正淳"}')
    assert learned == SkillPracticed(skill_id="art:一阳指", proficiency_gained=LEGACY_MASTERY_POINTS, source_id="chr:段正淳")
    assert mastery_of(LEGACY_MASTERY_POINTS, 1.0) is Mastery.ADEPT  # 当年学会了，今天仍是融会贯通
    repelled = decode_event({"type": "SkillExecuted", "skill_id": None, "target_id": "chr:左子穆", "outcome": "受挫"})
    assert isinstance(repelled, SkillExecuted) and repelled.outcome is CombatOutcome.MINOR_WOUND
    old_spawn = decode_event({"type": "PlayerSpawned", "player_id": PID, "name": "阿星", "location_id": "loc:无量山"})
    assert isinstance(old_spawn, PlayerSpawned) and old_spawn.aptitude == 1.0  # 悟性之前的旧账：中人之资


def test_events_are_immutable() -> None:
    with pytest.raises(ValidationError):
        ALL_EVENTS[1].to_location_id = "loc:少林寺"  # type: ignore[attr-defined,misc]
    with pytest.raises(ValidationError):
        SkillPracticed(skill_id="art:一阳指", proficiency_gained=0)  # 修习至少有一分所得


# ============================================================
#  渐进式状态：内部是整数，对外是语义
# ============================================================
def test_mastery_is_points_times_aptitude() -> None:
    assert mastery_of(0, 1.3) is None
    assert [mastery_of(p, 1.0) for p in (1, 20, 45, 75, 110)] == list(Mastery)
    assert mastery_of(19, 1.0) is Mastery.NOVICE and mastery_of(19, 1.1) is Mastery.MINOR  # 悟性高一分，早一步小成
    assert effective_tier(Tier.FIRST, Mastery.NOVICE) is Tier.THIRD
    assert effective_tier(Tier.FIRST, Mastery.MINOR) is Tier.SECOND
    assert effective_tier(Tier.THIRD, Mastery.NOVICE) is Tier.NONE  # 折扣不跌穿不入流
    assert effective_tier(Tier.FIRST, Mastery.PEAK) is Tier.FIRST  # 火候再高也不越过功夫本身的境界


def test_aptitude_is_innate_and_vitality_is_semantic() -> None:
    assert aptitude_for("ply:甲") == aptitude_for("ply:甲") and 0.5 <= aptitude_for("ply:乙") <= 1.5
    assert [vitality(hp) for hp in (100, 89, 49, 19)] == list(Vitality)


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
    assert once.hp == MAX_HP - 18 and once.vitality is Vitality.HURT


def test_skill_level_is_the_reduce_of_practice_scaled_by_aptitude() -> None:
    def practiced(aptitude: float) -> Player:
        return Player.from_history(PID, _history(
            PlayerSpawned(player_id=PID, name="阿星", location_id="loc:无量玉洞", aptitude=aptitude),
            ItemTransferred(item_id="itm:北冥神功卷轴", from_holder="loc:无量玉洞", to_holder=PID),
            SkillPracticed(skill_id="art:北冥神功", proficiency_gained=10, source_id="itm:北冥神功卷轴"),
            SkillPracticed(skill_id="art:北冥神功", proficiency_gained=10, source_id="itm:北冥神功卷轴"),
            SkillPracticed(skill_id="art:北冥神功", proficiency_gained=5),
        ))

    dull, gifted = practiced(0.8), practiced(1.3)
    assert dull.state.practice == gifted.state.practice == {"art:北冥神功": 25}  # 事件只记"练了多少"
    assert dull.mastery("art:北冥神功") is Mastery.MINOR  # 25 × 0.8 = 20
    assert gifted.mastery("art:北冥神功") is Mastery.MINOR  # 25 × 1.3 = 32.5
    assert dull.mastery("art:凌波微步") is None


def test_acquiring_a_manual_is_not_learning() -> None:
    state = Player.replay([
        PlayerSpawned(player_id=PID, name="阿星", location_id="loc:无量玉洞"),
        ItemTransferred(item_id="itm:北冥神功卷轴", from_holder="loc:无量玉洞", to_holder=PID),
    ])
    assert state is not None and state.inventory == {"itm:北冥神功卷轴"} and state.skills == frozenset()


def test_health_is_clamped_and_success_subdues() -> None:
    state = Player.replay([
        PlayerSpawned(player_id=PID, name="阿星", location_id="loc:无量山"),
        HealthChanged(delta=+30, cause="调息疗伤"),
        SkillExecuted(skill_id=None, target_id="chr:左子穆", outcome=CombatOutcome.SUCCESS),
        HealthChanged(delta=-500, cause="与南海鳄神交手", source_id="chr:南海鳄神"),
    ])
    assert state is not None and state.hp == 0 and state.alive  # 气血归零不等于死：死亡只认明写的 PlayerDied
    assert state.subdued == {"chr:左子穆"}


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
