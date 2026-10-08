"""
[INPUT]: 依赖 fastapi.testclient 的 TestClient，依赖 app.main 的 create_app，依赖 app.container 的 build_container，依赖 tests/world 的 WORLD
[OUTPUT]: WebSocket 线协议用例：投胎 → 流式叙事 → 终帧（状态栏只有语义标签：境界、伤势、武学火候）、自由文本与选项点选、
          错误帧不断连接、选项不下发意图、健康检查、极端找死即永久死亡
[POS]: tests 的表现层验收：经 create_app 的 lifespan 装配，与 uvicorn 启动走同一条路径
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from app.config import Settings
from app.container import build_container
from app.main import create_app
from tests.world import WORLD


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings, container_factory=lambda s: build_container(s, blueprint=WORLD))
    with TestClient(app) as test_client:
        yield test_client


def until_done(ws: WebSocketTestSession) -> list[dict[str, Any]]:
    frames = []
    while True:
        frame = ws.receive_json()
        frames.append(frame)
        if frame["type"] in {"turn_completed", "error"}:
            return frames


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok", "seeded": True}


def test_a_full_session_over_the_wire(client: TestClient) -> None:
    with client.websocket_connect("/ws/play") as ws:
        ws.send_json({"type": "spawn", "name": "阿星", "location": "无量山"})
        frames = until_done(ws)
        assert frames[0]["type"] == "session" and frames[0]["player_id"].startswith("ply:")
        assert {f["type"] for f in frames[1:-1]} == {"narration_delta"}
        done = frames[-1]
        assert done["status"]["location"] == "无量山" and not done["game_over"]
        assert done["status"]["health"] == "安然无恙" and done["status"]["tier"] == "不入流"
        assert all(set(o) == {"id", "label", "category"} for o in done["options"])  # 意图留在服务端

        ws.send_json({"type": "act", "text": "拾起玉佩"})
        frames = until_done(ws)
        assert frames[0] == {
            "type": "turn_resolved",
            "intent": {"action_type": "TAKE", "target_entity": "玉佩", "item_used": None, "skill_used": None,
                       "narrative_style": "", "reason": None},
            "facts": ["阿星在无量山地上拾得玉佩。"],
        }
        assert frames[-1]["status"]["inventory"] == ["玉佩"]

        chosen = frames[-1]["options"][0]
        ws.send_json({"type": "choose", "option_id": chosen["id"]})
        frames = until_done(ws)
        assert frames[0]["type"] == "turn_resolved" and frames[-1]["type"] == "turn_completed"


@pytest.mark.parametrize(
    ("frame", "code"),
    [
        ({"type": "act", "text": "拾起玉佩"}, "BAD_FRAME"),  # 尚未入世
        ({"type": "fly"}, "BAD_FRAME"),
        ({"type": "resume", "player_id": "ply:forged"}, "UNKNOWN_PLAYER"),
        ({"type": "spawn", "name": "阿星", "location": "桃花岛"}, "WORLD_NOT_SEEDED"),
    ],
)
def test_errors_are_frames_not_disconnects(client: TestClient, frame: dict[str, Any], code: str) -> None:
    with client.websocket_connect("/ws/play") as ws:
        ws.send_json(frame)
        assert ws.receive_json()["code"] == code
        ws.send_text("{不是 JSON")
        assert ws.receive_json()["code"] == "BAD_FRAME"
        ws.send_json({"type": "spawn", "name": "阿星"})  # 连接仍然可用
        assert until_done(ws)[-1]["type"] == "turn_completed"


def test_forged_options_and_the_dead(client: TestClient) -> None:
    with client.websocket_connect("/ws/play") as ws:
        ws.send_json({"type": "spawn", "name": "阿星", "location": "无量山"})
        until_done(ws)
        ws.send_json({"type": "choose", "option_id": "combat-00000000"})
        assert ws.receive_json()["code"] == "OPTION_EXPIRED"
        ws.send_json({"type": "act", "text": "偷袭南海鳄神"})  # 不入流挑衅一流狠辣：极端找死
        done = until_done(ws)[-1]
        assert done["game_over"] is True and done["status"]["health"] == "奄奄一息"
        ws.send_json({"type": "act", "text": "静观"})
        assert ws.receive_json()["code"] == "PLAYER_DEAD"
