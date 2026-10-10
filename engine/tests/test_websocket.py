"""
[INPUT]: 依赖 fastapi.testclient 的 TestClient，依赖 app.main 的 create_app，依赖 app.container 的 build_container，依赖 tests/world 的 WORLD，
         依赖 app.application 的 bus（回合消息与状态栏）/ options（ActionOption），依赖 app.presentation.protocol 的 to_frame
[OUTPUT]: WebSocket 线协议用例：投胎 → 流式叙事 → 终帧（状态栏只有语义标签：境界、伤势、武学火候，人情、心事与暗流缺省为空，时辰第一日辰正）、
          自由文本与选项点选（turn_resolved 的意图含手段 / 所图 / 话题 / 此行所为，取物之后时辰走了一刻而白描不提时间）、错误帧不断连接、
          选项只下发 id / flavor_text / tactical_axis / 方向 / why / risk（朴素标签、指令与意图不下发，风险档有才下发）、
          方位导航与选项分开下发 {id, direction, target, travel_method, time_cost, time_label, discovery, retreat}（MOVE 指令不下发），
          choose 帧点导航项同样只带 id、走到去处；终帧的 options / navigation / bonds / pursuits / clocks / renown / time / risk
          与 frontend/src/engineTypes.ts 逐字段一致（时钟 id 不下发）、健康检查、极端找死即永久死亡（时辰停在出手那一刻，没有选项也没有导航）
[POS]: tests 的表现层验收：经 create_app 的 lifespan 装配，与 uvicorn 启动走同一条路径
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from app.application.bus import Bond, ClockInfo, PlayerStatus, Pursuit, TurnCompleted
from app.application.navigation import NavigationOption, duration_label
from app.application.options import ActionOption, OptionCategory
from app.config import Settings
from app.container import build_container
from app.domain.approach import TacticalAxis
from app.domain.commands import Command
from app.domain.geography import Direction, DiscoveryStatus, TravelMethod
from app.domain.intent import ActionType, PlayerIntent
from app.domain.stakes import Risk
from app.main import create_app
from app.presentation.protocol import to_frame
from tests.world import WORLD

OPTION_KEYS = {"id", "flavor_text", "tactical_axis", "category", "why"}
NAV_KEYS = {"id", "direction", "target", "travel_method", "time_cost", "time_label", "discovery", "retreat"}
RISKS = {r.value for r in Risk}
AXES = {a.value for a in TacticalAxis}


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
        assert all(OPTION_KEYS <= set(o) <= {*OPTION_KEYS, "risk"} for o in done["options"])  # 朴素标签、指令与意图留在服务端
        assert all(o.get("risk", Risk.SAFE.value) in RISKS for o in done["options"])  # 风险档只有三档语义标签
        assert all(o["tactical_axis"] in AXES and o["flavor_text"] for o in done["options"])
        assert done["navigation"] and all(set(n) == NAV_KEYS for n in done["navigation"])  # 移动另行下发，指令不下发
        assert [(n["direction"], n["target"]) for n in done["navigation"]] == [("南", "未知区域"), ("下", "未知区域")]  # 初来乍到：迷雾
        assert all(n["id"].startswith("nav-") and n["time_label"] for n in done["navigation"])
        assert done["status"]["bonds"] == [] and done["status"]["pursuits"] == []  # 初入江湖：无人情、无心事
        assert done["status"]["clocks"] == [] and done["status"]["renown"] == "籍籍无名"  # 也无暗流，名望未立
        assert done["status"]["time"] == "第一日·辰正"  # 投胎在第一日辰正，投胎本身不走时间
        assert all(0 < len(o["why"]) <= 12 for o in done["options"])  # 上榜缘由随选项下发

        ws.send_json({"type": "act", "text": "拾起玉佩"})
        frames = until_done(ws)
        assert frames[0] == {
            "type": "turn_resolved",
            "intent": {"action_type": "TAKE", "target_entity": "玉佩", "item_used": None, "skill_used": None,
                       "narrative_style": "", "reason": None, "approach": "寻常", "aim": None, "topic": None,
                       "motivation": ""},
            "facts": ["阿星在无量山地上拾得玉佩。"],  # 时间流逝不出声：它经状态栏的时辰被感知
        }
        assert frames[-1]["status"]["inventory"] == ["玉佩"]
        assert frames[-1]["status"]["time"] == "第一日·辰正一刻"  # 取物花一刻

        chosen = frames[-1]["options"][0]
        ws.send_json({"type": "choose", "option_id": chosen["id"]})
        frames = until_done(ws)
        assert frames[0]["type"] == "turn_resolved" and frames[-1]["type"] == "turn_completed"

        south = next(n for n in frames[-1]["navigation"] if n["direction"] == "南")
        ws.send_json({"type": "choose", "option_id": south["id"]})  # 导航项同样只带 id
        frames = until_done(ws)
        assert frames[0]["intent"]["action_type"] == "MOVE" and frames[0]["intent"]["target_entity"] == "南"  # 方位把手，不露地名
        assert frames[-1]["status"]["location"] == "大理城"
        back = next(n for n in frames[-1]["navigation"] if n["direction"] == "北")
        assert (back["target"], back["discovery"]) == ("无量山", "亲历")  # 来路是到过的地方


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


def test_a_quiet_resume_only_rebinds(client: TestClient) -> None:
    """断线重连发 quiet 续接：线上只回 session 与终帧（叙事为空，选项与状态照给），没有 turn_resolved、没有叙事分片。"""
    with client.websocket_connect("/ws/play") as ws:
        ws.send_json({"type": "spawn", "name": "阿星", "location": "无量山"})
        pid = until_done(ws)[0]["player_id"]
    with client.websocket_connect("/ws/play") as ws:
        ws.send_json({"type": "resume", "player_id": pid, "quiet": True})
        frames = until_done(ws)
        assert [f["type"] for f in frames] == ["session", "turn_completed"]
        assert frames[-1]["narration"] == "" and frames[-1]["options"] and frames[-1]["status"]["location"] == "无量山"


def test_forged_options_and_the_dead(client: TestClient) -> None:
    with client.websocket_connect("/ws/play") as ws:
        ws.send_json({"type": "spawn", "name": "阿星", "location": "无量山"})
        until_done(ws)
        ws.send_json({"type": "choose", "option_id": "combat-00000000"})
        assert ws.receive_json()["code"] == "OPTION_EXPIRED"
        ws.send_json({"type": "act", "text": "偷袭南海鳄神"})  # 不入流挑衅一流狠辣：极端找死
        done = until_done(ws)[-1]
        assert done["game_over"] is True and done["status"]["health"] == "气绝"
        assert done["options"] == [] and done["navigation"] == []  # 死者没有选项，也没有去处
        assert done["status"]["time"] == "第一日·辰正"  # 死者没有心跳：时辰停在出招那一刻
        ws.send_json({"type": "act", "text": "静观"})
        assert ws.receive_json()["code"] == "PLAYER_DEAD"


def test_status_and_risk_mirror_the_frontend_contract() -> None:
    """
    终帧逐字段对照 frontend/src/engineTypes.ts：EngineOption {id, flavor_text, tactical_axis, category, why?, risk?}、
    NavigationOption {id, direction, target, travel_method, time_cost, time_label, discovery, retreat}、Bond {name, attitude, cause}、
    Pursuit {label, note}、ClockInfo {name, kind, progress, maximum}、renown（语义标签）、time（时辰）。
    """
    look = PlayerIntent(action_type=ActionType.OBSERVE)
    talk = PlayerIntent(action_type=ActionType.TALK, target_entity="左子穆")
    options = (
        ActionOption.of(OptionCategory.EXPLORE, "静观四周", look, why="四下无事"),
        ActionOption.of(OptionCategory.SOCIAL, "向左子穆打听", talk, why="心事未了").model_copy(
            update={"risk": Risk.RISKY, "flavor_text": "抱拳上前，探一探东宗口风"}
        ),
    )
    east = PlayerIntent(action_type=ActionType.MOVE, target_entity="东")
    navigation = (
        NavigationOption(
            id="nav-0000beef", direction=Direction.EAST, target="未知区域", travel_method=TravelMethod.BOAT, time_cost=8,
            time_label=duration_label(8), discovery=DiscoveryStatus.UNKNOWN, retreat=True,
            underlying_command=Command(intent=east, time_cost=8),
        ),
    )
    status = PlayerStatus(
        name="阿星", location="无量山", tier="不入流", health="轻伤", alive=True,
        bonds=(Bond(name="辛双清", attitude="敌视", cause="你打伤其师兄"),),
        pursuits=(Pursuit(label="打探 · 左子穆", note="碰了钉子；已试：言辞"),),
        clocks=(ClockInfo(name="钟灵的戒心", kind="疑心", progress=2, maximum=4),),
        renown="小有名气", time="第二日·子初三刻",
    )
    frame = to_frame(TurnCompleted(narration="", options=options, status=status, game_over=False, navigation=navigation))
    assert frame["options"][0] == {"id": options[0].id, "flavor_text": "静观四周", "tactical_axis": "OBSERVE",
                                   "category": OptionCategory.EXPLORE.value, "why": "四下无事"}  # 没有风险档就不下发这个键
    assert frame["options"][1]["risk"] == "有险" and set(frame["options"][1]) == {*OPTION_KEYS, "risk"}
    assert frame["options"][1]["flavor_text"] == "抱拳上前，探一探东宗口风"  # 玩家看见的是风味，朴素标签不下发
    assert frame["navigation"] == [{"id": "nav-0000beef", "direction": "东", "target": "未知区域", "travel_method": "乘船",
                                    "time_cost": 8, "time_label": "约一个时辰", "discovery": "未知", "retreat": True}]
    assert frame["status"]["bonds"] == [{"name": "辛双清", "attitude": "敌视", "cause": "你打伤其师兄"}]
    assert frame["status"]["pursuits"] == [{"label": "打探 · 左子穆", "note": "碰了钉子；已试：言辞"}]
    assert frame["status"]["clocks"] == [{"name": "钟灵的戒心", "kind": "疑心", "progress": 2, "maximum": 4}]
    assert frame["status"]["renown"] == "小有名气" and frame["status"]["time"] == "第二日·子初三刻"
    assert set(frame["status"]) == {"name", "location", "tier", "health", "alive", "death_cause", "inventory", "skills",
                                    "bonds", "pursuits", "clocks", "renown", "time", "traits"}
    assert "clk:" not in str(frame["status"])  # 时钟的 id 与挂处从不下发
    assert "intent" not in str(frame["options"]) and "underlying_command" not in str(frame)
    assert "label" not in frame["options"][0]
