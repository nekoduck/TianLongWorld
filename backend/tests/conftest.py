"""
[INPUT]: 依赖 pytest、fastapi.testclient，依赖 app.main.create_app / app.director.Director / app.store.EventStore / app.engine 投影，
         依赖 app.llm.mock.MockLLM 与 app.schemas 的契约模型
[OUTPUT]: 对外提供 ScriptedLLM 剧本替身、snapshot() / alive() / dead() / reply() 导演报文工厂、PLAYER / OPTIONS 标准素材、
          begin_life() 直写开局事件、make_director() 装配器、WINDOW / MEMORY_LIMIT；夹具 db_path / settings / store / director / client
[POS]: tests 的公共装置：报文一律由契约模型构造再序列化，契约一改，夹具先报错；
       HTTP 用例经 create_app(settings, llm) 走与生产相同的组合根与 lifespan，事件库落在 pytest 的临时目录
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import random
from collections.abc import Iterator, Sequence
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.director import Director
from app.engine import LifeView, begin
from app.events import LifeBegan
from app.llm.base import JsonSchema, LLMClient
from app.llm.mock import MockLLM
from app.main import create_app
from app.schemas import (
    NO_LOCAL_CHANGE,
    NO_PLAYER_CHANGE,
    DirectorOutput,
    LocalDelta,
    Options,
    PlayerDelta,
    PlayerSnapshot,
    PlayerState,
    WorldEvent,
)
from app.store import EventStore

WINDOW = 4
MEMORY_LIMIT = 8


# ============================================================
#  剧本替身 —— 逐条吐出预设回复（异常则抛出），并记录收到的 Prompt
# ============================================================
class ScriptedLLM:
    def __init__(self, *replies: str | Exception) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []
        self.systems: list[str] = []
        self.schemas: list[JsonSchema] = []

    async def complete(self, system: str, user: str, schema: JsonSchema) -> str:
        self.systems.append(system)
        self.prompts.append(user)
        self.schemas.append(schema)
        if not self.replies:
            raise AssertionError("剧本耗尽：大模型被调用的次数超出预期")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


# ============================================================
#  导演报文工厂 —— 先构造契约模型，再序列化为大模型会吐出的文本
# ============================================================
PLAYER = PlayerState(
    location="无锡松鹤楼",
    time="午时",
    weather="晴",
    health_status="健康",
    buffs_debuffs=("饥饿",),
    inventory=("三枚铜钱",),
)

OPTIONS = Options(A="静观其变", B="低声询问", C="夺门而走")


def snapshot(**update: str) -> PlayerSnapshot:
    """以 PLAYER 的快照为底，覆写指定字段。"""
    base = {"location": PLAYER.location, "time": "未时", "weather": PLAYER.weather, "health_status": PLAYER.health_status}
    return PlayerSnapshot.model_validate(base | update)


def alive(
    scene: str = "雾气漫上楼梯，你屏住呼吸。",
    *,
    next_state: PlayerSnapshot | None = None,
    player_delta: PlayerDelta = NO_PLAYER_CHANGE,
    local_delta: LocalDelta = NO_LOCAL_CHANGE,
    world_events: Sequence[WorldEvent] = (),
    options: Options = OPTIONS,
) -> DirectorOutput:
    return DirectorOutput(
        scene_description=scene,
        game_over=False,
        options=options,
        next_state=next_state or snapshot(),
        player_delta=player_delta,
        local_delta=local_delta,
        world_events=tuple(world_events),
    )


def dead(scene: str = "一掌当胸，你倒飞出去，再也没能起来。", *, health: str = "气绝身亡") -> DirectorOutput:
    return DirectorOutput(
        scene_description=scene,
        game_over=True,
        options=None,
        next_state=snapshot(health_status=health),
        player_delta=NO_PLAYER_CHANGE,
        local_delta=NO_LOCAL_CHANGE,
        world_events=(),
    )


def reply(out: DirectorOutput) -> str:
    return out.model_dump_json()


# ============================================================
#  直写事件 —— 绕过大模型，确定性地布置一条命的开局（如：乔峰在场）
# ============================================================
def begin_life(
    store: EventStore,
    *,
    player: PlayerState = PLAYER,
    entities: Sequence[str] = (),
    world_id: UUID | None = None,
    scene: str = "邻桌一条魁梧大汉独据一桌，面前已摆了十几只空酒碗。",
) -> LifeView:
    life_id = uuid4()
    began = LifeBegan(
        world_id=world_id or uuid4(), player=player, scene=scene, options=OPTIONS, entities=tuple(entities)
    )
    store.append(life_id=life_id, world_id=began.world_id, expected_version=0, life=(began,))
    return begin(life_id, began)


# ============================================================
#  夹具
# ============================================================
@pytest.fixture
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "events.db")


@pytest.fixture
def settings(db_path: str) -> Settings:
    # model_construct 不读环境变量与 .env：测试与开发者本机的密钥、provider 完全隔离
    return Settings.model_construct(
        llm_provider="mock", database_path=db_path, history_turns=WINDOW, memory_limit=MEMORY_LIMIT
    )


@pytest.fixture
def store(db_path: str) -> Iterator[EventStore]:
    events = EventStore(db_path)
    yield events
    events.close()


def make_director(store: EventStore, llm: LLMClient, *, attempts: int = 2) -> Director:
    return Director(llm, store, window=WINDOW, memory_limit=MEMORY_LIMIT, attempts=attempts, rng=random.Random(7))


@pytest.fixture
def director(store: EventStore) -> Director:
    return make_director(store, MockLLM(rng=random.Random(7)))


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings, MockLLM(rng=random.Random(7)))) as http:
        yield http
