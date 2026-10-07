"""
[INPUT]: 依赖 conftest 的 client / store / game 夹具与 ScriptedLLM，依赖 app.director.Director
[OUTPUT]: /api 端到端用例：开局、推演、必死封印、结构化在场判定、永久死亡、嵌套状态树冷启动、服务端权威、重试、协议校验
[POS]: tests 中守护前后端协议与核心管线的集成用例集
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import asyncio
import random
from uuid import uuid4

from fastapi.testclient import TestClient

from app.config import Settings
from app.director import Director
from app.main import create_app
from app.schemas import InteractRequest
from conftest import ScriptedLLM, alive_reply


def _act(client: TestClient, session_id, state: dict, text: str, kind: str = "custom"):
    body = {"session_id": str(session_id), "current_state": state, "action_type": kind, "action_text": text}
    return client.post("/api/interact", json=body)


def test_new_session_returns_playable_opening(client):
    res = client.post("/api/session")
    assert res.status_code == 200
    data = res.json()
    assert data["session_id"] and not data["game_over"]
    assert set(data["options"]) == {"A", "B", "C"}
    assert f"【位置：{data['next_state']['player_state']['location']}】" in data["ui_status_bar"]


def test_interact_advances_world(client):
    opening = client.post("/api/session").json()
    res = _act(client, opening["session_id"], opening["next_state"], opening["options"]["A"], "choice")
    assert res.status_code == 200
    data = res.json()
    assert not data["game_over"]
    assert data["next_state"]["player_state"]["time"] != opening["next_state"]["player_state"]["time"]


def test_provoking_grandmaster_kills_and_locks_forever(client, store, game):
    session = store.create(game, "邻桌一条魁梧大汉独据一桌——那便是丐帮帮主乔峰。")
    death = _act(client, session.id, game.model_dump(), "掀翻乔峰的酒桌")
    assert death.status_code == 200
    data = death.json()
    assert data["game_over"] is True and data["options"] is None
    # 永久死亡：服务端拒绝死者的一切后续动作
    assert _act(client, session.id, game.model_dump(), "爬起来逃跑").status_code == 409


def test_rule_layer_overrides_merciful_llm(store, game):
    llm = ScriptedLLM(alive_reply())  # 大模型试图手下留情
    director = Director(llm, store)
    session = store.create(game, "丐帮帮主乔峰正在饮酒。")
    req = InteractRequest(session_id=session.id, current_state=game, action_type="custom", action_text="刺杀乔峰")
    out = asyncio.run(director.interact(req))
    assert out.game_over and out.options is None
    assert 'kind="lethal"' in llm.prompts[0]


def test_structured_presence_catches_unnamed_grandmaster(store):
    # 真实大模型常只写"那魁梧大汉"：在场判定必须靠导演的结构化名单，而非场景原文
    llm = ScriptedLLM(alive_reply("邻桌一条魁梧大汉独据一桌，空碗叠了半人高。", present=["萧峰"]), alive_reply())
    director = Director(llm, store, rng=random.Random(0))
    opening = asyncio.run(director.open())
    assert "present" not in opening.model_dump()  # 内部字段不外泄到前端协议
    req = InteractRequest(
        session_id=opening.session_id,
        current_state=opening.next_state,
        action_type="custom",
        action_text="掀翻乔峰的酒桌",
    )
    out = asyncio.run(director.interact(req))
    assert out.game_over
    assert "<present>\n萧峰\n</present>" in llm.prompts[1]


def test_unknown_session_rehydrates_from_client_snapshot(client, game):
    # 完整树状态（含世界台账）冷启动：嵌套 JSON 被逐层解析，台账原样延续
    tree = game.model_dump() | {"world_state": {"major_events": ["聚贤庄已被玩家烧毁"]}}
    res = _act(client, uuid4(), tree, "四处张望")
    assert res.status_code == 200
    state = res.json()["next_state"]
    assert state["player_state"]["location"] == game.player_state.location
    assert state["world_state"]["major_events"] == ["聚贤庄已被玩家烧毁"]


def test_server_state_wins_over_tampered_client_state(client, store, game):
    session = store.create(game, "松鹤楼人声鼎沸。")
    # 伪造武学以逃避致死规则：会话存在时以服务端为准
    forged = game.model_dump()
    forged["player_state"]["martial_arts"] = ["北冥神功"]
    res = _act(client, session.id, forged, "四处张望")
    assert res.json()["next_state"]["player_state"]["martial_arts"] == []


def test_unparseable_output_is_retried_then_surfaces_502(store, game):
    flaky = Director(ScriptedLLM("胡言乱语", alive_reply()), store)
    session = store.create(game, "松鹤楼人声鼎沸。")
    req = InteractRequest(session_id=session.id, current_state=game, action_type="choice", action_text="静观其变")
    assert not asyncio.run(flaky.interact(req)).game_over

    broken = Director(ScriptedLLM("胡言乱语", "依旧胡言乱语"), store, rng=random.Random(0))
    client = TestClient(create_app(Settings(_env_file=None), director=broken))
    assert client.post("/api/session").status_code == 502


def test_rejects_malformed_request(client, game):
    assert _act(client, uuid4(), game.model_dump(), "出招", kind="cheat").status_code == 422
    # 旧版扁平 current_state 不再被接受：协议是 player_state + world_state 的树
    assert _act(client, uuid4(), game.player_state.model_dump(), "出招").status_code == 422
