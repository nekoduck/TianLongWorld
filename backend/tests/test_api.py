"""
[INPUT]: 依赖标准库 random / re / typing / uuid，依赖 pytest 的 parametrize，依赖 fastapi.testclient 的 TestClient 与 httpx2 的 Response，
         依赖 app.main 的 create_app、app.config 的 Settings、app.errors 的 LLMError、app.llm.mock 的 MockLLM、app.lore 的 OPENING_SEEDS、
         app.schemas 的 GameState / PlayerState，依赖 conftest 的 ScriptedLLM / alive / dead / reply / PLAYER 与 settings / client 夹具
[OUTPUT]: /api 的 HTTP 集成用例：开局报文（无 body 与 world_id=null 皆可、UUID、六段状态栏、A/B/C）、出招返回 {player_state, world_state} 树、
          关闭重开 app 后会话延续、投胎保留世界大事而此身全新、未知世界 / 未知会话 404、死者 409、大模型持续失败 502、
          错误体恒为 {detail, code}、客户端篡改无效、旧版扁平状态与空动作 422、健康检查
[POS]: tests 中守护前后端协议与组合根（create_app + lifespan + GameError 处理器）的端到端用例集：一律经 TestClient 走真实路由，
       需要特定剧情时以 ScriptedLLM 注入 create_app，事件库落在 pytest 临时目录
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import random
import re
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response

from app.config import Settings
from app.errors import LLMError
from app.llm.mock import MockLLM
from app.lore import OPENING_SEEDS
from app.main import create_app
from app.schemas import GameState, PlayerState
from conftest import PLAYER, ScriptedLLM, alive, dead, reply

# 六段状态栏：顺序与标签固定，每段内容非空
STATUS_BAR = re.compile(" \\| ".join(f"【{label}：[^】]+】" for label in ("位置", "时辰", "身份", "状态", "武学", "行囊")))

UNKNOWN_SESSION_TURN = {
    "session_id": str(uuid4()),
    "current_state": GameState(player_state=PLAYER).model_dump(mode="json"),
    "action_type": "custom",
    "action_text": "四处张望",
}


# ============================================================
#  驱动
# ============================================================
def _open(http: TestClient, world_id: str | None = None) -> dict[str, Any]:
    res = http.post("/api/session", json={"world_id": world_id})
    assert res.status_code == 200, res.text
    body: dict[str, Any] = res.json()
    return body


def _turn(session: dict[str, Any], action: str, *, state: dict[str, Any] | None = None) -> dict[str, Any]:
    """出招请求体：current_state 缺省回显服务端上次给出的状态树。"""
    return {
        "session_id": session["session_id"],
        "current_state": state or session["next_state"],
        "action_type": "custom",
        "action_text": action,
    }


def _act(http: TestClient, session: dict[str, Any], action: str, *, state: dict[str, Any] | None = None) -> Response:
    return http.post("/api/interact", json=_turn(session, action, state=state))


def _assert_error(res: Response, status: int, code: str) -> None:
    """GameError 统一错误体：恰好 {detail: 给玩家看的文字, code: 机器可读语义}。"""
    assert res.status_code == status, res.text
    body = res.json()
    assert set(body) == {"detail", "code"} and body["code"] == code
    assert isinstance(body["detail"], str) and body["detail"]


# ============================================================
#  开局与出招
# ============================================================
@pytest.mark.parametrize("body", [None, {"world_id": None}], ids=["no_body", "null_world"])
def test_new_session_opens_a_playable_life(client: TestClient, body: dict[str, None] | None) -> None:
    res = client.post("/api/session", json=body)

    assert res.status_code == 200, res.text
    data = res.json()
    assert str(UUID(data["session_id"])) == data["session_id"] and str(UUID(data["world_id"])) == data["world_id"]
    assert STATUS_BAR.fullmatch(data["ui_status_bar"]), data["ui_status_bar"]
    assert data["game_over"] is False and set(data["options"]) == {"A", "B", "C"}


def test_interact_returns_state_tree_and_renders_status_bar_from_it(client: TestClient) -> None:
    session = _open(client)
    res = _act(client, session, session["options"]["A"])

    assert res.status_code == 200, res.text
    data = res.json()
    assert set(data["next_state"]) == {"player_state", "world_state"}
    assert data["ui_status_bar"] == GameState.model_validate(data["next_state"]).status_bar()


def test_session_survives_app_restart(settings: Settings) -> None:
    # 同一 settings 即同一事件库文件：第一个 app 的 lifespan 结束（连接关闭）后，第二个 app 只凭事件日志复原此局
    with TestClient(create_app(settings, MockLLM(rng=random.Random(7)))) as first:
        session = _open(first)
    with TestClient(create_app(settings, MockLLM(rng=random.Random(7)))) as second:
        res = _act(second, session, "静观其变")

    assert res.status_code == 200, res.text
    assert res.json()["game_over"] is False


def test_reincarnation_keeps_world_events_but_starts_a_fresh_life(client: TestClient) -> None:
    first = _open(client)
    razed = _act(client, first, "捡起火折子，一把火烧了此地")  # Mock：「捡」入行囊、「烧」成世界大事
    assert razed.status_code == 200, razed.text
    world = razed.json()["next_state"]["world_state"]
    assert world["major_events"]

    reborn = _open(client, first["world_id"])

    assert reborn["session_id"] != first["session_id"] and reborn["world_id"] == first["world_id"]
    assert reborn["next_state"]["world_state"] == world
    assert PlayerState.model_validate(reborn["next_state"]["player_state"]) in [seed.player for seed in OPENING_SEEDS]


# ============================================================
#  错误谱系 —— 状态码 + {detail, code}
# ============================================================
@pytest.mark.parametrize(
    ("path", "body"),
    [("/api/session", {"world_id": str(uuid4())}), ("/api/interact", UNKNOWN_SESSION_TURN)],
    ids=["unknown_world", "unknown_session"],
)
def test_unknown_world_or_session_is_not_found(client: TestClient, path: str, body: dict[str, Any]) -> None:
    _assert_error(client.post(path, json=body), 404, "not_found")


def test_dead_player_cannot_act_again(settings: Settings) -> None:
    with TestClient(create_app(settings, ScriptedLLM(reply(alive()), reply(dead())))) as http:
        session = _open(http)
        death = _act(http, session, "静观其变")
        assert death.status_code == 200 and death.json()["game_over"] is True

        # 剧本已耗尽：若死者的动作再触达大模型，ScriptedLLM 会直接让用例失败
        _assert_error(_act(http, session, "爬起来逃跑", state=death.json()["next_state"]), 409, "dead")


def test_persistent_llm_failure_is_bad_gateway(settings: Settings) -> None:
    with TestClient(create_app(settings, ScriptedLLM(*(LLMError("上游限流") for _ in range(5))))) as http:
        _assert_error(http.post("/api/session"), 502, "llm_unavailable")


# ============================================================
#  协议边界 —— 服务端权威与请求校验
# ============================================================
def test_tampered_client_state_is_ignored(client: TestClient) -> None:
    session = _open(client)
    tree = session["next_state"]
    forged = tree | {"player_state": tree["player_state"] | {"martial_arts": ["北冥神功"]}}

    res = _act(client, session, "静观其变", state=forged)

    assert res.status_code == 200, res.text
    assert "北冥神功" not in res.json()["next_state"]["player_state"]["martial_arts"]


@pytest.mark.parametrize(
    "patch",
    [{"current_state": PLAYER.model_dump(mode="json")}, {"action_text": ""}, {"action_text": "   "}],
    ids=["legacy_flat_state", "empty_action", "blank_action"],
)
def test_malformed_interact_request_is_unprocessable(client: TestClient, patch: dict[str, Any]) -> None:
    session = _open(client)
    assert client.post("/api/interact", json=_turn(session, "静观其变") | patch).status_code == 422


def test_health(client: TestClient) -> None:
    res = client.get("/api/health")
    assert res.status_code == 200 and res.json() == {"status": "ok"}
