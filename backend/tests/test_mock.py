"""
[INPUT]: 依赖 pytest 的 parametrize，依赖 app.llm.mock 的 MockLLM，依赖 app.director.prompts 的 SYSTEM_PROMPT / build_opening / build_turn，
         依赖 app.director.lethal 的 SAFE / Verdict、app.director.fallback 的 execution，依赖 app.engine 的 begin / snapshot_of / LifeView，
         依赖 app.events 的 LifeBegan、app.lore 的 GRANDMASTERS / OpeningSeed、app.schemas 的 DIRECTOR_SCHEMA / DirectorOutput / TagDelta，
         依赖 conftest 的 PLAYER / OPTIONS
[OUTPUT]: Mock 导演的行为单测：开局写破种子点名的绝顶高手、"拾/捡"上报「锈蚀铁牌」、"烧/毁"上报以当前地点为标签的世界大事、
          必死指令产出与 fallback.execution 一致的处决、SAFE 回合存活且有选项、时辰前进一格（含亥时→子时回绕）
[POS]: tests 中守护"零密钥替身读懂同一份提示词协议"的用例集：Prompt 一律由 build_opening / build_turn 从手造的 LifeView 与
       lethal.Verdict 组装，输出一律经 DirectorOutput 契约校验后再断言——Mock 与真实大模型走同一条读写协议，协议一改即报错
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import asyncio
import random
from uuid import uuid4

import pytest

from app.director import fallback
from app.director.lethal import SAFE, Verdict
from app.director.prompts import SYSTEM_PROMPT, build_opening, build_turn
from app.engine import LifeView, begin, snapshot_of
from app.events import LifeBegan
from app.llm.mock import MockLLM
from app.lore import GRANDMASTERS, OpeningSeed
from app.schemas import DIRECTOR_SCHEMA, DirectorOutput, TagDelta
from conftest import OPTIONS, PLAYER

QIAO_FENG = next(master for master in GRANDMASTERS if master.name == "萧峰")


def _direct(user: str) -> DirectorOutput:
    """让 Mock 读一份真实 Prompt，并把它吐出的文本交给导演契约终审。"""
    text = asyncio.run(MockLLM(rng=random.Random(7)).complete(SYSTEM_PROMPT, user, DIRECTOR_SCHEMA))
    return DirectorOutput.model_validate_json(text)


def _view(*, time: str = PLAYER.time, entities: tuple[str, ...] = ()) -> LifeView:
    """以 PLAYER 开局的一条命：经 LifeBegan 事件投影，而非手写视图字段。"""
    player = PLAYER.model_copy(update={"time": time})
    began = LifeBegan(world_id=uuid4(), player=player, scene="邻桌一条魁梧大汉独据一桌。", options=OPTIONS, entities=entities)
    return begin(uuid4(), began)


def _turn(view: LifeView, action: str, verdict: Verdict = SAFE) -> DirectorOutput:
    return _direct(build_turn(view, (), "custom", action, verdict))


# ============================================================
#  开局 —— 叙事可以含蓄，arrived 必须写破
# ============================================================
@pytest.mark.parametrize(
    ("premise", "arrived"),
    [
        ("邻桌乔帮主正与慕容公子隔窗对饮，谁也不看谁一眼。", ("萧峰", "慕容复")),
        ("冷雨打在太湖的芦苇上，四下里一个人影也没有。", ()),
    ],
    ids=["aliases_resolved_to_names", "nobody_named"],
)
def test_opening_brings_grandmasters_named_in_premise_into_view(premise: str, arrived: tuple[str, ...]) -> None:
    out = _direct(build_opening(OpeningSeed(PLAYER, premise, arrived), ()))
    assert out.local_delta.arrived == arrived
    assert out.game_over is False


# ============================================================
#  普通回合 —— 两条确定性规则走通两种记账
# ============================================================
@pytest.mark.parametrize(
    ("action", "inventory"),
    [
        ("拾起脚边那枚铁牌", TagDelta(add=("锈蚀铁牌",), remove=())),
        ("捡起地上的铁牌揣进怀里", TagDelta(add=("锈蚀铁牌",), remove=())),
        ("静观其变", TagDelta(add=(), remove=())),
    ],
    ids=["pick_up_shi", "pick_up_jian", "idle"],
)
def test_picking_up_reports_rusty_token_to_inventory(action: str, inventory: TagDelta) -> None:
    assert _turn(_view(), action).player_delta.inventory == inventory


@pytest.mark.parametrize(
    ("action", "count"),
    [("放火烧了这座酒楼", 1), ("一掌毁了这座酒楼的招牌", 1), ("静观其变", 0)],
    ids=["raze_shao", "raze_hui", "idle"],
)
def test_razing_reports_world_event_tagged_with_current_location(action: str, count: int) -> None:
    view = _view()
    events = _turn(view, action).world_events
    assert len(events) == count
    assert all(view.local.location in event.tags for event in events)


@pytest.mark.parametrize(("before", "after"), [("午时", "未时"), ("亥时", "子时")], ids=["forward", "wrap_around"])
def test_safe_turn_keeps_player_alive_and_advances_one_shichen(before: str, after: str) -> None:
    out = _turn(_view(time=before), "静观其变")
    assert out.next_state.time == after
    assert out.game_over is False and out.options is not None  # SAFE 回合：Mock 无权判死，且必须给出下一步选项


# ============================================================
#  必死 —— directive 一旦是处决，Mock 只复述 fallback 的确定性结局
# ============================================================
def test_lethal_directive_yields_fallback_execution() -> None:
    view = _view(entities=(QIAO_FENG.name,))
    out = _turn(view, "一拳打向乔峰", Verdict(killer=QIAO_FENG))
    assert out.game_over is True and out.options is None
    assert QIAO_FENG.signature in out.next_state.health_status
    assert out == fallback.execution(snapshot_of(view.player), QIAO_FENG.name, QIAO_FENG.signature)
