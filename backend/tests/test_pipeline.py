"""
[INPUT]: 依赖标准库 asyncio / json，依赖 pytest 的 parametrize / monkeypatch / usefixtures，依赖 app.director.pipeline 的 Director 与 OPENING_SEEDS（开局种子注入点），
         依赖 app.director.fallback 的 STALLED_SCENE / DEFAULT_OPTIONS、app.director.prompts 的 SYSTEM_PROMPT / read_section，
         依赖 app.engine 的 LifeView / LocalEnvironment / project_life / project_world / game_state，依赖 app.store 的 EventStore，
         依赖 app.events / app.errors / app.lore / app.schemas 的契约，依赖 conftest 的 ScriptedLLM / alive / dead / reply / snapshot /
         begin_life / make_director / PLAYER / WINDOW / MEMORY_LIMIT 与 store 夹具
[OUTPUT]: 编排器 Director 的行为单测：回合只追加一条事实且响应即投影、不合法度（持续幻觉或幻觉与失联交错）原地停顿且保留世界大事与上一回合选项、
          带错重采样、LLMError 传播与恢复、必死回合的确定性处决与死亡封印、死者 / 未知 / 并发守卫及失败出招后释放守卫、绝学防线、
          JIT 召回（地点 / 身份 / 在场者别名 / 动作点名、条数上限）、复述的世界大事不重复追加、滑动窗口、换地图清空局部环境、
          开局与投胎（种子权威、返回可投胎的真实世界、开局 JIT、判死退回种子并登记在场高手、世界大事延续、未知世界、LLMError 耗尽）
[POS]: tests 中守护"大模型只提议、编排器校验后原子追加、状态是事件的纯投影"的用例集：一律经真实 EventStore 与 ScriptedLLM 驱动，
       断言落在事件库与投影上，而非管线内部细节；asyncio.run 驱动协程，不引入异步测试插件
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import asyncio
import json
from uuid import UUID, uuid4

import pytest

from app.director import Director, fallback, pipeline
from app.director.prompts import SYSTEM_PROMPT, read_section
from app.engine import LifeView, LocalEnvironment, game_state, project_life, project_world
from app.errors import LLMError, NotFoundError, SessionBusyError, SessionDeadError
from app.events import LifeBegan, TurnResolved, WorldEventRecorded
from app.llm.base import JsonSchema
from app.lore import OpeningSeed
from app.schemas import (
    DIRECTOR_SCHEMA,
    NO_PLAYER_CHANGE,
    DirectorOutput,
    GameState,
    InteractRequest,
    InteractResponse,
    Ledger,
    LocalDelta,
    NewSessionResponse,
    Options,
    PlayerDelta,
    PlayerState,
    TagDelta,
    WorldEvent,
    WorldState,
)
from app.store import EventStore
from conftest import MEMORY_LIMIT, PLAYER, WINDOW, ScriptedLLM, alive, begin_life, dead, make_director, reply, snapshot

PROVOKE = "掀翻乔峰的酒桌"
BURNED = WorldEvent(tags=("无锡松鹤楼", "乔峰"), event_desc="玩家掀翻了乔峰的酒桌")
UNRELATED = WorldEvent(tags=("星宿海", "丁春秋"), event_desc="星宿派弟子血洗了一座渔村")
# 与 conftest 的 OPTIONS 不同：断言"停顿时沿用上一回合选项"时不会被开局选项巧合满足
FRESH = Options(A="推窗远眺", B="唤小二来问话", C="翻身跃上横梁")

# 开局种子与 conftest 的 PLAYER 处处不同：断言"玩家状态等于种子"时不会被巧合满足；
# present 与 lore 的算法一致（开场白点到的绝顶高手），兜底开局必须把他登记为在场——致死预判只读局部环境
SEED = OpeningSeed(
    player=PlayerState(location="少林寺山门外", time="酉时", weather="落雪", health_status="健康", buffs_debuffs=("饥寒交迫",)),
    premise="大雪封山，你缩在少林寺山门的石狮后避风，暮鼓声里一位扫地老僧慢慢扫着台阶上的积雪。",
    present=("扫地僧",),
)


# ============================================================
#  驱动与读回
# ============================================================
def _act(director: Director, view: LifeView, action: str = "静观其变") -> InteractResponse:
    request = InteractRequest(
        session_id=view.life_id, current_state=GameState(player_state=view.player), action_type="custom", action_text=action
    )
    return asyncio.run(director.interact(request))


def _open(director: Director, world_id: UUID | None = None) -> NewSessionResponse:
    return asyncio.run(director.open(world_id))


def _projected(store: EventStore, life_id: UUID) -> LifeView:
    return project_life(life_id, store.load_life(life_id), WINDOW)


def _gain(ledger: Ledger, *tags: str) -> PlayerDelta:
    return NO_PLAYER_CHANGE.model_copy(update={ledger: TagDelta(add=tags, remove=())})


def _without(out: DirectorOutput, field: str) -> str:
    """缺字段的报文：契约之外的漂移，解析闸门必须拦下。"""
    payload = out.model_dump(mode="json")
    del payload[field]
    return json.dumps(payload, ensure_ascii=False)


def _record(store: EventStore, world_id: UUID, *events: WorldEvent) -> None:
    """以某个前世的名义直写世界大事：只追加 world 流，不触碰任何 life 流。"""
    past = uuid4()
    recorded = tuple(WorldEventRecorded(life_id=past, event=event) for event in events)
    store.append(life_id=past, world_id=world_id, expected_version=0, life=(), world=recorded)


def _defiant() -> str:
    """必死回合的抗命报文：玩家存活，还顺手讨了赏、改写了世界线。"""
    return reply(alive(player_delta=_gain("inventory", "打狗棒"), world_events=(BURNED,)))


# ============================================================
#  正常回合 —— 恰好一条事实，响应即投影
# ============================================================
def test_turn_appends_exactly_one_event_and_responds_with_projection(store: EventStore) -> None:
    view = begin_life(store)
    llm = ScriptedLLM(reply(alive(player_delta=_gain("inventory", "折扇"), world_events=(BURNED,))))
    res = _act(make_director(store, llm), view)

    events = store.load_life(view.life_id)
    assert len(events) == view.version + 1 and isinstance(events[-1], TurnResolved)
    after = _projected(store, view.life_id)
    assert res.next_state == game_state(after, project_world(store.load_world(view.world_id)))
    assert "折扇" in after.player.inventory and res.next_state.world_state.major_events == (BURNED,)


def test_every_llm_call_carries_system_prompt_and_director_schema(store: EventStore) -> None:
    view = begin_life(store)
    llm = ScriptedLLM("天机不可泄露", reply(alive()))
    _act(make_director(store, llm), view)

    assert llm.systems == [SYSTEM_PROMPT, SYSTEM_PROMPT]
    assert llm.schemas == [DIRECTOR_SCHEMA, DIRECTOR_SCHEMA]


# ============================================================
#  容错链 —— 持续幻觉原地停顿、带错重采样、LLMError
# ============================================================
@pytest.mark.parametrize(
    "unlawful",
    [
        ("天机不可泄露", json.dumps({"scene_description": "你什么也没做。"}, ensure_ascii=False)),
        (LLMError("超时"), "天机不可泄露"),
        ("天机不可泄露", LLMError("超时")),
    ],
    ids=["hallucination_twice", "failure_then_hallucination", "hallucination_then_failure"],
)
def test_unlawful_replies_stall_in_place_without_appending(store: EventStore, unlawful: tuple[str | Exception, ...]) -> None:
    """大模型可达却始终不合法度（哪怕夹着一次失联）：原地停顿而非 502，世界与此身都停在上一回合。"""
    view = begin_life(store)
    director = make_director(store, ScriptedLLM(reply(alive(options=FRESH)), *unlawful))
    _act(director, view)
    before = _projected(store, view.life_id)
    _record(store, view.world_id, UNRELATED)  # 世界非空：停顿的响应不得丢掉已有的大事记
    res = _act(director, before)

    assert res.scene_description == fallback.STALLED_SCENE and not res.game_over
    assert res.options == FRESH
    assert res.next_state == game_state(before, project_world(store.load_world(view.world_id)))
    assert len(store.load_life(view.life_id)) == before.version and len(store.load_world(view.world_id)) == 1


def test_resample_after_invalid_reply_is_adopted(store: EventStore) -> None:
    view = begin_life(store)
    llm = ScriptedLLM(_without(alive(), "world_events"), reply(alive("你贴着墙根挪到窗边。")))
    res = _act(make_director(store, llm), view)

    assert res.scene_description == "你贴着墙根挪到窗边。"
    assert len(store.load_life(view.life_id)) == view.version + 1


def test_resample_prompt_extends_original_with_format_error(store: EventStore) -> None:
    view = begin_life(store)
    llm = ScriptedLLM(_without(alive(), "world_events"), reply(alive()))
    _act(make_director(store, llm), view)

    first, second = llm.prompts
    assert second.startswith(first) and "<format_error>" not in first
    assert "<format_error>" in second and "world_events" in read_section(second, "format_error")


def test_llm_failure_on_every_attempt_raises_without_appending(store: EventStore) -> None:
    view = begin_life(store)
    llm = ScriptedLLM(LLMError("限流"), LLMError("限流"))

    with pytest.raises(LLMError):
        _act(make_director(store, llm), view)
    assert len(store.load_life(view.life_id)) == view.version


def test_llm_failure_then_valid_reply_recovers(store: EventStore) -> None:
    view = begin_life(store)
    llm = ScriptedLLM(LLMError("超时"), reply(alive("你定了定神。")))
    res = _act(make_director(store, llm), view)

    assert res.scene_description == "你定了定神。"
    assert len(store.load_life(view.life_id)) == view.version + 1


# ============================================================
#  必死回合 —— 规则判死，大模型抗命、失联、服从都改变不了结局，也写不进任何增减
# ============================================================
def test_defiant_llm_on_lethal_turn_yields_deterministic_execution(store: EventStore) -> None:
    attempts = 3
    view = begin_life(store, entities=("萧峰",))
    llm = ScriptedLLM(*(_defiant() for _ in range(attempts)))
    res = _act(make_director(store, llm, attempts=attempts), view, PROVOKE)

    after = _projected(store, view.life_id)
    assert res.game_over and res.options is None and after.dead
    assert "降龙十八掌" in after.player.health_status
    assert after.player.inventory == PLAYER.inventory and store.load_world(view.world_id) == ()
    assert len(llm.prompts) == attempts


def test_unavailable_llm_on_lethal_turn_still_executes(store: EventStore) -> None:
    view = begin_life(store, entities=("萧峰",))
    llm = ScriptedLLM(LLMError("断网"), LLMError("断网"))
    res = _act(make_director(store, llm), view, PROVOKE)

    after = _projected(store, view.life_id)
    assert res.game_over and after.dead
    assert "降龙十八掌" in after.player.health_status


def test_obedient_lethal_reply_is_stripped_of_grants_and_world_events(store: EventStore) -> None:
    view = begin_life(store, entities=("萧峰",))
    obedient = dead("乔峰随手一掌，你当胸中招，气绝身亡。").model_copy(
        update={"player_delta": _gain("inventory", "打狗棒"), "world_events": (BURNED,)}
    )
    llm = ScriptedLLM(reply(obedient))
    res = _act(make_director(store, llm), view, PROVOKE)

    after = _projected(store, view.life_id)
    assert res.game_over and after.dead and res.scene_description == obedient.scene_description
    assert after.player.inventory == PLAYER.inventory and store.load_world(view.world_id) == ()


# ============================================================
#  守卫 —— 死者、未知、并发
# ============================================================
def test_dead_cannot_act_and_llm_is_not_consulted(store: EventStore) -> None:
    view = begin_life(store)
    llm = ScriptedLLM(reply(dead()))
    director = make_director(store, llm)
    _act(director, view)

    with pytest.raises(SessionDeadError):
        _act(director, view)
    assert len(llm.prompts) == 1


def test_unknown_session_is_not_found(store: EventStore) -> None:
    ghost = begin_life(store).model_copy(update={"life_id": uuid4()})
    llm = ScriptedLLM()

    with pytest.raises(NotFoundError):
        _act(make_director(store, llm), ghost)
    assert llm.prompts == []


def test_failed_move_releases_guard_for_next_move(store: EventStore) -> None:
    view = begin_life(store)
    director = make_director(store, ScriptedLLM(LLMError("限流"), LLMError("限流"), reply(alive("你定了定神。"))))

    with pytest.raises(LLMError):
        _act(director, view)
    assert _act(director, view).scene_description == "你定了定神。"


class GatedLLM:
    """第一招悬停在大模型里，直到测试放行：用来制造同一条命上的并发出招。"""

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.calls = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def complete(self, system: str, user: str, schema: JsonSchema) -> str:
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        return self.answer


def test_concurrent_move_on_same_life_is_busy(store: EventStore) -> None:
    view = begin_life(store)
    request = InteractRequest(
        session_id=view.life_id, current_state=GameState(player_state=view.player), action_type="custom", action_text="静观其变"
    )

    async def scenario() -> GatedLLM:
        llm = GatedLLM(reply(alive()))
        director = make_director(store, llm)
        first = asyncio.create_task(director.interact(request))
        await asyncio.wait_for(llm.entered.wait(), timeout=1)
        with pytest.raises(SessionBusyError):  # 守卫失效时第二招也会悬停：限时等待，以失败代替挂起
            await asyncio.wait_for(director.interact(request), timeout=1)
        llm.release.set()
        await first
        return llm

    llm = asyncio.run(scenario())
    assert llm.calls == 1
    assert len(store.load_life(view.life_id)) == view.version + 1


# ============================================================
#  绝学防线
# ============================================================
def test_llm_cannot_grant_master_art(store: EventStore) -> None:
    view = begin_life(store)
    llm = ScriptedLLM(reply(alive(player_delta=_gain("martial_arts", "降龙十八掌"))))
    res = _act(make_director(store, llm), view)

    assert "降龙十八掌" not in res.next_state.player_state.martial_arts
    assert "降龙十八掌" not in _projected(store, view.life_id).player.martial_arts


# ============================================================
#  上下文工程 —— JIT 召回、滑动窗口、换地图清空局部环境
# ============================================================
@pytest.mark.parametrize(
    ("player", "entities", "relevant"),
    [
        (PLAYER, (), WorldEvent(tags=("松鹤楼",), event_desc="松鹤楼被付之一炬")),
        (PLAYER.model_copy(update={"social_traits": ("丐帮弟子",)}), (), WorldEvent(tags=("丐帮",), event_desc="丐帮长老围攻聚贤庄")),
        (PLAYER, ("乔峰",), WorldEvent(tags=("萧峰",), event_desc="萧峰在雁门关外折箭立誓")),  # 在场者以别名登记，大事以本名打标
    ],
    ids=["location", "social_trait", "present_alias"],
)
def test_only_relevant_world_events_are_injected(
    store: EventStore, player: PlayerState, entities: tuple[str, ...], relevant: WorldEvent
) -> None:
    view = begin_life(store, player=player, entities=entities)
    _record(store, view.world_id, relevant, UNRELATED)
    llm = ScriptedLLM(reply(alive()))
    _act(make_director(store, llm), view)

    history = read_section(llm.prompts[0], "relevant_history")
    assert relevant.event_desc in history and UNRELATED.event_desc not in history


def test_world_events_named_by_the_action_are_injected(store: EventStore) -> None:
    # 玩家"前往聚贤庄"：落笔写抵达场景之前，导演就必须看见那里的过往
    view = begin_life(store)
    razed = WorldEvent(tags=("聚贤庄",), event_desc="聚贤庄被玩家付之一炬")
    _record(store, view.world_id, razed, UNRELATED)
    llm = ScriptedLLM(reply(alive()))
    _act(make_director(store, llm), view, "连夜赶往聚贤庄")

    history = read_section(llm.prompts[0], "relevant_history")
    assert razed.event_desc in history and UNRELATED.event_desc not in history


def test_recited_world_event_is_not_appended_again(store: EventStore) -> None:
    # 大模型把 relevant_history 原样抄回 world_events：只追加的台账一条也不重复
    view = begin_life(store)
    burned = WorldEvent(tags=("松鹤楼",), event_desc="松鹤楼被付之一炬")
    _record(store, view.world_id, burned)
    _act(make_director(store, ScriptedLLM(reply(alive(world_events=(burned,))))), view)
    assert [recorded.event for recorded in store.load_world(view.world_id)] == [burned]


def test_recall_keeps_latest_relevant_events_up_to_memory_limit(store: EventStore) -> None:
    view = begin_life(store)
    history = [WorldEvent(tags=("松鹤楼",), event_desc=f"松鹤楼第{i}场风波") for i in range(1, MEMORY_LIMIT + 2)]
    _record(store, view.world_id, *history)
    llm = ScriptedLLM(reply(alive()))
    _act(make_director(store, llm), view)

    injected = read_section(llm.prompts[0], "relevant_history")
    assert len(injected.splitlines()) == MEMORY_LIMIT
    assert history[0].event_desc not in injected and history[-1].event_desc in injected


def test_sliding_window_keeps_latest_turns(store: EventStore) -> None:
    rounds = WINDOW + 2
    view = begin_life(store)
    llm = ScriptedLLM(*(reply(alive(f"第{i}回合，风声又紧了些。")) for i in range(1, rounds + 1)))
    director = make_director(store, llm)
    for _ in range(rounds):
        _act(director, view)

    lines = read_section(llm.prompts[-1], "sliding_window").splitlines()
    assert len(lines) == WINDOW
    assert json.loads(lines[-1])["scene"] == f"第{rounds - 1}回合，风声又紧了些。"


def test_map_change_clears_local_environment(store: EventStore) -> None:
    view = begin_life(store, entities=("段誉",))
    moved = alive(next_state=snapshot(location="太湖畔"), local_delta=LocalDelta(arrived=("阿朱",), departed=()))
    llm = ScriptedLLM(reply(moved), reply(alive()))
    director = make_director(store, llm)
    _act(director, view, "离开松鹤楼，赶往太湖")
    _act(director, view)

    local = LocalEnvironment.model_validate_json(read_section(llm.prompts[1], "local_environment"))
    assert local == LocalEnvironment(location="太湖畔", entities=("阿朱",))


# ============================================================
#  开局与投胎 —— 状态取自种子，世界大事延续
# ============================================================
@pytest.fixture
def seed(monkeypatch: pytest.MonkeyPatch) -> OpeningSeed:
    monkeypatch.setattr(pipeline, "OPENING_SEEDS", (SEED,))
    return SEED


def test_open_new_world_starts_from_seed_ignoring_llm_grants(store: EventStore, seed: OpeningSeed) -> None:
    lavish = alive(
        "你睁开眼，怀里竟揣着一柄宝剑。",
        next_state=snapshot(location="大理皇宫", health_status="神完气足"),
        player_delta=_gain("inventory", "倚天剑"),
    )
    res = _open(make_director(store, ScriptedLLM(reply(lavish))))

    (began,) = store.load_life(res.session_id)
    assert isinstance(began, LifeBegan) and began.world_id == res.world_id
    assert store.world_exists(res.world_id)  # 返回的 world_id 必须是日后可以投胎进去的真实世界
    assert res.next_state == GameState(player_state=seed.player, world_state=WorldState())
    assert res.scene_description == lavish.scene_description and not res.game_over


@pytest.mark.usefixtures("seed")
def test_opening_injects_only_relevant_world_events(store: EventStore) -> None:
    world_id = uuid4()
    by_place = WorldEvent(tags=("少林寺",), event_desc="少林寺藏经阁失窃")
    by_person = WorldEvent(tags=("扫地老僧",), event_desc="扫地老僧一掌震退了萧远山")  # 只经种子在场者的别名命中
    _record(store, world_id, by_place, UNRELATED, by_person)
    llm = ScriptedLLM(reply(alive()))
    _open(make_director(store, llm), world_id)

    history = read_section(llm.prompts[0], "relevant_history")
    assert by_place.event_desc in history and by_person.event_desc in history
    assert UNRELATED.event_desc not in history


def test_opening_judged_dead_falls_back_to_seed(store: EventStore, seed: OpeningSeed) -> None:
    llm = ScriptedLLM(reply(dead()), reply(dead()))
    res = _open(make_director(store, llm))

    assert res.scene_description == seed.premise and not res.game_over
    assert res.options == fallback.DEFAULT_OPTIONS
    assert res.next_state.player_state == seed.player
    assert _projected(store, res.session_id).local.entities == seed.present


def test_reincarnation_inherits_world_events_with_fresh_player(store: EventStore, seed: OpeningSeed) -> None:
    llm = ScriptedLLM(
        reply(alive()),  # 前世开局
        reply(alive(player_delta=_gain("inventory", "打狗棒"), world_events=(BURNED,))),  # 前世改写世界线
        reply(alive()),  # 投胎开局
    )
    director = make_director(store, llm)
    past = _open(director)
    _act(director, _projected(store, past.session_id))
    res = _open(director, past.world_id)

    assert res.world_id == past.world_id and res.session_id != past.session_id
    assert res.next_state.world_state.major_events == (BURNED,)
    assert res.next_state.player_state == seed.player


def test_open_unknown_world_is_not_found(store: EventStore) -> None:
    llm = ScriptedLLM()

    with pytest.raises(NotFoundError):
        _open(make_director(store, llm), uuid4())
    assert llm.prompts == []


@pytest.mark.usefixtures("seed")
def test_opening_llm_failure_on_every_attempt_raises(store: EventStore) -> None:
    llm = ScriptedLLM(LLMError("鉴权失败"), LLMError("鉴权失败"))

    with pytest.raises(LLMError):
        _open(make_director(store, llm))
