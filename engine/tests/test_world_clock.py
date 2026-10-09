"""
[INPUT]: 依赖 tests/conftest 的 container / settings / play / spawned_at，依赖 tests/world 的 WORLD，依赖 app.container 的 build_container，
         依赖 app.application 的 bus（命令与回合消息）/ narrator（Narrator / NarrationRequest / ShortTermMemory），
         依赖 domain 的 commands（SPAWN_TICK / time_label）/ ambient（ActivityKind / ActivityState）/ heartbeat（BLOOD / LITTER / ROUT_TICKS）/
         events（世界心跳七事件与 Moved）/ intent（PlayerIntent）/ lore（SwarmNode）/ models（Location / WorldBlueprint）
[OUTPUT]: 世界心跳的端到端用例（经组合根装配的完整引擎，离线解析 + 确定性裁决 + 白描说书人）：
          每条命令都花时间（沉思 1 刻、同一处所内走动 1 刻、换处所 4 刻、交手 1 刻、调息 8 刻、驳回 1 刻）且状态栏时辰随之走，投胎与续前缘不走时间；
          交手在此地留下往事（已结束的交手活动）与血迹，走开再回来快照里仍在，过了一日血迹消散、往事随之了结；
          带人群的蓝图里出手的烈度高过惊惧阈值即人群溃散（白描只有这一句出声，痕迹与消息不出声）、留下狼藉、有人群目睹的消息每刻传两处，半日后人群回来；
          消息只在传到之处进快照（物归原主的消息只传一跳：无量山知道、无量玉洞不知道）；
          跨进新地方的回合叙事请求带短期记忆 recollection（刚离开之地、眼中所见、此行所为），没跨的回合没有，夺路而逃也算跨；
          意图的此行所为随 Moved 入账、PlayerState.motivation 记住、NarrationRequest.motivation 恒取它；死者没有心跳
[POS]: tests 的世界心跳验收：三类时空连续性 bug（精神时光屋、跨房间记忆清空、全知视角幻觉）各有一条端到端用例守着
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.application.bus import ResumePlayer, SubmitText, TurnCompleted, TurnResolved
from app.application.narrator import NarrationRequest, Narrator
from app.config import Settings
from app.container import Container, build_container
from app.domain.ambient import ActivityKind, ActivityState
from app.domain.commands import SPAWN_TICK, time_label
from app.domain.events import (
    ActivityStarted,
    DomainEvent,
    FactTokenSpawned,
    ItemDecayed,
    ItemPilfered,
    Moved,
    PlayerDied,
    RumorSpread,
    TimePassed,
    TraceLeft,
)
from app.domain.heartbeat import BLOOD, LITTER, ROUT_TICKS
from app.domain.intent import ActionType, PlayerIntent
from app.domain.lore import SwarmNode
from app.domain.models import Location, WorldBlueprint
from app.domain.snapshot import LocalSnapshot
from app.errors import PlayerDeadError
from tests.conftest import kinds, play, spawned_at
from tests.world import WORLD

HEARTBEAT = (TimePassed, ActivityStarted, TraceLeft, FactTokenSpawned, RumorSpread, ItemDecayed, ItemPilfered)
CROWD = "看热闹的山民"
ROUTED = f"{CROWD}惊惶四散，一哄而逃。"


def _with(**changes: Any) -> WorldBlueprint:
    return WORLD.model_copy(update=changes)


# 无量山之内多一处剑湖宫（「上级·处所」同一上级）：进出只花一刻
NESTED = _with(locations=(
    *(loc.model_copy(update={"exits": {**loc.exits, "入宫": "loc:无量山·剑湖宫"}}) if loc.id == "loc:无量山" else loc
      for loc in WORLD.locations),
    Location(id="loc:无量山·剑湖宫", name="无量山·剑湖宫", region="大理", description="东宗的宫观",
             exits={"出宫": "loc:无量山"}),
))
# 无量山上一群看热闹的山民：惊惧阈值 3，交手（烈度 ≥ 4）即溃散
CROWDED = _with(swarms=(SwarmNode(id=f"swm:{CROWD}", name=CROWD, location_id="loc:无量山", size=30, panic_threshold=3,
                                  routine="围观比剑", sources=("chunk:1",)),))


# ============================================================
#  替身：意图原样落地（离线解析器不认得沉思与此行所为），叙事请求逐条记下
# ============================================================
class Literal:
    """意图替身：JSON 原样落成 PlayerIntent，其余文本交给原来的解析器——只替换「意图从哪来」，下游一概不动。"""

    def __init__(self, fallback: Any) -> None:
        self._fallback = fallback

    async def parse(self, text: str, scene: LocalSnapshot) -> PlayerIntent:
        if text.startswith("{"):
            return PlayerIntent.model_validate_json(text)
        return await self._fallback.parse(text, scene)


class Listening(Narrator):
    """说书人替身：记下每一份 NarrationRequest，再交给原来的说书人照常渲染。"""

    def __init__(self, inner: Narrator) -> None:
        self._inner = inner
        self.requests: list[NarrationRequest] = []

    async def narrate(self, request: NarrationRequest) -> AsyncIterator[str]:
        self.requests.append(request)
        async for chunk in self._inner.narrate(request):
            yield chunk


def rig(container: Container) -> Listening:
    pipeline = container.pipeline
    pipeline.parser = Literal(pipeline.parser)  # type: ignore[assignment]
    listening = Listening(pipeline._narrator)
    pipeline._narrator = listening
    return listening


def intent(action: ActionType, target: str | None = None, motivation: str = "") -> str:
    return PlayerIntent(action_type=action, target_entity=target, motivation=motivation).model_dump_json(exclude_defaults=True)


async def turn(container: Container, pid: str, said: str) -> tuple[TurnResolved, TurnCompleted, list[DomainEvent]]:
    """一回合：结果帧、终帧与这一回合新入账的事件。"""
    before = len(await container.store.load(pid))
    messages = await play(container, SubmitText(player_id=pid, text=said))
    assert kinds(messages)[0] == "TurnResolved" and kinds(messages)[-1] == "TurnCompleted"
    fresh = [e.event for e in (await container.store.load(pid))[before:]]
    return messages[0], messages[-1], fresh  # type: ignore[return-value]


def spent(events: list[DomainEvent]) -> int:
    passed = [e for e in events if isinstance(e, TimePassed)]
    assert len(passed) <= 1  # 一条命令只走一次时间
    return passed[0].ticks if passed else 0


async def here(container: Container, pid: str) -> LocalSnapshot:
    return await container.reader.local_snapshot(pid)


# ============================================================
#  精神时光屋：每条命令都花时间
# ============================================================
async def test_every_command_spends_time_and_the_status_bar_tells_it(settings: Settings) -> None:
    container = await build_container(settings, blueprint=NESTED)
    try:
        rig(container)
        pid = await spawned_at(container, "无量山")
        opening = (await play(container, ResumePlayer(player_id=pid)))[-1]  # 投胎与续前缘不走时间
        assert isinstance(opening, TurnCompleted) and opening.status.time == "第一日·辰正"
        assert len(await container.store.load(pid)) == 1

        tick = SPAWN_TICK
        for said, cost in (
            (intent(ActionType.THINK), 1),  # 沉思：只花时间，一刻
            (intent(ActionType.MOVE, "入宫"), 1),  # 同一处所之内走动：一刻
            (intent(ActionType.MOVE, "出宫"), 1),
            ("去南下", 4),  # 换一处所：一个时辰的脚程
            ("去北上", 4),
            ("徒手攻击狠辣的龚光杰", 1),  # 交手一刻（重伤沿来路夺路而逃也算在这一刻里）
            ("就地打坐疗伤", 8),  # 逃到大理城调息：一个时辰
            ("学六脉神剑", 1),  # 驳回：试过、没成，也花了一刻
        ):
            _, done, events = await turn(container, pid, said)
            tick += cost
            assert spent(events) == cost, said
            assert done.status.time == time_label(tick) and (await here(container, pid)).tick == tick, said
        assert tick == SPAWN_TICK + 21 and done.status.time == "第一日·未初一刻"
        quiet = (await play(container, ResumePlayer(player_id=pid, quiet=True)))[-1]
        assert isinstance(quiet, TurnCompleted) and quiet.status.time == "第一日·未初一刻"  # quiet 续接也不走时间
    finally:
        await container.aclose()


async def test_thinking_writes_only_time(container: Container) -> None:
    """沉思与静观不改变世界：一条命令只写一条 TimePassed，白描一句不出。"""
    rig(container)
    pid = await spawned_at(container, "无量山")
    resolved, done, events = await turn(container, pid, intent(ActionType.THINK))
    assert events == [TimePassed(ticks=1)] and resolved.facts == ()
    assert resolved.intent is not None and resolved.intent.action_type is ActionType.THINK
    assert done.status.time == "第一日·辰正一刻"


# ============================================================
#  过去式持久化：往事与痕迹留在发生之地，随时间消散
# ============================================================
async def test_a_fight_leaves_its_mark_where_it_happened(container: Container) -> None:
    pid = await spawned_at(container, "无量山")
    resolved, _, _ = await turn(container, pid, "攻击左子穆")
    assert resolved.facts[0] == "阿星徒手向左子穆出手——吃了点亏，带着轻伤退开。"  # 见了血
    assert not any("血迹" in f or "动手" in f for f in resolved.facts)  # 痕迹与消息不出声
    snap = await here(container, pid)
    (fight,) = snap.activities
    assert fight.kind is ActivityKind.FIGHT and set(fight.participants) == {pid, "chr:左子穆"}
    assert fight.state is ActivityState.ENDED and fight.started_tick == SPAWN_TICK  # 交手一刻即止，此后是往事
    assert [(t.description, t.remaining) for t in snap.traces] == [(BLOOD.description, BLOOD.decay_ticks - 1)]
    assert [r.text for r in snap.rumors] == ["阿星与左子穆动手，挂了彩"]

    await turn(container, pid, "去南下")
    away = await here(container, pid)
    assert away.location.name == "大理城" and away.activities == () and away.traces == ()  # 往事属于无量山
    await turn(container, pid, "去北上")
    back = await here(container, pid)  # 走开再回来：往事与血迹都还在
    assert [a.id for a in back.activities] == [fight.id]
    assert [t.remaining for t in back.traces] == [BLOOD.decay_ticks - 9]

    gone = SPAWN_TICK + BLOOD.decay_ticks  # 血迹一日方散
    while back.tick < gone:
        assert back.traces and back.activities
        await turn(container, pid, "去南下")
        await turn(container, pid, "去北上")
        back = await here(container, pid)
    assert back.location.name == "无量山" and back.traces == ()
    assert back.activities == ()  # 已结束的往事随它的痕迹一同了结


async def test_a_crowd_routs_and_comes_back_by_noon(settings: Settings) -> None:
    container = await build_container(settings, blueprint=CROWDED)
    try:
        listening = rig(container)
        pid = await spawned_at(container, "无量山")
        (calm,) = (await here(container, pid)).swarms
        assert not calm.routed and calm.current_state == "围观比剑"

        resolved, _, events = await turn(container, pid, "攻击左子穆")
        assert resolved.facts[-1] == ROUTED  # 世界心跳里唯一出声的一句：眼前的人群溃散
        assert not any("狼藉" in f or "血迹" in f for f in resolved.facts)
        assert ROUTED in listening.requests[-1].facts  # 进叙事的 <settled_facts>
        token = next(e.token for e in events if isinstance(e, FactTokenSpawned))
        assert token.speed == 2  # 有人群目睹，消息每刻传两处
        snap = await here(container, pid)
        (fled,) = snap.swarms
        assert fled.routed and fled.current_state == "溃散逃离"
        assert {t.description for t in snap.traces} == {BLOOD.description, LITTER.description}
        assert any(a.kind is ActivityKind.ROUT and a.state is ActivityState.ONGOING for a in snap.activities)

        back = SPAWN_TICK + ROUT_TICKS  # 半日之后回来
        while snap.tick < back:
            assert snap.swarms[0].routed
            await turn(container, pid, "去南下")
            await turn(container, pid, "去北上")
            snap = await here(container, pid)
        (calm,) = snap.swarms
        assert not calm.routed and calm.current_state == "围观比剑"
        assert {t.description for t in snap.traces} == {BLOOD.description}  # 狼藉已散，血迹还在
        assert not any(a.kind is ActivityKind.ROUT for a in snap.activities)
        assert all(ROUTED not in r.facts for r in listening.requests[-2:])  # 溃散只宣告一次
    finally:
        await container.aclose()


# ============================================================
#  全知视角幻觉：消息只在传到之处
# ============================================================
async def test_news_is_known_only_where_it_has_spread(container: Container) -> None:
    """物归原主的消息烈度为零，只传一跳：大理城的人当场知道、无量山与无锡城随后知道，两跳之外的无量玉洞永远不知道。"""
    listening = rig(container)
    news = "阿星把玉佩还给了段正淳"
    pid = await spawned_at(container, "无量山")
    await turn(container, pid, "拾起玉佩")
    await turn(container, pid, "去南下")
    resolved, _, events = await turn(container, pid, "把玉佩交还段正淳")
    assert not any(news in f for f in resolved.facts)  # 消息经快照被感知，不被宣告
    token = next(e.token for e in events if isinstance(e, FactTokenSpawned))
    assert (token.text, token.origin_id, token.radius) == (news, "loc:大理城", 1)
    assert set(token.subject_ids) == {pid, "chr:段正淳", "itm:玉佩"}
    city = listening.requests[-1].snapshot  # 说书人看到的此地：在场之人知道这件事
    assert [(r.text, r.origin_id) for r in city.rumors] == [(news, "loc:大理城")]

    await turn(container, pid, "去北上")
    assert [r.text for r in (await here(container, pid)).rumors] == [news]  # 一跳：传到了
    await turn(container, pid, "去崖下")
    cave = await here(container, pid)
    assert cave.location.name == "无量玉洞" and cave.rumors == ()  # 两跳：没传到，此地的人不知道
    assert listening.requests[-1].snapshot.rumors == ()
    player = await container.pipeline.load(pid)
    (spread,) = player.state.tokens
    assert spread.reached == ("loc:大理城", "loc:无量山", "loc:无锡城")  # 传满一跳即停


# ============================================================
#  跨房间记忆清空：此行所为与短期记忆
# ============================================================
async def test_crossing_into_a_new_place_carries_a_short_term_memory(container: Container) -> None:
    listening = rig(container)
    pid = await spawned_at(container, "无量山")
    errand = "去大理城找段正淳"
    _, done, events = await turn(container, pid, intent(ActionType.MOVE, "南下", motivation=errand))
    moved = next(e for e in events if isinstance(e, Moved))
    assert moved.motivation == errand and done.status.location == "大理城"
    request = listening.requests[-1]
    memory = request.recollection
    assert memory is not None and memory.left == "无量山" and memory.motivation == errand
    assert any(line.startswith("左子穆") for line in memory.seen)  # 出发前眼中所见：刚离开之地的人
    assert request.motivation == errand
    assert (await container.pipeline.load(pid)).state.motivation == errand  # 聚合记住此行所为

    await turn(container, pid, intent(ActionType.THINK))  # 没跨地方：没有短期记忆，此行所为照旧
    assert listening.requests[-1].recollection is None and listening.requests[-1].motivation == errand

    await turn(container, pid, "去北上")  # 换个地方而没说为何：短期记忆照给，此行所为清空
    memory = listening.requests[-1].recollection
    assert memory is not None and memory.left == "大理城" and memory.motivation == ""
    assert any(line.startswith("段正淳") for line in memory.seen)
    assert listening.requests[-1].motivation == "" and (await container.pipeline.load(pid)).state.motivation == ""

    _, done, _ = await turn(container, pid, "徒手攻击狠辣的龚光杰")  # 夺路而逃也是跨进新地方
    assert done.status.location == "大理城"
    request = listening.requests[-1]
    assert request.fled is not None and request.recollection is not None and request.recollection.left == "无量山"


# ============================================================
#  死者没有心跳
# ============================================================
async def test_the_dead_have_no_heartbeat(container: Container) -> None:
    pid = await spawned_at(container, "无量山")
    _, done, events = await turn(container, pid, "偷袭南海鳄神")
    assert isinstance(events[-1], PlayerDied) and not any(isinstance(e, HEARTBEAT) for e in events)
    assert done.game_over and done.status.time == "第一日·辰正"  # 时辰停在出手那一刻
    length = len(await container.store.load(pid))
    with pytest.raises(PlayerDeadError):
        await play(container, SubmitText(player_id=pid, text=intent(ActionType.THINK)))
    resumed = (await play(container, ResumePlayer(player_id=pid, quiet=True)))[-1]
    assert isinstance(resumed, TurnCompleted) and resumed.status.time == "第一日·辰正"
    assert len(await container.store.load(pid)) == length
