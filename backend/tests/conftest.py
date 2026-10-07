"""
[INPUT]: 依赖 pytest、fastapi.testclient，依赖 app.main.create_app / app.director.Director / app.session.SessionStore / app.llm.mock.MockLLM
[OUTPUT]: 对外提供 ScriptedLLM 替身（记录每回合的 System Prompt 与 User Message）、alive_reply 报文工厂（快照 + 新增大事 + 实体提取 + 标签账增减 + 局部环境增减）、
          game 状态树夹具、store / director / client 夹具
[POS]: tests 的公共装置：经 create_app(director=...) 注入，测试与生产走同一条组合根
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import json
import random

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.director import Director
from app.llm.mock import MockLLM
from app.main import create_app
from app.schemas import GameState, PlayerState
from app.session import SessionStore


class ScriptedLLM:
    """按剧本逐条吐出预设回复，并记录收到的 Prompt：systems 是每回合的 System Prompt（含 RAG 参考模块），prompts 是 User Message。"""

    def __init__(self, *replies: str):
        self.replies = list(replies)
        self.systems: list[str] = []
        self.prompts: list[str] = []

    async def complete(self, system: str, user: str, schema=None) -> str:
        self.systems.append(system)
        self.prompts.append(user)
        return self.replies.pop(0)


def alive_reply(
    scene: str = "雾气漫上芦苇荡，你屏住呼吸。",
    arrived: list[str] | None = None,
    departed: list[str] | None = None,
    player_delta: dict | None = None,
    major_events: list[dict] | None = None,
    involved: list[str] | None = None,
    **next_state_extra,
) -> str:
    """
    导演报文工厂。player_delta 形如 {"inventory": {"add": [...], "remove": [...]}}；
    major_events 是本回合新增大事，形如 [{"tags": ["聚贤庄"], "event_desc": "..."}]，与实体提取 involved 一同写进 next_state。
    """
    state = {"location": "太湖畔", "time": "丑时", "weather": "大雾", "health_status": "健康"}
    return json.dumps({
        "scene_description": scene,
        "game_over": False,
        "options": {"A": "静观其变", "B": "低声询问", "C": "夺船而走"},
        "next_state": state | next_state_extra | {"major_events": major_events or [], "involved_entities": involved or []},
        "player_delta": player_delta or {},
        "local_delta": {"arrived": arrived or [], "departed": departed or []},
    }, ensure_ascii=False)


@pytest.fixture
def game() -> GameState:
    player = PlayerState(
        location="无锡松鹤楼", time="午时", weather="晴", health_status="健康",
        buffs_debuffs=["饥饿"], inventory=["三枚铜钱"],
    )
    return GameState(player_state=player)


@pytest.fixture
def store() -> SessionStore:
    return SessionStore(capacity=10, history_turns=4)


@pytest.fixture
def director(store: SessionStore) -> Director:
    return Director(MockLLM(rng=random.Random(7)), store, rng=random.Random(7))


@pytest.fixture
def client(director: Director) -> TestClient:
    return TestClient(create_app(Settings(_env_file=None), director=director))
