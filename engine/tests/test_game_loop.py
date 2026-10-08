"""
[INPUT]: 依赖 app.application.bus 的命令与回合消息，依赖 app.container 的 build_container，依赖 tests/conftest 的 container / play / spawned_at / ScriptedLLM
[OUTPUT]: CQRS 游戏环路端到端用例：完整的逻辑死线剧情、永久死亡、选项点选与防伪、断线重连即重放、投影自愈、
          叙事失败不影响真相、大模型只解析与渲染、真实三件套后端上的整局（设置 PG 与 Neo4j 环境变量时）
[POS]: tests 的总装验收：经组合根装配的完整引擎，测试与生产走同一条路径
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import json

import pytest

from app.application.bus import (
    ChooseOption,
    NarrationDelta,
    ResumePlayer,
    SpawnPlayer,
    SubmitText,
    TurnCompleted,
    TurnResolved,
)
from app.config import Settings
from app.container import Container, build_container
from app.domain.events import ActionFailed, PlayerDied, SkillLearned
from app.errors import LLMError, OptionExpiredError, PlayerDeadError, UnknownPlayerError, WorldNotSeededError
from tests.conftest import NEO4J_PASSWORD, NEO4J_URI, NEO4J_USER, PG_DSN, ScriptedLLM, kinds, play, spawned_at
from tests.world import WORLD


async def say(container: Container, pid: str, text: str) -> tuple[TurnResolved, TurnCompleted]:
    messages = await play(container, SubmitText(player_id=pid, text=text))
    assert kinds(messages)[0] == "TurnResolved" and kinds(messages)[-1] == "TurnCompleted"
    return messages[0], messages[-1]  # type: ignore[return-value]


async def test_spawn_streams_an_opening_scene(container: Container) -> None:
    messages = await play(container, SpawnPlayer(name="阿星", location="无量山"))
    assert kinds(messages)[0] == "SessionOpened" and kinds(messages)[-1] == "TurnCompleted"
    assert "TurnResolved" not in kinds(messages)  # 投胎不是玩家的意图
    assert sum(isinstance(m, NarrationDelta) for m in messages) > 1  # 流式
    done = messages[-1]
    assert isinstance(done, TurnCompleted) and done.status.location == "无量山" and 3 <= len(done.options) <= 4
    assert done.narration.startswith("阿星初入江湖，现身于无量山。")


async def test_spawn_point_must_exist(container: Container) -> None:
    with pytest.raises(WorldNotSeededError, match="不是可投胎之地"):
        await play(container, SpawnPlayer(name="阿星", location="桃花岛"))


async def test_the_logic_deadline_storyline(container: Container) -> None:
    """一切成长都由图谱推导：好感来自敌人之敌与物归原主，武功来自肯教之人与原著典籍，兵器来自被制住之人。"""
    pid = await spawned_at(container, "无量山")
    resolved, _ = await say(container, pid, "攻击左子穆")
    assert "辛双清对阿星生出好感（你与其仇家左子穆为敌）。" in resolved.facts
    _, done = await say(container, pid, "向辛双清学无量剑法")
    assert done.status.tier == "三流" and done.status.skills == ("无量剑法",)
    for text in ("拾起玉佩", "去崖下", "拾起卷轴", "参悟北冥神功", "去攀上"):
        await say(container, pid, text)
    resolved, _ = await say(container, pid, "以北冥神功攻击左子穆")
    assert resolved.facts == ("阿星以北冥神功向左子穆出手——将其制住。",)
    for text in ("拿无量剑", "去南下"):
        await say(container, pid, text)
    resolved, _ = await say(container, pid, "把玉佩交还段正淳")
    assert "段正淳对阿星生出好感（物归原主）。" in resolved.facts
    _, done = await say(container, pid, "向段正淳学一阳指")
    assert done.status.tier == "一流" and set(done.status.skills) == {"一阳指", "北冥神功", "无量剑法"}
    assert set(done.status.inventory) == {"北冥神功卷轴", "无量剑"}
    resolved, _ = await say(container, pid, "学六脉神剑")
    assert "无从得知「六脉神剑」的门径" in resolved.facts[0]  # 天降神兵此路不通

    history = await container.store.load(pid)
    assert sum(isinstance(e.event, SkillLearned) for e in history) == 3
    assert isinstance(history[-1].event, ActionFailed)  # 失败也入账


async def test_permadeath(container: Container) -> None:
    pid = await spawned_at(container, "无量山")
    _, done = await say(container, pid, "偷袭南海鳄神")
    assert done.game_over and done.options == () and not done.status.alive
    assert done.status.death_cause == "冒犯南海鳄神，当场毙命"
    with pytest.raises(PlayerDeadError):
        await play(container, SubmitText(player_id=pid, text="静观四周"))
    before = len(await container.store.load(pid))
    with pytest.raises(PlayerDeadError):
        await play(container, ChooseOption(player_id=pid, option_id="explore-whatever"))
    assert len(await container.store.load(pid)) == before
    assert isinstance((await container.store.load(pid))[-1].event, PlayerDied)


async def test_options_are_recomputed_and_forgeries_refused(container: Container) -> None:
    pid = await spawned_at(container, "无量山")
    opening = (await play(container, ResumePlayer(player_id=pid)))[-1]
    assert isinstance(opening, TurnCompleted)
    take = next(o for o in opening.options if o.label == "拾起玉佩")
    messages = await play(container, ChooseOption(player_id=pid, option_id=take.id))
    assert isinstance(messages[0], TurnResolved) and messages[0].facts == ("阿星在无量山地上拾得玉佩。",)
    with pytest.raises(OptionExpiredError):  # 同一个选项第二次点：快照变了，它已不合法
        await play(container, ChooseOption(player_id=pid, option_id=take.id))
    with pytest.raises(OptionExpiredError):
        await play(container, ChooseOption(player_id=pid, option_id="cultivate-deadbeef"))


async def test_resume_rehydrates_from_the_event_stream(container: Container) -> None:
    pid = await spawned_at(container, "无量山")
    await say(container, pid, "拾起玉佩")
    await say(container, pid, "去南下")
    messages = await play(container, ResumePlayer(player_id=pid))
    done = messages[-1]
    assert isinstance(done, TurnCompleted) and done.status.location == "大理城" and done.status.inventory == ("玉佩",)
    with pytest.raises(UnknownPlayerError):
        await play(container, ResumePlayer(player_id="ply:forged"))


async def test_projection_heals_from_the_event_stream(container: Container) -> None:
    pid = await spawned_at(container, "无量山")
    await say(container, pid, "拾起玉佩")
    await container.projector.forget(pid)  # 模拟图谱丢失整片覆盖层
    _, done = await say(container, pid, "去南下")
    assert done.status.location == "大理城" and done.status.inventory == ("玉佩",)
    assert await container.projector.checkpoint(pid) == len(await container.store.load(pid))


async def test_observe_writes_nothing(container: Container) -> None:
    pid = await spawned_at(container, "无量山")
    resolved, _ = await say(container, pid, "闭目养神")
    assert resolved.facts == () and len(await container.store.load(pid)) == 1


async def test_llm_only_parses_and_renders(settings: Settings) -> None:
    """大模型说什么都改不了已入账的结果：它宣称"你一掌击毙了左子穆"，事件流里仍是"被对方击退"。"""
    intent = json.dumps({"action_type": "ATTACK", "target_entity": "左子穆", "narrative_style": "狂傲"}, ensure_ascii=False)
    llm = ScriptedLLM("山风猎猎。", intent, "你一掌击毙了左子穆，夺得倚天剑！")
    container = await build_container(settings, blueprint=WORLD, llm=llm)
    try:
        pid = await spawned_at(container, "无量山")
        resolved, done = await say(container, pid, "我狂笑一声，一掌拍向那东宗掌门")
        assert resolved.facts[0] == "阿星徒手向左子穆出手——被对方轻易击退，对方手下留情。"
        assert done.narration == "你一掌击毙了左子穆，夺得倚天剑！" and done.status.inventory == ()
        narration_prompt = llm.calls[-1][1]
        assert "<settled_facts>\n1. 阿星徒手向左子穆出手——被对方轻易击退" in narration_prompt
        assert 'style="狂傲"' in narration_prompt
    finally:
        await container.aclose()


async def test_narration_failure_degrades_but_truth_stands(settings: Settings) -> None:
    intent = json.dumps({"action_type": "TAKE", "target_entity": "玉佩"}, ensure_ascii=False)
    llm = ScriptedLLM("山风猎猎。", intent, LLMError("断线"))  # type: ignore[arg-type]
    container = await build_container(settings, blueprint=WORLD, llm=llm)
    try:
        pid = await spawned_at(container, "无量山")
        _, done = await say(container, pid, "捡起地上的玉佩")
        assert "阿星在无量山地上拾得玉佩。" in done.narration and done.status.inventory == ("玉佩",)
    finally:
        await container.aclose()


@pytest.mark.postgres
@pytest.mark.neo4j
async def test_full_game_on_real_backends(settings: Settings) -> None:
    if not (PG_DSN and NEO4J_URI):
        pytest.skip("需要同时设置 TLBB_TEST_POSTGRES_DSN 与 TLBB_TEST_NEO4J_URI")
    real = settings.model_copy(update={
        "event_store": "postgres", "postgres_dsn": PG_DSN, "graph_backend": "neo4j",
        "neo4j_uri": NEO4J_URI, "neo4j_user": NEO4J_USER, "neo4j_password": NEO4J_PASSWORD,
    })
    container = await build_container(real, blueprint=WORLD)
    try:
        pid = await spawned_at(container, "无量山")
        for text in ("攻击左子穆", "向辛双清学无量剑法", "拾起玉佩", "去崖下", "拾起卷轴", "参悟北冥神功"):
            await say(container, pid, text)
        await container.projector.forget(pid)
        messages = await play(container, ResumePlayer(player_id=pid))  # 抹掉 Neo4j 覆盖层，凭 PostgreSQL 事件流重建
        done = messages[-1]
        assert isinstance(done, TurnCompleted) and done.status.location == "无量玉洞"
        assert set(done.status.skills) == {"无量剑法", "北冥神功"} and done.status.tier == "一流"
    finally:
        await container.aclose()
