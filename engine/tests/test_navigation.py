"""
[INPUT]: 依赖 app.application.navigation 的 NavigationOption / navigation / duration_label，依赖 app.application.options 的 OptionGenerator，
         依赖 app.domain 的 rules / events / geography / models / commands，依赖 tests/test_rules 的 scene() 快照工厂与 PID
[OUTPUT]: 方位导航的单测：中文时长（刻 / 半个时辰 / 时辰 / 半日 / 日）、每条获准的出路一项且按 COMPASS 次序、指令是 MOVE + 方位把手（不用标签与地名）、
          耗时等于那条出路的 time_cost 且等于 rules.command、未知去处只露「未知区域」（下发的字段里没有标签与地名）、认知随旅程演化（亲历、问路）、
          道路注记的交通方式与耗时照实下发、同一方位两条出路各有把手且各通各处、id 随所在之地而变、点了即走得通（rules.decide 落成那条 Moved）、
          仇人在侧时恰有一条 retreat 且就是 rules.retreat 那条（先走来路）、无仇人即无 retreat、死者没有导航、导航与交互选项的 id 互不相撞
[POS]: tests 的导航单元层：test_options 管交互选项，test_option_metrics 管实录重放上的验收（含仇人在侧 retreat 100%），这里管方位导航本身
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from dataclasses import replace

import pytest

from app.application.navigation import NavigationOption, duration_label, navigation
from app.application.options import OptionGenerator
from app.domain import rules
from app.domain.events import Moved, PlacesLearned, RelationChanged
from app.domain.geography import COMPASS, UNKNOWN_PLACE, Direction, DiscoveryStatus, TravelMethod
from app.domain.intent import ActionType
from app.domain.models import Attitude
from app.domain.snapshot import ExitView, LocalSnapshot
from tests.test_rules import scene

GRUDGE = RelationChanged(character_id="chr:龚光杰", attitude=Attitude.HOSTILE, cause="遭你出手相攻")


def _exit(snap: LocalSnapshot, nav: NavigationOption) -> ExitView:
    return next(e for e in snap.exits if snap.handle(e) == nav.intent.target_entity)


@pytest.mark.parametrize(("ticks", "label"), [
    (1, "一刻"), (2, "两刻"), (3, "三刻"), (4, "约半个时辰"), (5, "约半个时辰"), (6, "约一个时辰"), (8, "约一个时辰"),
    (12, "约两个时辰"), (24, "约三个时辰"), (35, "约四个时辰"), (36, "约半日"), (48, "约半日"), (72, "约一日"),
    (96, "约一日"), (144, "约一日半"), (192, "约两日"), (288, "约三日"), (672, "约七日"),
])
def test_durations_read_like_a_storyteller(ticks: int, label: str) -> None:
    assert duration_label(ticks) == label


async def test_every_approved_exit_is_one_compass_item() -> None:
    state, snap = await scene("loc:无量山")
    nav = navigation(state, snap)
    assert len(nav) == len(snap.exits) == 2
    assert [n.direction for n in nav] == [Direction.SOUTH, Direction.DOWN]  # COMPASS 次序：东南西北在前，上下在后
    assert [COMPASS.index(n.direction) for n in nav] == sorted(COMPASS.index(n.direction) for n in nav)
    for n in nav:
        way = _exit(snap, n)
        assert n.intent.action_type is ActionType.MOVE and n.intent.target_entity == snap.handle(way) == n.direction.value
        assert n.underlying_command == rules.command(n.intent, state, snap)
        assert n.time_cost == way.time_cost == n.underlying_command.time_cost and n.time_label == duration_label(n.time_cost)
        assert n.travel_method is way.travel_method and n.id.startswith("nav-") and len(n.id) == 12
    assert len({n.id for n in nav}) == len(nav)
    assert not {n.id for n in nav} & {o.id for o in OptionGenerator().affordances(state, snap)}  # 点选按 id 在两张表里核验，互不相撞
    assert navigation(state, snap) == nav  # 世界不变，逐字不变


async def test_unknown_places_stay_in_the_fog() -> None:
    """未知的去处只露「未知区域」：下发的字段里没有出口标签、没有地名，指令也只用方位把手（turn_resolved 的意图同样不露名）。"""
    state, snap = await scene("loc:无量山")
    for n in navigation(state, snap):
        way = _exit(snap, n)
        assert n.discovery is DiscoveryStatus.UNKNOWN and n.target == UNKNOWN_PLACE
        shown = n.model_dump_json(exclude={"underlying_command"}) + n.intent.model_dump_json()
        assert way.label not in shown and way.to_name not in shown


async def test_discovery_grows_with_the_journey() -> None:
    went = Moved(from_location_id="loc:无量山", to_location_id="loc:大理城", exit_label="南下")
    back = Moved(from_location_id="loc:大理城", to_location_id="loc:无量山", exit_label="北上")
    state, snap = await scene("loc:无量山", went, back)
    south, down = navigation(state, snap)
    assert (south.target, south.discovery) == ("大理城", DiscoveryStatus.VISITED)  # 亲历
    assert (down.target, down.discovery) == (UNKNOWN_PLACE, DiscoveryStatus.UNKNOWN)
    told = PlacesLearned(location_ids=("loc:无量玉洞",), source_id="chr:辛双清")
    state, snap = await scene("loc:无量山", went, back, told)
    assert [(n.target, n.discovery) for n in navigation(state, snap)] == [
        ("大理城", DiscoveryStatus.VISITED), ("无量玉洞", DiscoveryStatus.TOLD),  # 问路得知
    ]


async def test_authored_roads_are_reported_as_they_are() -> None:
    """道路注记的交通方式与耗时照实下发，玩家点了花的就是这么多刻（rules.command 以那条出路的耗时为准）。"""
    state, snap = await scene("loc:大理城")
    road = snap.model_copy(update={"exits": tuple(
        e.model_copy(update={"travel_method": TravelMethod.RIDE, "time_cost": 24}) if e.to_id == "loc:无量山" else e
        for e in snap.exits
    )})
    north = next(n for n in navigation(state, road) if n.direction is Direction.NORTH)
    assert (north.travel_method, north.time_cost, north.time_label) == (TravelMethod.RIDE, 24, "约三个时辰")
    assert north.underlying_command.time_cost == 24


async def test_two_roads_one_direction_get_their_own_handles() -> None:
    state, snap = await scene("loc:大理城")
    east = snap.model_copy(update={"exits": tuple(e.model_copy(update={"direction": Direction.EAST}) for e in snap.exits)})
    nav = navigation(state, east)
    assert [n.intent.target_entity for n in nav] == ["东·一", "东·二"] and len({n.id for n in nav}) == 2
    for n in nav:
        moved = rules.decide(n.intent, state, east)
        assert isinstance(moved[0], Moved) and moved[0].to_location_id == _exit(east, n).to_id  # 各通各处


async def test_the_same_handle_elsewhere_is_another_item() -> None:
    """id 的摘要带着所在之地：同一个「南」换了地方就是另一个 id，过期的点选在新地方落空。"""
    state, snap = await scene("loc:无量山")
    elsewhere = snap.model_copy(update={"location": snap.location.model_copy(update={"id": "loc:别处"})})
    here, there = navigation(state, snap)[0], navigation(state, elsewhere)[0]
    assert here.intent == there.intent and here.id != there.id


async def test_a_click_walks_that_road() -> None:
    state, snap = await scene("loc:无量山")
    for n in navigation(state, snap):
        moved = rules.decide(n.intent, state, snap)
        assert isinstance(moved[0], Moved) and moved[0].to_location_id == _exit(snap, n).to_id


async def test_retreat_marks_the_road_rules_would_flee_by() -> None:
    state, snap = await scene("loc:无量山")
    assert not any(n.retreat for n in navigation(state, snap))  # 四下无敌：没有脱身之路可言
    went = Moved(from_location_id="loc:无量山", to_location_id="loc:无量玉洞", exit_label="崖下")
    back = Moved(from_location_id="loc:无量玉洞", to_location_id="loc:无量山", exit_label="攀上")
    state, snap = await scene("loc:无量山", went, back, GRUDGE)
    nav = navigation(state, snap)
    flee = [n for n in nav if n.retreat]
    assert len(flee) == 1 and _exit(snap, flee[0]).to_id == rules.retreat(state, snap).to_id == "loc:无量玉洞"  # type: ignore[union-attr]
    assert flee[0].direction is Direction.DOWN  # 先走来路
    subdued = snap.model_copy(update={"characters": tuple(
        c.model_copy(update={"subdued": True}) if c.id == "chr:龚光杰" else c for c in snap.characters
    )})
    assert not any(n.retreat for n in navigation(state, subdued))  # 被制住的仇人不算仇人在侧


async def test_the_dead_have_nowhere_to_go() -> None:
    state, snap = await scene("loc:无量山")
    assert navigation(replace(state, alive=False), snap) == ()
