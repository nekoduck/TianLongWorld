"""
[INPUT]: 依赖 pytest，依赖 app.engine 的裁决 decide_opening / decide_turn、投影 begin / apply / project_life / project_world / snapshot_of、
         局部环境原语 observe 与常量 MAX_ENTITIES / DEFAULT_DEATH，依赖 app.events 的事件模型、app.lore 的 OpeningSeed、
         app.schemas 的契约模型与 MAX_TAGS / LEDGERS，依赖 conftest 的 PLAYER / OPTIONS / WINDOW 与 alive() / dead() / snapshot() 报文工厂
[OUTPUT]: 决定论内核的纯函数单测：开局只认种子、四本账逐本记账并投影（遗漏≠失去、精确优先的唯一包含匹配、去重、
          容量与移除腾位、绝学防线）、时辰只进不退、四种死状保留与死者无选项、必死封印、世界大事去重、
          局部环境在场账（开局种子在场者恒登记、按身份认人、同图判据只认原地或深入子地点、换图清空、封顶保在场者）、
          世界大事去重与驳回复述、局部环境投影、事件折叠投影与滑动窗口、流完整性
[POS]: tests 中守护 engine "f(当前状态, 已校验意图) -> 事件 -> 下一状态" 不变量的用例集；全程纯内存，不碰事件库
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from collections.abc import Sequence
from uuid import uuid4

import pytest

from app.engine import (
    DEFAULT_DEATH,
    MAX_ENTITIES,
    Decision,
    LifeView,
    LocalEnvironment,
    apply,
    begin,
    decide_opening,
    decide_turn,
    observe,
    project_life,
    project_world,
    snapshot_of,
)
from app.events import LifeBegan, LifeEvent, TurnResolved, WorldEventRecorded
from app.lore import OpeningSeed
from app.schemas import (
    LEDGERS,
    MAX_TAGS,
    NO_LOCAL_CHANGE,
    NO_PLAYER_CHANGE,
    DirectorOutput,
    Ledger,
    LocalDelta,
    PlayerDelta,
    PlayerSnapshot,
    PlayerState,
    TagDelta,
    WorldEvent,
)
from conftest import OPTIONS, PLAYER, WINDOW, alive, dead, snapshot

SEED = OpeningSeed(player=PLAYER, premise="邻桌一条魁梧大汉独据一桌——那便是丐帮帮主乔峰。", present=("萧峰",))
SONGHE = LocalEnvironment(location="无锡松鹤楼", entities=("乔峰", "段誉"))
UPSTAIRS = SONGHE.model_copy(update={"location": "无锡松鹤楼二楼"})
# 四本账各持一项，让"遗漏≠失去"在每本账上都有可保留之物；GAINS 是每本账上一个可合法新增的标签
STOCKED = PLAYER.model_copy(update={"social_traits": ("松鹤楼常客",), "martial_arts": ("太祖长拳",)})
GAINS: dict[Ledger, str] = {"buffs_debuffs": "微醺", "social_traits": "丐帮记名弟子", "inventory": "酒碗", "martial_arts": "罗汉拳"}


# ============================================================
#  构造器 —— 纯内存地布置一条命，从不经过事件库
# ============================================================
def _began(player: PlayerState = PLAYER, entities: Sequence[str] = ()) -> LifeBegan:
    return LifeBegan(world_id=uuid4(), player=player, scene="你在松鹤楼角落里醒来。", options=OPTIONS, entities=tuple(entities))


def _view(player: PlayerState = PLAYER, entities: Sequence[str] = ()) -> LifeView:
    return begin(uuid4(), _began(player, entities))


def _decide(view: LifeView, out: DirectorOutput, *, condemned: bool = False) -> Decision:
    return decide_turn(view, "custom", "四下张望", out, condemned=condemned)


def _settle(view: LifeView, decision: Decision) -> PlayerState:
    """把裁决产出的事件投影回去，得到本回合落定后的玩家状态。"""
    return apply(view, decision.turn, WINDOW).player


def _ledgers(player: PlayerState) -> dict[str, tuple[str, ...]]:
    """四本标签账的内容，供整体比对（快照字段不在其中）。"""
    return {ledger: getattr(player, ledger) for ledger in LEDGERS}


def _delta(ledger: Ledger, *, add: Sequence[str] = (), remove: Sequence[str] = ()) -> PlayerDelta:
    """只在一本账上提出增减，其余三本原样不动。"""
    return NO_PLAYER_CHANGE.model_copy(update={ledger: TagDelta(add=tuple(add), remove=tuple(remove))})


def _turn(action: str) -> TurnResolved:
    """直接构造一条落定事件，供投影用例拼装事件流。"""
    return TurnResolved(
        action_type="custom",
        action=action,
        scene=f"{action}之后，楼中一片寂静。",
        snapshot=snapshot(),
        changes=NO_PLAYER_CHANGE,
        entities=(),
        options=OPTIONS,
        died=False,
    )


# ============================================================
#  开局 —— 状态完全取自种子
# ============================================================
def test_opening_state_comes_only_from_seed() -> None:
    greedy = alive(
        next_state=snapshot(location="少林寺藏经阁", health_status="内力深厚"),
        player_delta=PlayerDelta(
            buffs_debuffs=TagDelta(add=(), remove=("饥饿",)),
            social_traits=TagDelta(add=("丐帮弟子",), remove=()),
            inventory=TagDelta(add=("倚天剑",), remove=()),
            martial_arts=TagDelta(add=("太祖长拳",), remove=()),
        ),
    )
    world_id = uuid4()
    began = decide_opening(world_id, SEED, greedy)
    assert began.player == SEED.player
    assert (began.world_id, began.scene, began.options) == (world_id, greedy.scene_description, greedy.options)


def test_opening_entities_are_unique_and_capped() -> None:
    crowd = tuple(f"看客{i}" for i in range(MAX_ENTITIES + 3))
    # 契约把 arrived 封顶在 12 个：绕过契约校验直接构造，验证内核自身的封顶防线；
    # 「乔峰」与种子登记的「萧峰」是同一人，不重复占位
    arrived = LocalDelta.model_construct(arrived=("乔峰", "乔峰", *crowd), departed=())
    began = decide_opening(uuid4(), SEED, alive(local_delta=arrived))
    assert began.entities == ("萧峰", *crowd)[:MAX_ENTITIES]


def test_opening_registers_seed_presence_without_llm_help() -> None:
    # 大模型忘了写 arrived，种子点名的高手照样在场——规则层的生死判定不依赖大模型的记性
    began = decide_opening(uuid4(), SEED, alive())
    assert began.entities == ("萧峰",)


def test_opening_departure_overrides_seed_presence() -> None:
    # 世界大事里萧峰已死：大模型显式写进 departed，开局才不把他登记在场
    began = decide_opening(uuid4(), SEED, alive(local_delta=LocalDelta(arrived=(), departed=("乔峰",))))
    assert began.entities == ()


def test_opening_requires_options() -> None:
    with pytest.raises(ValueError, match="选项"):
        decide_opening(uuid4(), SEED, dead())


# ============================================================
#  四本账 —— 只认点名的增减，遗漏不等于失去
# ============================================================
@pytest.mark.parametrize("ledger", LEDGERS)
def test_each_ledger_books_only_named_additions(ledger: Ledger) -> None:
    view = _view(STOCKED)
    gain = GAINS[ledger]
    player = _settle(view, _decide(view, alive(player_delta=_delta(ledger, add=(gain,)))))
    # 点名的那本账落下新增、原有标签保留；未被点名的三本账原样不动
    assert _ledgers(player) == _ledgers(STOCKED) | {ledger: (*getattr(STOCKED, ledger), gain)}


def test_removal_resolves_to_unique_containing_tag() -> None:
    view = _view()
    decision = _decide(view, alive(player_delta=_delta("inventory", remove=("铜钱",))))
    assert decision.turn.changes.inventory.remove == ("三枚铜钱",)
    assert _settle(view, decision).inventory == ()


def test_exact_name_takes_precedence_over_containment() -> None:
    view = _view(PLAYER.model_copy(update={"inventory": ("铜钱", "三枚铜钱")}))
    decision = _decide(view, alive(player_delta=_delta("inventory", remove=("铜钱",))))
    assert _settle(view, decision).inventory == ("三枚铜钱",)


def test_ambiguous_removal_changes_nothing() -> None:
    view = _view(PLAYER.model_copy(update={"inventory": ("三枚铜钱", "五枚铜钱")}))
    decision = _decide(view, alive(player_delta=_delta("inventory", remove=("铜钱",))))
    assert decision.turn.changes.inventory.remove == ()
    assert _settle(view, decision).inventory == ("三枚铜钱", "五枚铜钱")
    assert any("铜钱" in note for note in decision.rejections)


def test_removing_unheld_tag_is_rejected() -> None:
    view = _view()
    decision = _decide(view, alive(player_delta=_delta("inventory", remove=("倚天剑",))))
    assert decision.turn.changes.inventory.remove == ()
    assert _settle(view, decision).inventory == PLAYER.inventory
    assert any("倚天剑" in note for note in decision.rejections)


def test_additions_are_deduplicated_and_skip_held_tags() -> None:
    decision = _decide(_view(), alive(player_delta=_delta("inventory", add=("酒碗", "酒碗", "三枚铜钱"))))
    assert decision.turn.changes.inventory.add == ("酒碗",)


def test_additions_beyond_capacity_are_dropped() -> None:
    full = tuple(f"杂物{i}" for i in range(MAX_TAGS - 1))
    view = _view(PLAYER.model_copy(update={"inventory": full}))
    decision = _decide(view, alive(player_delta=_delta("inventory", add=("酒碗", "折扇", "火折子"))))
    assert decision.turn.changes.inventory.add == ("酒碗",)
    assert _settle(view, decision).inventory == (*full, "酒碗")
    assert any("折扇" in note and "火折子" in note for note in decision.rejections)


def test_removal_frees_room_for_addition_in_same_turn() -> None:
    full = tuple(f"杂物{i}" for i in range(MAX_TAGS))
    view = _view(PLAYER.model_copy(update={"inventory": full}))
    # 满额以物易物：先移除腾出的空位，必须让同回合的新增落账
    decision = _decide(view, alive(player_delta=_delta("inventory", add=("酒碗",), remove=("杂物0",))))
    assert _settle(view, decision).inventory == (*full[1:], "酒碗")
    assert decision.rejections == ()


def test_llm_cannot_grant_master_arts() -> None:
    view = _view()
    arts = _delta("martial_arts", add=("北冥神功", "降龙十八掌残篇", "太祖长拳"))
    decision = _decide(view, alive(player_delta=arts))
    assert decision.turn.changes.martial_arts.add == ("太祖长拳",)
    assert _settle(view, decision).martial_arts == ("太祖长拳",)
    assert any("北冥神功" in note for note in decision.rejections)
    assert any("降龙十八掌残篇" in note for note in decision.rejections)


# ============================================================
#  时辰 —— 十二时辰之内，只进不退，一回合至多半天
# ============================================================
def test_time_outside_twelve_shichen_is_rejected() -> None:
    decision = _decide(_view(), alive(next_state=snapshot(time="黄昏")))
    assert decision.turn.snapshot.time == PLAYER.time
    assert any("黄昏" in note for note in decision.rejections)


def test_time_cannot_flow_backwards() -> None:
    decision = _decide(_view(), alive(next_state=snapshot(time="辰时")))
    assert decision.turn.snapshot.time == "午时"
    assert any("辰时" in note for note in decision.rejections)


@pytest.mark.parametrize(
    ("proposed", "settled"), [("子时", "子时"), ("丑时", "午时")], ids=["six_steps_accepted", "seven_steps_rejected"]
)
def test_time_advances_at_most_six_shichen(proposed: str, settled: str) -> None:
    assert _decide(_view(), alive(next_state=snapshot(time=proposed))).turn.snapshot.time == settled


def test_decorated_shichen_is_normalized() -> None:
    view = _view(PLAYER.model_copy(update={"time": "亥时"}))
    decision = _decide(view, alive(next_state=snapshot(time="子时三刻")))
    assert decision.turn.snapshot.time == "子时"
    assert decision.rejections == ()


# ============================================================
#  死亡 —— 死状必须写明，死者没有选择
# ============================================================
@pytest.mark.parametrize(
    ("health", "settled"),
    [
        ("奄奄一息", DEFAULT_DEATH),
        ("当场身死", "当场身死"),
        ("当场身亡", "当场身亡"),
        ("被一掌震毙", "被一掌震毙"),
        ("气绝", "气绝"),
    ],
    ids=["rewritten", "preserved_si", "preserved_wang", "preserved_bi", "preserved_jue"],
)
def test_dead_health_must_state_death(health: str, settled: str) -> None:
    turn = _decide(_view(), dead(health=health)).turn
    assert turn.died
    assert turn.snapshot.health_status == settled


def test_dead_have_no_options() -> None:
    view = _view()
    # 契约允许 game_over 时仍带选项：清空选项是内核自己的职责
    decision = _decide(view, alive().model_copy(update={"game_over": True}))
    after = apply(view, decision.turn, WINDOW)
    assert decision.turn.options is None
    assert after.dead and after.options is None


def test_condemned_turn_voids_every_llm_intent() -> None:
    merciful = alive(
        next_state=snapshot(health_status="毫发无伤"),
        player_delta=_delta("inventory", add=("打狗棒",), remove=("铜钱",)),
        world_events=(WorldEvent(tags=("乔峰", "松鹤楼"), event_desc="玩家在松鹤楼击败了乔峰"),),
    )
    decision = _decide(_view(entities=("乔峰",)), merciful, condemned=True)
    assert decision.turn.died and decision.turn.options is None
    assert decision.turn.snapshot.health_status == DEFAULT_DEATH
    assert decision.turn.changes == NO_PLAYER_CHANGE
    assert decision.world_events == ()


# ============================================================
#  世界大事 —— 按事实去重，溯源到这一世
# ============================================================
def test_world_events_are_deduplicated_and_traced_to_life() -> None:
    view = _view()
    first = WorldEvent(tags=("段誉",), event_desc="玩家抢走了段誉的折扇")
    echo = WorldEvent(tags=("段誉", "松鹤楼"), event_desc="玩家抢走了段誉的折扇")
    other = WorldEvent(tags=("乔峰",), event_desc="乔峰在松鹤楼连干四十碗")
    decision = _decide(view, alive(world_events=(first, echo, other)))
    assert [recorded.event for recorded in decision.world_events] == [first, other]
    assert all(recorded.life_id == view.life_id for recorded in decision.world_events)


def test_world_events_already_known_are_rejected() -> None:
    # 只追加的台账容不下复述：大模型把 relevant_history 原样抄回来，一条也不追加
    view = _view()
    known = WorldEvent(tags=("聚贤庄",), event_desc="聚贤庄被玩家付之一炬")
    fresh = WorldEvent(tags=("段誉",), event_desc="段誉被玩家掳上了船")
    decision = decide_turn(view, "custom", "四下张望", alive(world_events=(known, fresh)), condemned=False, known=(known,))
    assert [recorded.event for recorded in decision.world_events] == [fresh]
    assert any("聚贤庄被玩家付之一炬" in note for note in decision.rejections)


# ============================================================
#  局部环境 —— 同图只认增减，换图强制清空
# ============================================================
def test_same_map_keeps_present_and_appends_arrivals() -> None:
    entities = observe(SONGHE, "无锡松鹤楼", LocalDelta(arrived=("王语嫣", "王语嫣", "乔峰"), departed=()))
    assert entities == ("乔峰", "段誉", "王语嫣")


def test_departure_matches_fuzzily() -> None:
    entities = observe(SONGHE, "无锡松鹤楼", LocalDelta(arrived=(), departed=("丐帮帮主乔峰",)))
    assert entities == ("段誉",)


def test_presence_is_tracked_by_identity_not_by_name() -> None:
    # 「萧峰」与「乔帮主」是同一人：到场不重复登记，离场按身份认人
    entities = observe(SONGHE, "无锡松鹤楼", LocalDelta(arrived=("萧峰",), departed=()))
    assert entities == SONGHE.entities
    assert observe(SONGHE, "无锡松鹤楼", LocalDelta(arrived=(), departed=("乔帮主",))) == ("段誉",)


@pytest.mark.parametrize("location", ["无锡松鹤楼", "无锡松鹤楼二楼"], ids=["stay", "into_sub_location"])
def test_staying_or_going_deeper_keeps_the_map(location: str) -> None:
    assert observe(SONGHE, location, NO_LOCAL_CHANGE) == SONGHE.entities


@pytest.mark.parametrize(
    ("local", "location"),
    [(SONGHE, "无锡"), (SONGHE, "松鹤楼二楼"), (UPSTAIRS, "无锡松鹤楼")],
    ids=["out_to_parent", "wording_drift", "back_down_from_sub_location"],
)
def test_any_other_location_is_a_new_map(local: LocalEnvironment, location: str) -> None:
    # 宁可让高手暂时离开实体账，也不让不在场的人凭旧账处决玩家；Prompt 规则 6 讲明同一判据
    assert observe(local, location, NO_LOCAL_CHANGE) == ()


def test_changing_map_clears_previous_presence() -> None:
    entities = observe(SONGHE, "少林寺山门外", LocalDelta(arrived=("扫地僧",), departed=()))
    assert entities == ("扫地僧",)


def test_presence_cap_keeps_those_already_present() -> None:
    crowded = LocalEnvironment(location="无锡松鹤楼", entities=tuple(f"看客{i}" for i in range(MAX_ENTITIES)))
    # 封顶舍弃溢出的新到场者，已在场者不会被挤出名单（致死预判读的正是这份名单）
    entities = observe(crowded, "无锡松鹤楼", LocalDelta(arrived=("乔峰", "段誉", "王语嫣"), departed=()))
    assert entities == crowded.entities


# ============================================================
#  投影 —— 事件流的纯函数折叠
# ============================================================
def test_project_life_equals_stepwise_apply() -> None:
    life_id = uuid4()
    began = _began(entities=("乔峰",))
    view = begin(life_id, began)
    events: list[LifeEvent] = [began]
    versions = [view.version]
    for out in (
        alive(player_delta=_delta("inventory", add=("酒碗",)), local_delta=LocalDelta(arrived=("段誉",), departed=())),
        alive(next_state=snapshot(location="少林寺山门外", time="申时"), player_delta=_delta("buffs_debuffs", remove=("饥饿",))),
    ):
        turn = decide_turn(view, "choice", OPTIONS.A, out, condemned=False).turn
        view = apply(view, turn, WINDOW)
        events.append(turn)
        versions.append(view.version)
    assert project_life(life_id, events, WINDOW) == view
    assert versions == [1, 2, 3]


def test_local_environment_is_projected_from_events() -> None:
    view = _view(entities=("乔峰",))
    assert view.local == LocalEnvironment(location=PLAYER.location, entities=("乔峰",))
    leave = alive(next_state=snapshot(location="少林寺山门外"), local_delta=LocalDelta(arrived=("扫地僧",), departed=()))
    after = apply(view, _decide(view, leave).turn, WINDOW)
    assert after.local == LocalEnvironment(location="少林寺山门外", entities=("扫地僧",))


def test_window_keeps_only_last_turns_in_order() -> None:
    turns = [_turn(f"第{i}招") for i in range(1, 7)]
    view = project_life(uuid4(), [_began(), *turns], WINDOW)
    assert [turn.action for turn in view.window] == [turn.action for turn in turns[-WINDOW:]]


@pytest.mark.parametrize("events", [[], [_turn("偷袭")]], ids=["empty", "turn_first"])
def test_life_stream_must_open_with_life_began(events: list[LifeEvent]) -> None:
    with pytest.raises(ValueError, match="LifeBegan"):
        project_life(uuid4(), events, WINDOW)


def test_life_stream_rejects_second_life_began() -> None:
    with pytest.raises(ValueError, match="重复"):
        project_life(uuid4(), [_began(), _turn("出招"), _began()], WINDOW)


def test_project_world_preserves_append_order() -> None:
    events = [
        WorldEvent(tags=("段誉",), event_desc="玩家抢走了段誉的折扇"),
        WorldEvent(tags=("乔峰",), event_desc="乔峰离开了无锡"),
        WorldEvent(tags=("杏子林",), event_desc="丐帮长老齐聚杏子林"),
    ]
    recorded = [WorldEventRecorded(life_id=uuid4(), event=event) for event in events]
    assert project_world(recorded).major_events == tuple(events)


def test_snapshot_of_takes_the_four_snapshot_fields() -> None:
    expected = PlayerSnapshot(
        location=PLAYER.location, time=PLAYER.time, weather=PLAYER.weather, health_status=PLAYER.health_status
    )
    assert snapshot_of(PLAYER) == expected
