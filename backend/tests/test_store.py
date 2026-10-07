"""
[INPUT]: 依赖标准库 sqlite3 / contextlib，依赖 pytest，依赖 app.store 的 EventStore、app.events 的事件模型、app.errors 的 SessionBusyError、
         app.schemas 的 NO_PLAYER_CHANGE / WorldEvent，依赖 conftest 的 store / db_path 夹具、begin_life() / snapshot() 与 PLAYER / OPTIONS
[OUTPUT]: 事件库单测：追加后读回保型保序、触发器强制只追加（外部连接 UPDATE/DELETE 均被拒）、乐观并发冲突整批回滚（life 与 world 同生共死）、
          world 流跨投胎按追加序延续、world_exists 与世界隔离、关库重开后事件完整
[POS]: tests 中守护"事件日志是唯一事实来源"的持久化用例集；事件库落在 pytest 的临时目录，只经 EventStore 公共接口读写
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import sqlite3
from contextlib import closing
from uuid import UUID, uuid4

import pytest

from app.errors import SessionBusyError
from app.events import LifeBegan, TurnResolved, WorldEventRecorded
from app.schemas import NO_PLAYER_CHANGE, WorldEvent
from app.store import EventStore
from conftest import OPTIONS, PLAYER, begin_life, snapshot


# ============================================================
#  构造器
# ============================================================
def _turn(action: str) -> TurnResolved:
    return TurnResolved(
        action_type="custom",
        action=action,
        scene=f"{action}之后，楼中一片寂静。",
        snapshot=snapshot(),
        changes=NO_PLAYER_CHANGE,
        entities=("乔峰",),
        options=OPTIONS,
        died=False,
    )


def _recorded(life_id: UUID, desc: str) -> WorldEventRecorded:
    return WorldEventRecorded(life_id=life_id, event=WorldEvent(tags=("松鹤楼",), event_desc=desc))


# ============================================================
#  读写往返
# ============================================================
def test_appended_events_read_back_typed_and_ordered(store: EventStore) -> None:
    view = begin_life(store)
    turns = (_turn("静观其变"), _turn("低声询问"))
    store.append(life_id=view.life_id, world_id=view.world_id, expected_version=view.version, life=turns)
    began, *rest = store.load_life(view.life_id)
    assert isinstance(began, LifeBegan)
    assert (began.world_id, began.player) == (view.world_id, PLAYER)
    assert tuple(rest) == turns


# ============================================================
#  只追加 —— 数据库触发器拒绝一切改写
# ============================================================
@pytest.mark.parametrize("statement", ["UPDATE events SET payload = '{}'", "DELETE FROM events"], ids=["update", "delete"])
def test_events_are_append_only(store: EventStore, db_path: str, statement: str) -> None:
    view = begin_life(store)
    before = store.load_life(view.life_id)
    with closing(sqlite3.connect(db_path, isolation_level=None)) as rogue:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            rogue.execute(statement)
    assert store.load_life(view.life_id) == before


# ============================================================
#  乐观并发 —— 过期版本整批回滚，世界线不分叉
# ============================================================
def test_stale_version_rolls_back_whole_batch(store: EventStore) -> None:
    view = begin_life(store)
    store.append(
        life_id=view.life_id,
        world_id=view.world_id,
        expected_version=view.version,
        life=(_turn("夺门而走"),),
        world=(_recorded(view.life_id, "玩家夺门逃出松鹤楼"),),
    )
    life, world = store.load_life(view.life_id), store.load_world(view.world_id)
    with pytest.raises(SessionBusyError):
        store.append(
            life_id=view.life_id,
            world_id=view.world_id,
            expected_version=view.version,
            life=(_turn("掀翻酒桌"), _turn("拔刀相向")),
            world=(_recorded(view.life_id, "玩家掀翻了乔峰的酒桌"),),
        )
    assert store.load_life(view.life_id) == life
    assert store.load_world(view.world_id) == world
    # 回滚彻底：事务已关闭，按最新版本的追加照常成功
    store.append(life_id=view.life_id, world_id=view.world_id, expected_version=len(life), life=(_turn("静观其变"),))
    assert len(store.load_life(view.life_id)) == len(life) + 1


# ============================================================
#  世界流 —— 跨投胎延续，世界之间互不可见
# ============================================================
def test_world_stream_spans_lives_in_append_order(store: EventStore) -> None:
    world_id = uuid4()
    first = begin_life(store, world_id=world_id)
    earlier = _recorded(first.life_id, "玩家掀翻了乔峰的酒桌")
    store.append(
        life_id=first.life_id, world_id=world_id, expected_version=first.version, life=(_turn("掀桌"),), world=(earlier,)
    )
    second = begin_life(store, world_id=world_id)
    later = (_recorded(second.life_id, "玩家抢走了段誉的折扇"), _recorded(second.life_id, "玩家火烧了松鹤楼"))
    store.append(life_id=second.life_id, world_id=world_id, expected_version=second.version, life=(_turn("放火"),), world=later)
    assert store.load_world(world_id) == (earlier, *later)


def test_world_exists_only_for_logged_worlds(store: EventStore) -> None:
    view = begin_life(store)
    assert store.world_exists(view.world_id)
    assert not store.world_exists(uuid4())


def test_worlds_do_not_see_each_other(store: EventStore) -> None:
    here, there = begin_life(store), begin_life(store)
    mine, theirs = _recorded(here.life_id, "玩家火烧了松鹤楼"), _recorded(there.life_id, "玩家拜入了丐帮")
    for view, recorded in ((here, mine), (there, theirs)):
        store.append(
            life_id=view.life_id, world_id=view.world_id, expected_version=view.version, life=(_turn("出招"),), world=(recorded,)
        )
    assert store.load_world(here.world_id) == (mine,)
    assert store.load_world(there.world_id) == (theirs,)


# ============================================================
#  持久化 —— 关库重开，事件完整
# ============================================================
def test_events_survive_reopen(store: EventStore, db_path: str) -> None:
    view = begin_life(store)
    store.append(
        life_id=view.life_id,
        world_id=view.world_id,
        expected_version=view.version,
        life=(_turn("夺门而走"),),
        world=(_recorded(view.life_id, "玩家夺门逃出松鹤楼"),),
    )
    life, world = store.load_life(view.life_id), store.load_world(view.world_id)
    store.close()
    reopened = EventStore(db_path)
    try:
        assert len(life) == 2 and len(world) == 1
        assert reopened.load_life(view.life_id) == life
        assert reopened.load_world(view.world_id) == world
    finally:
        reopened.close()
