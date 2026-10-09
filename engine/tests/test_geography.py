"""
[INPUT]: 依赖 app.domain.geography 的方位 / 交通方式 / 认知词汇、Passage / Sight 注记、Way / ways / derive_* / discovery / geography_errors，
         依赖 app.domain.snapshot 的 ExitView / LocalSnapshot（迷雾、把手、exit_names），依赖 app.domain.rules 的 decide / command 与 rules.talk.directions，
         依赖 app.domain.aggregates 的 Player（visited / heard 的折叠），依赖 tests/test_heartbeat 的 look（任意蓝图上的状态与内存图谱快照），依赖 tests/world 的 WORLD
[OUTPUT]: 空间属性图与探索迷雾的单测：
          词汇（Direction.opposite 两两互逆、COMPASS 次序、DiscoveryStatus.known）、推导（处所嵌套先于标签、四隅先于正向、正向取最先出现的、
          入进出上下、推不出即不明、去处名里的方位字不算；坠 / 船舟渡 / 攀爬；同一处所一刻否则四刻）、ways（每条出口一条有效属性、撰写的注记优先且标明 authored；
          没撰写的与回程对齐：推不出取回程之反、冲突以较小的一头为准、回程撰写过取其反并随其耗时（坠落的回程不随））、
          注记字段有界、蓝图闸门（注记须落在出口上、不得写不明、往返相反、重复、可见性注记落地且至少一项为真）、discovery 的强弱次序；
          折叠（投胎之地与每个去处进 visited、夺路而逃也算、问路并进 heard、旧账照读）；
          快照（ExitView 缺省视作亲历；未知即 shown_name「未知区域」且 names 只剩标签；handle 同方位按次序加序号；exit_names = 标签 + 把手 + 已知之名；
          内存图谱按 ways 与认知填实，地标 / 名胜不用到过也认得）；
          规则（移动按把手与标签走、未知之名不落地、问路之后才落地；注记的耗时进 rules.command；
          问路：寻常攀谈的话题落在此地或去处即出 PlacesLearned（此地相邻去处 ∪ 话题地点，减去已认得的，指路人是交谈对象），认得全了就不再出、
          话题不是地点不出、交涉不出，问路得知的去处从此叫得出名）
[POS]: tests 的地理基线：方位、交通方式、耗时与迷雾都由 geography 一个模块定，两套图谱、寻路与规则都只读它
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import pytest
from pydantic import ValidationError

from app.domain.aggregates import Player
from app.domain.events import Conversed, Moved, Parleyed, PlacesLearned, PlayerSpawned
from app.domain.geography import (
    COMPASS,
    UNKNOWN_PLACE,
    Direction,
    DiscoveryStatus,
    Passage,
    Sight,
    TravelMethod,
    Way,
    derive_cost,
    derive_direction,
    derive_method,
    discovery,
    geography_errors,
    ways,
)
from app.domain.intent import ActionType, Approach, PlayerIntent
from app.domain.models import Location, WorldBlueprint
from app.domain.rules import command, decide
from app.domain.rules.talk import directions
from app.domain.snapshot import ExitView, LocalSnapshot, LocationView
from tests.test_heartbeat import look
from tests.world import WORLD

PID = "ply:beat"  # 与 tests/test_heartbeat.look 同一位玩家
HILL, CITY, CAVE, WUXI = "loc:无量山", "loc:大理城", "loc:无量玉洞", "loc:无锡城"
D, M = Direction, TravelMethod


def act(kind: ActionType, **fields: object) -> PlayerIntent:
    return PlayerIntent(action_type=kind, **fields)  # type: ignore[arg-type]


def _bp(**update: object) -> WorldBlueprint:
    return WorldBlueprint.model_validate({**WORLD.model_dump(), **update})


def road(a: str, b: str, direction: Direction, cost: int, method: TravelMethod = M.WALK) -> Passage:
    return Passage(from_id=a, to_id=b, direction=direction, travel_method=method, time_cost=cost, basis="大理在南",
                   sources=("chunk:1",))


# ============================================================
#  词汇
# ============================================================
def test_directions_pair_up_and_the_compass_has_a_fixed_order() -> None:
    assert all(d.opposite.opposite is d for d in Direction)
    assert (D.EAST.opposite, D.NORTHWEST.opposite, D.UP.opposite, D.INSIDE.opposite) == (D.WEST, D.SOUTHEAST, D.DOWN, D.OUTSIDE)
    assert D.UNSPECIFIED.opposite is D.UNSPECIFIED
    assert COMPASS[:4] == (D.EAST, D.SOUTH, D.WEST, D.NORTH) and COMPASS[-1] is D.UNSPECIFIED and len(COMPASS) == len(D)
    assert [s.known for s in DiscoveryStatus] == [True, True, True, True, False]
    assert UNKNOWN_PLACE == "未知区域"


# ============================================================
#  推导 —— 没撰写过的出路照样有方位、交通方式与耗时
# ============================================================
@pytest.mark.parametrize(
    ("label", "here", "there", "direction"),
    [
        ("往西走", "剑湖宫", "剑湖宫·练武厅", D.INSIDE),  # 处所嵌套先于标签
        ("出厅", "剑湖宫·练武厅", "剑湖宫", D.OUTSIDE),
        ("出宫往西北", "剑湖宫", "无量山", D.NORTHWEST),  # 四隅先于「出」与正向
        ("东南方的小径", "甲", "乙", D.SOUTHEAST),
        ("北上", "大理城", "无量山", D.NORTH),  # 正向先于上下
        ("南下", "无量山", "大理城", D.SOUTH),
        ("西去东归", "甲", "乙", D.WEST),  # 正向取最先出现的一个
        ("入洞", "无量山", "无量玉洞", D.INSIDE),
        ("进城", "甲", "乙", D.INSIDE),
        ("出谷", "甲", "乙", D.OUTSIDE),
        ("攀上", "无量玉洞", "无量山", D.UP),
        ("崖下", "无量山", "无量玉洞", D.DOWN),
        ("跳崖", "甲", "乙", D.UNSPECIFIED),
        ("往无量山·西北角山坡", "无量山·乱石堆", "无量山·西北角山坡", D.UNSPECIFIED),  # 去处名里的方位字不算方位
        ("西去西夏", "大理城", "西夏", D.WEST),  # 抹掉名字之后照看动词
    ],
)
def test_direction_is_derived_from_nesting_then_the_label(label: str, here: str, there: str, direction: Direction) -> None:
    assert derive_direction(label, here, there) is direction


@pytest.mark.parametrize(
    ("label", "method"),
    [("坠崖", M.FALL), ("乘船过湖", M.BOAT), ("渡口", M.BOAT), ("驾舟", M.BOAT), ("攀上", M.CLIMB), ("爬上石壁", M.CLIMB),
     ("坠船", M.FALL), ("南下", M.WALK), ("崖下", M.WALK)],
)
def test_travel_method_is_derived_from_the_label(label: str, method: TravelMethod) -> None:
    assert derive_method(label) is method


def test_cost_is_one_within_a_place_and_four_between_places() -> None:
    assert derive_cost("剑湖宫", "剑湖宫·练武厅") == 1 and derive_cost("剑湖宫·练武厅", "剑湖宫·后院") == 1
    assert derive_cost("无量山", "大理城") == 4


def test_ways_give_every_exit_its_effective_properties() -> None:
    derived = ways(WORLD)
    assert len(derived) == sum(len(loc.exits) for loc in WORLD.locations)
    assert derived[(HILL, CITY)] == Way(HILL, CITY, "南下", D.SOUTH, M.WALK, 4)
    assert derived[(HILL, CAVE)] == Way(HILL, CAVE, "崖下", D.DOWN, M.WALK, 4)
    assert derived[(CAVE, HILL)].direction is D.UP and derived[(CAVE, HILL)].travel_method is M.CLIMB
    assert not any(w.authored for w in derived.values())
    noted = ways(_bp(passages=(road(CITY, WUXI, D.EAST, 288, M.RIDE), road(WUXI, CITY, D.WEST, 288, M.RIDE))))
    assert noted[(CITY, WUXI)] == Way(CITY, WUXI, "东去", D.EAST, M.RIDE, 288, authored=True)
    assert noted[(WUXI, CITY)].authored and noted[(HILL, CITY)] == derived[(HILL, CITY)]  # 没注记的照旧推出


def _pair(a_exits: dict[str, str], b_exits: dict[str, str]) -> WorldBlueprint:
    spots = (Location(id="loc:甲", name="甲", region="大理", description="", exits=a_exits),
             Location(id="loc:乙", name="乙", region="大理", description="", exits=b_exits))
    return WorldBlueprint(locations=spots)


def test_ways_align_a_defaulted_road_with_its_way_back() -> None:
    """往返方位永远相反：自己推不出取回程之反；两头推得出却冲突，以 (from, to) 较小的一头为准；回程撰写过即取其反并随其耗时。"""
    vague = ways(_pair({"东行过山坳": "loc:乙"}, {"往乙的那头": "loc:甲"}))
    assert vague[("loc:甲", "loc:乙")].direction is D.EAST and vague[("loc:乙", "loc:甲")].direction is D.WEST
    clash = ways(_pair({"东行": "loc:乙"}, {"西北去": "loc:甲"}))
    assert clash[("loc:乙", "loc:甲")].direction is D.NORTHWEST  # 「乙」排在「甲」前（U+4E59 < U+7532），以乙那头为准
    assert clash[("loc:甲", "loc:乙")].direction is D.SOUTHEAST
    note = Passage(from_id="loc:甲", to_id="loc:乙", direction=D.NORTH, time_cost=12, basis="北去", sources=("chunk:1",))
    noted = ways(_pair({"东行": "loc:乙"}, {"回去": "loc:甲"}).model_copy(update={"passages": (note,)}))
    assert noted[("loc:乙", "loc:甲")] == Way("loc:乙", "loc:甲", "回去", D.SOUTH, M.WALK, 12)
    fall = note.model_copy(update={"direction": D.DOWN, "travel_method": M.FALL, "time_cost": 1})
    cliff = ways(_pair({"失足坠崖": "loc:乙"}, {"攀回崖顶": "loc:甲"}).model_copy(update={"passages": (fall,)}))
    assert cliff[("loc:乙", "loc:甲")] == Way("loc:乙", "loc:甲", "攀回崖顶", D.UP, M.CLIMB, 4)  # 跳下去快，爬上来不随
    both = ways(_pair({"入": "loc:乙"}, {"出": "loc:甲"}))
    assert (both[("loc:甲", "loc:乙")].direction, both[("loc:乙", "loc:甲")].direction) == (D.INSIDE, D.OUTSIDE)  # 本就相反的不动


def test_notes_are_bounded() -> None:
    for bad in ({"time_cost": 0}, {"time_cost": 673}, {"basis": "据" * 41}, {"sources": ()}, {"from_id": "无量山"},
                {"direction": "东方"}, {"travel_method": "飞行"}, {"note": "多余"}):
        with pytest.raises(ValidationError):
            Passage.model_validate(road(HILL, CITY, D.SOUTH, 4).model_dump() | bad)
    assert road(HILL, CITY, D.SOUTH, 672).time_cost == 672
    with pytest.raises(ValidationError):
        Sight(location_id="无量山", landmark=True, sources=("chunk:1",))
    assert Sight(location_id=HILL, sources=("chunk:1",)).landmark is False


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"passages": (road(HILL, WUXI, D.EAST, 4),)}, "不是蓝图里的一条出口"),
        ({"passages": (road(HILL, CITY, D.UNSPECIFIED, 4),)}, "不得写「不明」"),
        ({"passages": (road(HILL, CITY, D.SOUTH, 4), road(HILL, CITY, D.SOUTH, 8))}, "重复的道路注记"),
        ({"passages": (road(HILL, CITY, D.SOUTH, 4), road(CITY, HILL, D.WEST, 4))}, "往返方位不相反"),
        ({"sights": (Sight(location_id="loc:少林寺", renowned=True, sources=("chunk:1",)),)}, "不存在的地点"),
        ({"sights": (Sight(location_id=HILL, sources=("chunk:1",)),)}, "既非地标也非名胜"),
        ({"sights": (Sight(location_id=HILL, landmark=True, sources=("chunk:1",)),) * 2}, "重复的可见性注记"),
    ],
)
def test_the_blueprint_gate_rejects_bad_geography(update: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        _bp(**update)


def test_one_way_notes_and_good_sights_pass_the_gate() -> None:
    fine = _bp(passages=(road(HILL, CITY, D.SOUTH, 6),),  # 只写一头：另一头仍由推导给出
               sights=(Sight(location_id=CITY, renowned=True, sources=("chunk:1",)),))
    assert geography_errors(fine) == [] and ways(fine)[(CITY, HILL)].direction is D.NORTH


def test_discovery_prefers_the_strongest_acquaintance() -> None:
    def seen(**sets: frozenset[str]) -> DiscoveryStatus:
        empty: frozenset[str] = frozenset()
        return discovery(CITY, **({"visited": empty, "heard": empty, "landmarks": empty, "renowned": empty} | sets))

    here = frozenset({CITY})
    assert seen(visited=here, heard=here, landmarks=here, renowned=here) is DiscoveryStatus.VISITED
    assert seen(heard=here, landmarks=here, renowned=here) is DiscoveryStatus.TOLD
    assert seen(landmarks=here, renowned=here) is DiscoveryStatus.SIGHTED
    assert seen(renowned=here) is DiscoveryStatus.RENOWNED
    assert seen(visited=frozenset({HILL})) is DiscoveryStatus.UNKNOWN


# ============================================================
#  折叠 —— 到过的与问路得知的
# ============================================================
def test_visited_and_heard_fold_from_the_stream() -> None:
    spawn = PlayerSpawned(player_id=PID, name="阿星", location_id=HILL)
    went = Moved(from_location_id=HILL, to_location_id=CITY, exit_label="南下")
    fled = Moved(from_location_id=CITY, to_location_id=WUXI, exit_label="东去", fleeing=True)
    told = PlacesLearned(location_ids=(CAVE, CITY), source_id="chr:左子穆")
    state = Player.replay([spawn, told, went, fled, PlacesLearned(location_ids=(CAVE,))])
    assert state is not None and state.visited == {HILL, CITY, WUXI}  # 投胎之地、每个去处，夺路而逃也算到过
    assert state.heard == {CAVE, CITY}  # 问路只并不减；后来到过了也不抹
    old = Player.replay([spawn, went])  # 旧账没有任何新事件：认知照样从投胎与移动里折出来
    assert old is not None and (old.visited, old.heard, old.agendas, old.agenda_tick) == ({HILL, CITY}, frozenset(), {}, None)
    with pytest.raises(ValidationError):
        PlacesLearned(location_ids=())


# ============================================================
#  快照 —— 迷雾、把手与落地的名字
# ============================================================
def _snap(*exits: ExitView) -> LocalSnapshot:
    return LocalSnapshot(player_id=PID, player_name="阿星", alive=True, version=1,
                         location=LocationView(id=HILL, name="无量山"), exits=exits)


def test_an_exit_defaults_to_known_and_an_unknown_one_hides_its_name() -> None:
    plain = ExitView(label="南下", to_id=CITY, to_name="大理城")
    assert (plain.discovery, plain.known, plain.shown_name, plain.names) == (
        DiscoveryStatus.VISITED, True, "大理城", ("南下", "大理城"))
    assert (plain.direction, plain.travel_method, plain.time_cost) == (D.UNSPECIFIED, M.WALK, 4)  # 旧图谱与手搭快照照旧能走
    fog = plain.model_copy(update={"discovery": DiscoveryStatus.UNKNOWN})
    assert (fog.known, fog.shown_name, fog.names) == (False, UNKNOWN_PLACE, ("南下",))  # 名字只剩标签（引擎内部的把手）
    told = plain.model_copy(update={"discovery": DiscoveryStatus.TOLD})
    assert told.shown_name == "大理城"


def test_handles_number_exits_that_share_a_direction() -> None:
    south = ExitView(label="南下", to_id=CITY, to_name="大理城", direction=D.SOUTH)
    path = ExitView(label="山径", to_id="loc:山道", to_name="山道", direction=D.SOUTH, discovery=DiscoveryStatus.UNKNOWN)
    down = ExitView(label="崖下", to_id=CAVE, to_name="无量玉洞", direction=D.DOWN, discovery=DiscoveryStatus.UNKNOWN)
    snap = _snap(south, path, down)
    assert [snap.handle(e) for e in snap.exits] == ["南·一", "南·二", "下"]
    assert snap.exit_names(south) == ("南下", "大理城", "南·一")
    assert snap.exit_names(path) == ("山径", "南·二")  # 未知去处：只认标签与把手
    assert _snap(south).handle(south) == "南"


async def test_the_memory_graph_fills_ways_and_acquaintance() -> None:
    _, snap = await look(WORLD, HILL)
    seen = {e.to_id: (e.label, e.direction, e.travel_method, e.time_cost, e.discovery) for e in snap.exits}
    assert seen == {CITY: ("南下", D.SOUTH, M.WALK, 4, DiscoveryStatus.UNKNOWN),
                    CAVE: ("崖下", D.DOWN, M.WALK, 4, DiscoveryStatus.UNKNOWN)}
    famous = _bp(sights=(Sight(location_id=CITY, renowned=True, sources=("chunk:1",)),
                         Sight(location_id=CAVE, landmark=True, sources=("chunk:1",))))
    _, known = await look(famous, HILL)
    assert {e.to_id: e.discovery for e in known.exits} == {CITY: DiscoveryStatus.RENOWNED, CAVE: DiscoveryStatus.SIGHTED}
    _, back = await look(WORLD, HILL, Moved(from_location_id=HILL, to_location_id=CITY, exit_label="南下"))
    assert {e.to_id: e.discovery for e in back.exits} == {HILL: DiscoveryStatus.VISITED, WUXI: DiscoveryStatus.UNKNOWN}
    assert {e.to_id: back.handle(e) for e in back.exits} == {HILL: "北", WUXI: "东"}


# ============================================================
#  规则 —— 迷雾里的移动、注记的耗时、问路
# ============================================================
async def test_an_unknown_place_is_reached_by_its_bearing_until_someone_names_it() -> None:
    state, snap = await look(WORLD, HILL)
    south = [Moved(from_location_id=HILL, to_location_id=CITY, exit_label="南下")]
    assert decide(act(ActionType.MOVE, target_entity="南"), state, snap) == south
    refused = decide(act(ActionType.MOVE, target_entity="大理城"), state, snap)
    assert refused[0].reason_code == "NO_PATH"  # type: ignore[attr-defined]
    assert "未知区域" in refused[0].reason and "崖下" not in refused[0].reason  # type: ignore[attr-defined]
    told, snap = await look(WORLD, HILL, PlacesLearned(location_ids=(CITY,), source_id="chr:左子穆"))
    assert decide(act(ActionType.MOVE, target_entity="大理城"), told, snap) == south


async def test_an_authored_passage_sets_the_cost_of_the_move() -> None:
    slow = _bp(passages=(road(HILL, CITY, D.SOUTH, 96, M.RIDE),))
    state, snap = await look(slow, HILL)
    assert command(act(ActionType.MOVE, target_entity="南"), state, snap).time_cost == 96
    assert command(act(ActionType.MOVE, target_entity="崖下"), state, snap).time_cost == 4
    assert command(act(ActionType.MOVE, target_entity="北"), state, snap).time_cost == 1  # 无路：驳回只花一刻


async def test_asking_the_way_names_the_unknown_neighbours() -> None:
    state, snap = await look(WORLD, HILL)
    asked = act(ActionType.TALK, target_entity="左子穆", topic="无量山")
    assert decide(asked, state, snap) == [
        Conversed(npc_id="chr:左子穆", topic_id=HILL),
        PlacesLearned(location_ids=(CITY, CAVE), source_id="chr:左子穆"),  # 此地相邻去处，按 id 排序
    ]
    by_name = decide(act(ActionType.TALK, target_entity="辛双清", topic="大理城"), state, snap)  # 话题是某个去处，同样是问路
    assert by_name[-1] == PlacesLearned(location_ids=(CITY, CAVE), source_id="chr:辛双清")
    assert decide(act(ActionType.TALK, target_entity="左子穆", topic="辛双清"), state, snap) == [
        Conversed(npc_id="chr:左子穆", topic_id="chr:辛双清")]  # 话题不是地点：只是闲谈
    parley = decide(act(ActionType.TALK, target_entity="左子穆", topic="无量山", approach=Approach.WORDS), state, snap)
    assert isinstance(parley[0], Parleyed) and not any(isinstance(e, PlacesLearned) for e in parley)  # 交涉不是问路
    told, snap = await look(WORLD, HILL, *decide(asked, state, snap))
    assert decide(asked, told, snap) == [Conversed(npc_id="chr:左子穆", topic_id=HILL)]  # 认得全了，不再出
    assert directions(HILL, told, snap) == ()


async def test_asking_the_way_skips_what_you_already_know() -> None:
    went = Moved(from_location_id=HILL, to_location_id=CITY, exit_label="南下")
    state, snap = await look(WORLD, HILL, went)
    assert decide(act(ActionType.TALK, target_entity="段誉", topic="大理城"), state, snap)[-1] == PlacesLearned(
        location_ids=(WUXI,), source_id="chr:段誉")  # 来路无量山到过了
    famous = _bp(sights=(Sight(location_id=CITY, renowned=True, sources=("chunk:1",)),))
    state, snap = await look(famous, HILL)
    assert directions(HILL, state, snap) == (CAVE,)  # 名胜不用问
    assert directions("chr:左子穆", state, snap) == () and directions(None, state, snap) == ()
    hut = Location(id="loc:草庐", name="草庐", region="大理", description="山脚草庐", exits={"上山": HILL})
    state, snap = await look(_bp(locations=(*WORLD.locations, hut)), "loc:草庐")
    assert directions("loc:草庐", state, snap) == (HILL,)
