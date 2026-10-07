"""
[INPUT]: 依赖 app.session 的 reconcile，依赖 app.director 的 Director / lore.OPENING_SEEDS，依赖 conftest 的 ScriptedLLM / alive_reply 与 store / world / client 夹具
[OUTPUT]: 物品守恒用例：遗漏不等于失去、点名失去才离身、模糊匹配、走私拦截、开局与冷启动的物品延续、Mock 拾取走同一本账
[POS]: tests 中守护"随身物品由服务端记账、只认增减"这条状态法则的用例集
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import asyncio
from uuid import uuid4

from app.director import Director
from app.director.lore import OPENING_SEEDS
from app.schemas import InteractRequest
from app.session import reconcile
from conftest import ScriptedLLM, alive_reply


# ============================================================
#  记账规则（纯函数）
# ============================================================
def test_omission_is_not_loss():
    assert reconcile(["短刀", "玉佩"], gained=[], lost=[]) == ["短刀", "玉佩"]


def test_named_loss_leaves_and_transformation_works():
    # 喝空水囊：先失「水囊」再得「空水囊」，包含匹配找到「温热粗陶水囊」
    assert reconcile(["温热粗陶水囊", "短刀"], gained=["空水囊"], lost=["水囊"]) == ["短刀", "空水囊"]


def test_unheld_or_ambiguous_loss_is_ignored():
    assert reconcile(["短刀"], gained=[], lost=["屠龙刀"]) == ["短刀"]
    assert reconcile(["短刀", "柴刀"], gained=[], lost=["刀"]) == ["短刀", "柴刀"]  # 多义时宁可不删


def test_gain_is_deduplicated():
    assert reconcile(["短刀"], gained=["短刀", "玉佩"], lost=[]) == ["短刀", "玉佩"]


# ============================================================
#  管线集成：大模型遗漏 / 走私 / 开局 / 冷启动
# ============================================================
def _turn(director: Director, session_id, state, text: str = "四处张望"):
    req = InteractRequest(session_id=session_id, current_state=state, action_type="custom", action_text=text)
    return asyncio.run(director.interact(req))


def test_items_survive_turns_where_the_director_never_mentions_them(store, world):
    llm = ScriptedLLM(alive_reply(gained=["生锈短刀", "段字玉佩"]), *(alive_reply() for _ in range(5)))
    director = Director(llm, store)
    session = store.create(world, "松鹤楼人声鼎沸。")
    out = _turn(director, session.id, world, "捡起地上的短刀和玉佩")
    for _ in range(5):
        out = _turn(director, session.id, world)
    assert out.next_state.inventory == ["三枚铜钱", "生锈短刀", "段字玉佩"]
    assert "【行囊：三枚铜钱, 生锈短刀, 段字玉佩】" in out.ui_status_bar


def test_director_cannot_rewrite_inventory_through_next_state(store, world):
    # 大模型若在快照里夹带 inventory，契约直接忽略：随身物品只认增减
    llm = ScriptedLLM(alive_reply(inventory=["屠龙刀"]))
    session = store.create(world, "松鹤楼人声鼎沸。")
    out = _turn(Director(llm, store), session.id, world)
    assert out.next_state.inventory == ["三枚铜钱"]


def test_explicit_loss_reaches_the_player(store, world):
    llm = ScriptedLLM(alive_reply(lost=["铜钱"]))
    session = store.create(world, "松鹤楼人声鼎沸。")
    out = _turn(Director(llm, store), session.id, world, "把铜钱全给了乞丐")
    assert out.next_state.inventory == []
    assert "【行囊：空无一物】" in out.ui_status_bar


class _Draw:
    """确定性抽签：总是抽中指定开局种子。"""

    def __init__(self, seed):
        self._seed = seed

    def choice(self, _):
        return self._seed


def test_opening_seed_items_carry_over_without_duplication(store):
    songhe = next(s for s in OPENING_SEEDS if s.state.inventory == ["三枚铜钱"])
    # 导演重复上报种子里已有的物品，不得翻倍
    director = Director(ScriptedLLM(alive_reply(gained=["三枚铜钱", "半块炊饼"])), store, rng=_Draw(songhe))
    opening = asyncio.run(director.open())
    assert opening.next_state.inventory == ["三枚铜钱", "半块炊饼"]


def test_rehydration_restores_inventory_from_client_snapshot(store, world):
    out = _turn(Director(ScriptedLLM(alive_reply()), store), uuid4(), world)
    assert out.next_state.inventory == ["三枚铜钱"]


def test_mock_director_reports_found_items_through_the_same_ledger(client):
    opening = client.post("/api/session").json()
    body = {
        "session_id": opening["session_id"],
        "current_state": opening["next_state"],
        "action_type": "custom",
        "action_text": "拾起地上之物细看",
    }
    state = client.post("/api/interact", json=body).json()["next_state"]
    assert state["inventory"] == opening["next_state"]["inventory"] + ["锈蚀铁牌"]
