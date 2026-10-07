"""
[INPUT]: 依赖 app.session 的 reconcile / chronicle，依赖 app.schemas 的 TagDelta / WorldEvent / GameState / WorldState / InteractRequest / LEDGERS，
         依赖 app.director 的 Director、app.lore 的 OPENING_SEEDS，依赖 conftest 的 ScriptedLLM / alive_reply 与 store / game / client 夹具
[OUTPUT]: 记账用例：五本玩家账（四本标签账 + secrets；遗漏≠失去、点名才移除、模糊匹配、快照走私拦截）、世界台账（只追加、复述去重、不设上限、
          大模型无法借 next_state 改写或删除旧事）、状态栏格式与缺省值、开局与冷启动的整树延续、Mock 走同一本账
[POS]: tests 中守护"清单由服务端记账、大模型只报增减"这条状态法则的用例集
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import asyncio
from uuid import uuid4

import pytest

from app.director import Director
from app.lore import OPENING_SEEDS
from app.schemas import LEDGERS, GameState, InteractRequest, TagDelta, WorldEvent, WorldState
from app.session import chronicle, reconcile
from conftest import ScriptedLLM, alive_reply


# ============================================================
#  标签账（纯函数）
# ============================================================
def test_omission_is_not_loss():
    assert reconcile(["短刀", "玉佩"], TagDelta()) == ["短刀", "玉佩"]


def test_named_removal_leaves_and_transformation_works():
    # 喝空水囊：先减「水囊」再加「空水囊」，包含匹配找到「温热粗陶水囊」
    assert reconcile(["温热粗陶水囊", "短刀"], TagDelta(add=["空水囊"], remove=["水囊"])) == ["短刀", "空水囊"]


def test_unheld_or_ambiguous_removal_is_ignored():
    assert reconcile(["短刀"], TagDelta(remove=["屠龙刀"])) == ["短刀"]
    assert reconcile(["短刀", "柴刀"], TagDelta(remove=["刀"])) == ["短刀", "柴刀"]  # 多义时宁可不动


def test_addition_is_deduplicated():
    assert reconcile(["短刀"], TagDelta(add=["短刀", "玉佩"])) == ["短刀", "玉佩"]


# ============================================================
#  世界台账（纯函数）—— 只追加，不合并、不删除、不设上限
# ============================================================
def _event(desc: str, *tags: str) -> WorldEvent:
    return WorldEvent(tags=list(tags) or ["江湖"], event_desc=desc)


BURNED = _event("聚贤庄被玩家付之一炬", "聚贤庄", "游氏双雄")


def test_events_append_and_never_vanish_by_omission():
    assert chronicle([BURNED], []) == [BURNED]
    fallen = _event("乔峰提前身败名裂", "乔峰", "丐帮")
    assert chronicle([BURNED], [fallen]) == [BURNED, fallen]


def test_recited_event_is_not_appended_twice():
    # 大模型偶尔把注入的历史原样抄回 next_state.major_events：event_desc 已在台账里的一律忽略
    echo = _event("聚贤庄被玩家付之一炬", "聚贤庄")
    fresh = _event("游坦之立誓复仇", "游坦之")
    assert chronicle([BURNED], [echo, fresh, fresh]) == [BURNED, fresh]


def test_ledger_has_no_cap_and_never_merges():
    # 旧版满 10 条就合并/折叠，会扭曲历史；如今台账只管存，控制上下文是检索层的事
    history = [_event(f"大事{i}", "江湖") for i in range(200)]
    out = chronicle(history, [_event("第二百零一件大事", "江湖")])
    assert len(out) == 201 and out[:200] == history


# ============================================================
#  管线集成：五本账多回合不提仍在 / 快照走私 / 状态栏
# ============================================================
def _turn(director: Director, session_id, state: GameState, text: str = "四处张望"):
    req = InteractRequest(session_id=session_id, current_state=state, action_type="custom", action_text=text)
    return asyncio.run(director.interact(req))


@pytest.mark.parametrize("ledger", LEDGERS)
def test_every_ledger_survives_turns_that_never_mention_it(store, game, ledger):
    first = alive_reply(player_delta={ledger: {"add": ["新得之签"]}})
    director = Director(ScriptedLLM(first, *(alive_reply() for _ in range(5))), store)
    session = store.create(game, "松鹤楼人声鼎沸。")
    for _ in range(6):
        out = _turn(director, session.id, game)
    assert "新得之签" in getattr(out.next_state.player_state, ledger)


def test_director_cannot_erase_world_history_through_next_state(store, game):
    # next_state.major_events 只是"本回合新增"：大模型写空数组或只写新事，旧历史一条不少
    tree = GameState(player_state=game.player_state, world_state=WorldState(major_events=[BURNED]))
    session = store.create(tree, "松鹤楼人声鼎沸。")
    fresh = {"tags": ["燕子坞"], "event_desc": "燕子坞失火"}
    llm = ScriptedLLM(alive_reply(), alive_reply(major_events=[fresh]))
    _turn(Director(llm, store), session.id, tree)
    out = _turn(Director(llm, store), session.id, tree)
    assert out.next_state.world_state.major_events == [BURNED, WorldEvent(**fresh)]


def test_director_cannot_rewrite_ledgers_through_next_state(store, game):
    # 大模型若在快照里夹带清单，契约直接忽略：清单只认增减
    llm = ScriptedLLM(alive_reply(inventory=["屠龙刀"], martial_arts=["北冥神功"], buffs_debuffs=[]))
    session = store.create(game, "松鹤楼人声鼎沸。")
    player = _turn(Director(llm, store), session.id, game).next_state.player_state
    assert player.inventory == ["三枚铜钱"] and player.martial_arts == [] and player.buffs_debuffs == ["饥饿"]


def test_cure_and_events_flow_through_the_pipeline(store, game):
    llm = ScriptedLLM(alive_reply(
        player_delta={"buffs_debuffs": {"remove": ["饥饿"]}, "social_traits": {"add": ["丐帮一袋弟子"]}},
        major_events=[{"tags": ["松鹤楼", "丐帮"], "event_desc": "松鹤楼被玩家付之一炬"}],
    ))
    session = store.create(game, "松鹤楼人声鼎沸。")
    out = _turn(Director(llm, store), session.id, game, "吃饱后拜入丐帮，临走放火烧楼")
    assert out.next_state.player_state.buffs_debuffs == []
    assert out.next_state.world_state.major_events == [_event("松鹤楼被玩家付之一炬", "松鹤楼", "丐帮")]
    assert out.ui_status_bar == (
        "【位置：太湖畔】 | 【时辰：丑时】 | 【身份：丐帮一袋弟子】 | "
        "【状态：健康】 | 【武学：不会武功】 | 【行囊：三枚铜钱】"
    )


def test_status_bar_defaults_and_full_format(game):
    bare = game.model_copy(deep=True)
    bare.player_state.buffs_debuffs, bare.player_state.inventory = [], []
    assert "【身份：无名小卒】 | 【状态：健康】 | 【武学：不会武功】 | 【行囊：空无一物】" in bare.status_bar()

    full = game.model_copy(deep=True)
    p = full.player_state
    p.location, p.time, p.health_status = "太湖畔", "子时", "重伤"
    p.social_traits, p.buffs_debuffs, p.martial_arts, p.inventory = ["丐帮一袋弟子"], ["中毒"], ["太祖长拳"], ["羊皮卷"]
    assert full.status_bar() == (
        "【位置：太湖畔】 | 【时辰：子时】 | 【身份：丐帮一袋弟子】 | "
        "【状态：重伤, 中毒】 | 【武学：太祖长拳】 | 【行囊：羊皮卷】"
    )


# ============================================================
#  开局 / 冷启动 / Mock
# ============================================================
class _Draw:
    """确定性抽签：总是抽中指定开局种子。"""

    def __init__(self, seed):
        self._seed = seed

    def choice(self, _):
        return self._seed


def test_opening_seed_ledgers_carry_over_without_duplication(store):
    songhe = next(s for s in OPENING_SEEDS if s.state.player_state.inventory == ["三枚铜钱"])
    # 导演重复上报种子里已有的物品，不得翻倍
    reply = alive_reply(player_delta={"inventory": {"add": ["三枚铜钱", "半块炊饼"]}})
    opening = asyncio.run(Director(ScriptedLLM(reply), store, rng=_Draw(songhe)).open())
    assert opening.next_state.player_state.inventory == ["三枚铜钱", "半块炊饼"]
    assert opening.next_state.player_state.buffs_debuffs == songhe.state.player_state.buffs_debuffs


def test_rehydration_restores_the_whole_tree(store, game):
    fallen = _event("乔峰提前身败名裂", "乔峰")
    tree = GameState(player_state=game.player_state, world_state=WorldState(major_events=[fallen]))
    out = _turn(Director(ScriptedLLM(alive_reply()), store), uuid4(), tree)
    assert out.next_state.player_state.inventory == ["三枚铜钱"]
    assert out.next_state.world_state.major_events == [fallen]


def test_mock_director_writes_both_ledgers(client):
    opening = client.post("/api/session").json()

    def act(text: str, state: dict) -> dict:
        body = {"session_id": opening["session_id"], "current_state": state, "action_type": "custom", "action_text": text}
        return client.post("/api/interact", json=body).json()["next_state"]

    state = act("拾起地上之物细看", opening["next_state"])
    assert state["player_state"]["inventory"] == opening["next_state"]["player_state"]["inventory"] + ["锈蚀铁牌"]
    state = act("一把火烧了此地", state)
    (event,) = state["world_state"]["major_events"]
    assert event["tags"] == [state["player_state"]["location"]]  # 标签精准到实体：当前地点
