"""
[INPUT]: 依赖 app.session 的 reconcile / chronicle，依赖 app.schemas 的 TagDelta / WorldDelta / EventMerge / GameState / WorldState / LEDGERS / MAX_EVENTS，
         依赖 app.director 的 Director / lore.OPENING_SEEDS，依赖 conftest 的 ScriptedLLM / alive_reply 与 store / game / client 夹具
[OUTPUT]: 记账用例：四本玩家标签账（遗漏≠失去、点名才移除、模糊匹配、快照走私拦截）、世界台账（只增不删、合并门槛与校验、
          超限折叠）、状态栏格式与缺省值、开局与冷启动的整树延续、Mock 走同一本账
[POS]: tests 中守护"清单由服务端记账、大模型只报增减"这条状态法则的用例集
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import asyncio
from uuid import uuid4

import pytest

from app.director import Director
from app.director.lore import OPENING_SEEDS
from app.schemas import (
    LEDGERS,
    MAX_EVENTS,
    EventMerge,
    GameState,
    InteractRequest,
    TagDelta,
    WorldDelta,
    WorldState,
)
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
#  世界台账（纯函数）
# ============================================================
def _events(n: int) -> list[str]:
    return [f"大事{i}" for i in range(1, n + 1)]


def test_events_append_and_never_vanish_by_omission():
    assert chronicle(["聚贤庄被烧毁"], WorldDelta()) == ["聚贤庄被烧毁"]
    added = WorldDelta(events_added=["乔峰提前身败名裂", "聚贤庄被烧毁"])
    assert chronicle(["聚贤庄被烧毁"], added) == ["聚贤庄被烧毁", "乔峰提前身败名裂"]


def _merge(*sources: str, add: tuple[str, ...] = ()) -> WorldDelta:
    return WorldDelta(events_added=list(add), events_merged=[EventMerge(sources=list(sources), into="；".join(sources))])


def test_unnecessary_merge_is_ignored():
    # 实测大模型会在台账远未满时主动合并并改写原意：放得下就一律不并
    assert chronicle(_events(MAX_EVENTS - 1), _merge("大事1", "大事2", add=("新大事",)))[:2] == ["大事1", "大事2"]
    assert chronicle(_events(MAX_EVENTS), _merge("大事1", "大事2")) == _events(MAX_EVENTS)


def test_merge_must_name_existing_events():
    # 借"合并"凭空抹掉历史：点名了不存在的事件，整条合并作废，由确定性折叠兜底
    out = chronicle(_events(MAX_EVENTS), _merge("大事1", "子虚乌有", add=("新大事",)))
    assert out[0] == "大事1；大事2" and len(out) == MAX_EVENTS


def test_needed_merge_replaces_sources_in_place():
    out = chronicle(_events(MAX_EVENTS), _merge("大事1", "大事2", add=("新大事",)))
    assert out == ["大事1；大事2", *_events(MAX_EVENTS)[2:], "新大事"]


def test_overflow_folds_oldest_without_losing_them():
    out = chronicle(_events(MAX_EVENTS), WorldDelta(events_added=["新大事"]))
    assert len(out) == MAX_EVENTS
    assert out[0] == "大事1；大事2" and out[-1] == "新大事"


# ============================================================
#  管线集成：四本账多回合不提仍在 / 快照走私 / 状态栏
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


def test_director_cannot_rewrite_ledgers_through_next_state(store, game):
    # 大模型若在快照里夹带清单，契约直接忽略：清单只认增减
    llm = ScriptedLLM(alive_reply(inventory=["屠龙刀"], martial_arts=["北冥神功"], buffs_debuffs=[]))
    session = store.create(game, "松鹤楼人声鼎沸。")
    player = _turn(Director(llm, store), session.id, game).next_state.player_state
    assert player.inventory == ["三枚铜钱"] and player.martial_arts == [] and player.buffs_debuffs == ["饥饿"]


def test_cure_and_events_flow_through_the_pipeline(store, game):
    llm = ScriptedLLM(alive_reply(
        player_delta={"buffs_debuffs": {"remove": ["饥饿"]}, "social_traits": {"add": ["丐帮一袋弟子"]}},
        world_delta={"events_added": ["松鹤楼被玩家付之一炬"]},
    ))
    session = store.create(game, "松鹤楼人声鼎沸。")
    out = _turn(Director(llm, store), session.id, game, "吃饱后拜入丐帮，临走放火烧楼")
    assert out.next_state.player_state.buffs_debuffs == []
    assert out.next_state.world_state.major_events == ["松鹤楼被玩家付之一炬"]
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
    tree = GameState(player_state=game.player_state, world_state=WorldState(major_events=["乔峰提前身败名裂"]))
    out = _turn(Director(ScriptedLLM(alive_reply()), store), uuid4(), tree)
    assert out.next_state.player_state.inventory == ["三枚铜钱"]
    assert out.next_state.world_state.major_events == ["乔峰提前身败名裂"]


def test_mock_director_writes_both_ledgers(client):
    opening = client.post("/api/session").json()

    def act(text: str, state: dict) -> dict:
        body = {"session_id": opening["session_id"], "current_state": state, "action_type": "custom", "action_text": text}
        return client.post("/api/interact", json=body).json()["next_state"]

    state = act("拾起地上之物细看", opening["next_state"])
    assert state["player_state"]["inventory"] == opening["next_state"]["player_state"]["inventory"] + ["锈蚀铁牌"]
    state = act("一把火烧了此地", state)
    assert len(state["world_state"]["major_events"]) == 1
