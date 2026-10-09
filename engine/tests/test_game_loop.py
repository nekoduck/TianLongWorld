"""
[INPUT]: 依赖 app.application.bus 的命令与回合消息，依赖 app.container 的 build_container，依赖 tests/conftest 的 container / play / spawned_at / ScriptedLLM / wire / sse，
         依赖 tests/test_option_metrics 的 canon（入库的正典蓝图，含掌故）
[OUTPUT]: CQRS 游戏环路端到端用例：完整的逻辑死线剧情（入门 → 参照典籍练到略有小成 → 制敌夺剑 → 物归原主直升信赖 → 拜师一阳指）、
          极端找死的永久死亡、重伤后避开仇人调息疗伤、选项点选与防伪、断线重连即重放、投影自愈、
          叙事失败不影响真相、地下城主越界的提议被钳回区间、在场者的人物行写明恩怨、记忆召回带上焦点与在场者、
          P1 验收（正典蓝图上 ScriptedLLM 直接给意图 JSON）：交涉路线（言辞求艺 → 地下城主在区间里裁 → 心事线索 → 菜单「换个手段」→ 点选归气运）、
          暗取路线（点选零次地下城主、文本骗貂败露到手即中毒、人情一栏写明缘由）、随身之物（通天草驳回 NO_USE、金创药疗伤且只有一句白描）、
          每回合至多三次调用、点选与结果已定的文本回合零次地下城主、菜单作端倪进 <hooks>、
          真实三件套后端上的整局（设置 PG 与 Neo4j 环境变量时）
[POS]: tests 的总装验收：经组合根装配的完整引擎，测试与生产走同一条路径
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import json
from typing import Any

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
from app.application.options import OptionCategory
from app.config import Settings
from app.container import Container, build_container
from app.domain.events import (
    ActionFailed,
    HealthChanged,
    ItemConsumed,
    Maneuvered,
    Parleyed,
    PlayerDied,
    SkillPracticed,
)
from app.domain.intent import Approach
from app.domain.models import WorldBlueprint
from app.domain.outcomes import CovertOutcome, SocialOutcome
from app.errors import LLMError, OptionExpiredError, PlayerDeadError, UnknownPlayerError, WorldNotSeededError
from tests.conftest import NEO4J_PASSWORD, NEO4J_URI, NEO4J_USER, PG_DSN, ScriptedLLM, kinds, play, spawned_at, sse
from tests.test_option_metrics import canon
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
    """一切成长都由图谱推导：武功来自原著典籍与肯教之人且火候靠一次次累积，兵器来自被制住之人，信赖来自物归原主。"""
    pid = await spawned_at(container, "无量山")
    for text in ("拾起玉佩", "去崖下", "拾起卷轴"):
        await say(container, pid, text)
    resolved, done = await say(container, pid, "参悟北冥神功")
    assert resolved.facts == ("阿星参照北冥神功卷轴修习北冥神功，功力有所精进。",)
    assert done.status.skills == ("北冥神功（初窥门径）",) and done.status.tier == "三流"  # 一流内功，初窥门径打两档折扣
    for _ in range(8):  # 悟性由 id 抽取（0.8~1.3）、一流之功所得折半：练到略有小成要三到四次，不写死
        if done.status.skills == ("北冥神功（略有小成）",):
            break
        resolved, done = await say(container, pid, "参照卷轴苦练北冥神功")
        assert resolved.facts == ("阿星参照北冥神功卷轴修习北冥神功，功力有所精进。",)
    assert done.status.skills == ("北冥神功（略有小成）",) and done.status.tier == "二流"
    await say(container, pid, "去攀上")
    resolved, _ = await say(container, pid, "以北冥神功攻击左子穆")
    assert resolved.facts[0] == "阿星以北冥神功向左子穆出手——将其制住。"  # 技高一筹，胜负已定，不劳地下城主
    assert "辛双清对阿星生出好感（你出手教训其仇家左子穆）。" in resolved.facts  # 开篇的「敌人之敌」：辛双清升一档
    for text in ("拿无量剑", "去南下"):
        await say(container, pid, text)
    resolved, _ = await say(container, pid, "把玉佩交还段正淳")
    assert "段正淳对阿星深为信赖（物归原主）。" in resolved.facts  # 信赖的封闭清单：物归原主直升信赖，一阳指（一流）才肯传
    resolved, done = await say(container, pid, "向段正淳学一阳指")
    assert resolved.facts == ("阿星得段正淳点拨，修习一阳指，功力有所精进。",)
    assert set(done.status.skills) == {"一阳指（初窥门径）", "北冥神功（略有小成）"} and done.status.tier == "二流"
    assert set(done.status.inventory) == {"北冥神功卷轴", "无量剑"} and done.status.health == "安然无恙"
    resolved, _ = await say(container, pid, "学六脉神剑")
    assert "无从得知「六脉神剑」的门径" in resolved.facts[0]  # 天降神兵此路不通

    history = await container.store.load(pid)
    practice = [e.event for e in history if isinstance(e.event, SkillPracticed)]
    assert practice[0].proficiency_gained == 5 and practice[-1].source_id == "chr:段正淳"
    assert 4 <= len(practice) <= 6  # 入门 + 二到四次参照典籍 + 拜师入门
    assert isinstance(history[-1].event, ActionFailed)  # 失败也入账


async def test_permadeath(container: Container) -> None:
    """不入流挑衅一流狠辣之人是极端找死：没有地下城主时，确定性裁决就是毙命。"""
    pid = await spawned_at(container, "无量山")
    resolved, done = await say(container, pid, "偷袭南海鳄神")
    assert resolved.facts[0] == "阿星徒手向南海鳄神出手——反被一招毙命。"
    assert done.game_over and done.options == () and not done.status.alive
    assert done.status.death_cause == "冒犯南海鳄神，当场毙命" and done.status.health == "气绝"
    with pytest.raises(PlayerDeadError):
        await play(container, SubmitText(player_id=pid, text="静观四周"))
    before = len(await container.store.load(pid))
    with pytest.raises(PlayerDeadError):
        await play(container, ChooseOption(player_id=pid, option_id="explore-whatever"))
    assert len(await container.store.load(pid)) == before
    assert isinstance((await container.store.load(pid))[-1].event, PlayerDied)


async def test_a_severe_escape_then_rest_away_from_the_foe(container: Container) -> None:
    """「重伤逃脱」是真的逃：夺路离开仇人，换个清静地方才调息得了；回到仇人跟前，照样无从调息。"""
    pid = await spawned_at(container, "无量山")
    resolved, done = await say(container, pid, "徒手攻击狠辣的龚光杰")
    assert resolved.facts[:2] == ("阿星徒手向龚光杰出手——身受重伤，拼死逃脱。", "阿星受了伤（与龚光杰交手）。")
    assert resolved.facts[-1] == "阿星经「南下」夺路逃离无量山，来到大理城。"
    assert done.status.alive and done.status.health == "重伤" and done.status.location == "大理城"
    recover = next(o for o in done.options if o.category is OptionCategory.RECOVER)
    rested = await play(container, ChooseOption(player_id=pid, option_id=recover.id))
    assert isinstance(rested[0], TurnResolved) and rested[0].facts == ("阿星调息疗伤，伤势有所好转。",)
    assert isinstance(rested[-1], TurnCompleted) and rested[-1].status.health == "轻伤"
    await say(container, pid, "去北上")
    resolved, done = await say(container, pid, "就地打坐疗伤")
    assert resolved.facts == ("阿星欲调息疗伤，未果：左子穆在侧虎视眈眈，你无法安心调息。",)  # 师父也记了仇
    assert all(o.category is not OptionCategory.RECOVER for o in done.options)


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


async def test_game_master_overreach_is_clamped_into_the_rails(settings: Settings) -> None:
    """
    地下城主只能在区间里提议：越级取胜（SUCCESS 不在区间里）被驳回重采样，过狠的扣减被钳回轻伤的气血带；
    叙事大模型宣称"你一掌击毙了左子穆，夺得倚天剑"，事件流里照样只有轻伤退开。
    """
    intent = json.dumps({"action_type": "ATTACK", "target_entity": "左子穆", "narrative_style": "狂傲"}, ensure_ascii=False)
    greedy = json.dumps({"outcome_type": "SUCCESS", "hp_change": 0, "narrative_hint": "你一掌将左子穆拍翻在地"},
                        ensure_ascii=False)
    harsh = json.dumps({"outcome_type": "MINOR_WOUND", "hp_change": -99, "narrative_hint": "左子穆横剑一封，你掌缘见血，跃开数步"},
                       ensure_ascii=False)
    llm = ScriptedLLM("山风猎猎。", intent, greedy, harsh, "你一掌击毙了左子穆，夺得倚天剑！")
    container = await build_container(settings, blueprint=WORLD, llm=llm)
    try:
        pid = await spawned_at(container, "无量山")
        resolved, done = await say(container, pid, "我狂笑一声，一掌拍向那东宗掌门")
        assert resolved.facts[:2] == ("阿星徒手向左子穆出手——吃了点亏，带着轻伤退开。", "阿星受了伤（与左子穆交手）。")
        assert done.narration == "你一掌击毙了左子穆，夺得倚天剑！" and done.status.inventory == ()
        assert done.status.alive and done.status.health == "轻伤"
        wound = next(e.event for e in await container.store.load(pid) if isinstance(e.event, HealthChanged))
        assert wound.delta == -25  # -99 被钳回轻伤的气血带 [-25, -10]
        gm_calls = [c for c in llm.calls if c[2] is not None and "outcome_type" in c[2]["properties"]]
        assert len(gm_calls) == 2  # 越界一次、重采样一次
        narration_prompt = llm.calls[-1][1]
        assert "<settled_facts>\n1. 阿星徒手向左子穆出手——吃了点亏" in narration_prompt
        assert "<gm_sketch>左子穆横剑一封，你掌缘见血，跃开数步</gm_sketch>" in narration_prompt
        assert "拍翻在地" not in narration_prompt and 'style="狂傲"' in narration_prompt
    finally:
        await container.aclose()


async def test_a_flight_is_narrated_where_the_fight_happened(settings: Settings) -> None:
    """重伤夺路而逃：叙事拿到的快照已是大理城，交手的无量山与龚光杰经 <fled_scene> 一并送到，不逼说书人在两条铁律间二选一。"""
    intent = json.dumps({"action_type": "ATTACK", "target_entity": "龚光杰"}, ensure_ascii=False)
    severe = json.dumps({"outcome_type": "SEVERE_WOUND", "hp_change": -50, "narrative_hint": "龚光杰长剑一抖，你肩头中剑"},
                        ensure_ascii=False)
    back = json.dumps({"action_type": "MOVE", "target_entity": "北上"}, ensure_ascii=False)
    llm = ScriptedLLM("山风猎猎。", intent, severe, "你踉跄奔下山去。", back, "你又回到山上。")
    container = await build_container(settings, blueprint=WORLD, llm=llm)
    try:
        pid = await spawned_at(container, "无量山")
        _, done = await say(container, pid, "徒手攻击龚光杰")
        assert done.status.location == "大理城"
        fled_scene, truth = llm.calls[-1][1].split("<truth_snapshot>")
        assert '<location name="无量山"' in fled_scene and "- 龚光杰｜" in fled_scene
        assert "恩怨：" not in fled_scene  # 交手现场是交手前的样子，新结的仇以 settled_facts 为准
        assert '<location name="大理城"' in truth and "<gm_sketch>龚光杰长剑一抖，你肩头中剑</gm_sketch>" in truth
        await say(container, pid, "北上")  # 回到仇人跟前：在场者的人物行写明恩怨，说书人不必自己编仇从何来
        back_home = llm.calls[-1][1].split("<truth_snapshot>")[1]
        assert "- 龚光杰｜无量剑东宗｜三流｜性情狠辣｜对你敌视｜恩怨：遭你出手相攻｜行动自如" in back_home
        assert "- 左子穆｜无量剑东宗｜三流｜性情中庸｜对你敌视｜恩怨：你打伤其得意门徒龚光杰｜" in back_home
    finally:
        await container.aclose()


async def test_recalled_memories_are_distinct_and_capped(settings: Settings) -> None:
    """来回走两趟，白描一字不差：召回多取一倍、按字面去重后恰取 k 条，名额不浪费在复读上。"""
    north = json.dumps({"action_type": "MOVE", "target_entity": "无量山"}, ensure_ascii=False)
    south = json.dumps({"action_type": "MOVE", "target_entity": "大理城"}, ensure_ascii=False)
    llm = ScriptedLLM("苍山如黛。", north, "一", south, "二", north, "三", south, "四")
    container = await build_container(settings.model_copy(update={"memory_recall_k": 2}), blueprint=WORLD, llm=llm)
    try:
        pid = await spawned_at(container, "大理城")
        for text in ("北上", "南下", "北上", "南下"):
            await say(container, pid, text)
        recalled = llm.calls[-1][1].split("<memories>\n")[1].split("</memories>")[0].splitlines()
        assert len(recalled) == 2 and len(set(recalled)) == 2  # 四条往事里有两条一字不差
    finally:
        await container.aclose()


async def test_recall_asks_about_the_focus_and_the_people_present(container: Container) -> None:
    """召回两路、原话优先：一路查原话与玩家名，一路查焦点实体与在场者——「再给他一拳」也要想起「他」是谁、此地站着谁。"""
    queries: list[str] = []
    memory = container.pipeline._memory
    recall = memory.recall

    async def spy(player_id: str, query: str, k: int, before_version: int) -> Any:
        queries.append(query)
        return await recall(player_id, query, k, before_version)

    memory.recall = spy  # type: ignore[method-assign]
    pid = await spawned_at(container, "无量山")
    await say(container, pid, "向左子穆打听消息")
    await say(container, pid, "静观四周")
    assert queries[-2:] == ["静观四周 阿星", "左子穆 南海鳄神 辛双清 龚光杰"]  # 在场者的名字多，不与原话混在一条查询里


async def test_a_quiet_resume_costs_no_llm_call(settings: Settings) -> None:
    """断线重连的 quiet 续接：只回 session 与终帧（叙事为空、选项与状态照给），不复述此景，一次大模型也不调。"""
    llm = ScriptedLLM("山风猎猎。")
    container = await build_container(settings, blueprint=WORLD, llm=llm)
    try:
        pid = await spawned_at(container, "无量山")
        calls = len(llm.calls)
        messages = await play(container, ResumePlayer(player_id=pid, quiet=True))
        assert [type(m).__name__ for m in messages] == ["SessionOpened", "TurnCompleted"]
        done = messages[-1]
        assert isinstance(done, TurnCompleted) and done.narration == "" and done.options and done.status.location == "无量山"
        assert len(llm.calls) == calls
    finally:
        await container.aclose()


async def test_resume_waits_for_the_move_in_flight(container: Container) -> None:
    """续接与出手共用玩家锁：一招还没落账时续上，看到的必是落账之后的局面，而不是一点就过期的旧菜单。"""
    import asyncio

    pid = await spawned_at(container, "无量山")
    lock = container.pipeline._locks.setdefault(pid, asyncio.Lock())
    async with lock:  # 假装一招正在命令侧
        pending = asyncio.ensure_future(play(container, ResumePlayer(player_id=pid, quiet=True)))
        await asyncio.sleep(0.05)
        assert not pending.done()
    assert isinstance((await pending)[-1], TurnCompleted)


async def test_the_runtime_roles_share_one_call_fuse(settings: Settings, wire: Any) -> None:
    """意图、地下城主与叙事共用一份保险丝：开场叙事用掉唯一的一次，下一句意图解析就熔断——请求发不出去，回合不写任何事件。"""
    seen = wire(lambda r: sse({"candidates": [{"content": {"parts": [{"text": "山风猎猎。"}]}}]}))
    gemini = settings.model_copy(update={"llm_provider": "gemini", "llm_api_key": "g", "llm_model": "flash", "llm_call_limit": 1})
    container = await build_container(gemini, blueprint=WORLD)
    try:
        pid = await spawned_at(container, "无量山")
        before = len(await container.store.load(pid))
        with pytest.raises(LLMError, match="LLM_CALL_LIMIT=1"):
            await say(container, pid, "捡起地上的玉佩")
        assert len(seen) == 1 and len(await container.store.load(pid)) == before
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


# ============================================================
#  P1 验收：正典蓝图（含掌故）上的三条路线——ScriptedLLM 直接给意图 JSON，只测下游
# ============================================================
def _json(**fields: str) -> str:
    return json.dumps(fields, ensure_ascii=False)


def _gm_calls(llm: ScriptedLLM, since: int = 0) -> list[tuple[str, str, Any]]:
    """地下城主的调用：它的 schema 有 outcome（交涉 / 暗中）或 outcome_type（出手）。"""
    return [c for c in llm.calls[since:] if c[2] is not None and {"outcome", "outcome_type"} & c[2]["properties"].keys()]


async def _turn(container: Container, llm: ScriptedLLM, command: Any) -> tuple[list[Any], int, int]:
    """一回合：消息、这一回合的大模型调用次数、其中地下城主的次数。每回合至多三次（意图 + 地下城主 + 叙事）。"""
    before = len(llm.calls)
    messages = await play(container, command)
    spent, judged = len(llm.calls) - before, len(_gm_calls(llm, before))
    assert spent <= 3
    return messages, spent, judged


def _foreshadows(bp: WorldBlueprint) -> list[str]:
    return [c.foreshadow for c in bp.characters if c.foreshadow]


async def test_the_social_route_end_to_end(settings: Settings) -> None:
    """
    言辞求艺遇不肯：走交涉，地下城主只在区间（无果 / 碰壁）里裁，Parleyed 入账、开出一条心事线索；
    下一份菜单给出没试过的手段（人情，why「换个手段」），并作端倪进 <hooks>；点选它归气运，不请地下城主。
    """
    bp = canon()
    learn = _json(action_type="LEARN", target_entity="木婉清", skill_used="晓风拂柳", approach="言辞", aim="求艺")
    verdict = _json(outcome="无果", narrative_hint="木婉清冷冷瞥你一眼，转过脸去")
    llm = ScriptedLLM("山风猎猎。", learn, verdict, "木婉清不答。", "你又陪了几句好话。")
    container = await build_container(settings, blueprint=bp, llm=llm)
    try:
        pid = await spawned_at(container, "无量山")
        messages, spent, judged = await _turn(container, llm, SubmitText(player_id=pid, text="恳请木婉清传我晓风拂柳"))
        assert (spent, judged) == (3, 1)  # 意图、地下城主、叙事各一次
        resolved, done = messages[0], messages[-1]
        assert isinstance(resolved, TurnResolved) and isinstance(done, TurnCompleted)
        assert resolved.facts == ("阿星以言辞向木婉清求艺，无果而终。",)
        system, brief, schema = _gm_calls(llm)[0]
        assert schema["properties"]["outcome"]["enum"] == ["NOTHING", "REBUFFED"]  # 求艺够不上门槛：如愿不在区间里
        assert "木婉清" in brief and not any(f in system + brief for f in _foreshadows(bp))  # 简报只用 T=0
        assert "<gm_sketch>木婉清冷冷瞥你一眼，转过脸去</gm_sketch>" in llm.calls[-1][1]  # 结局被采纳，速写交给叙事
        parley = (await container.store.load(pid))[-1].event
        assert isinstance(parley, Parleyed) and parley.outcome is SocialOutcome.NOTHING and parley.subject_id == "art:晓风拂柳"
        assert [(p.label, p.note) for p in done.status.pursuits] == [("求艺 · 晓风拂柳", "尚无眉目；已试：言辞")]

        retry = next(o for o in done.options if o.why == "换个手段")
        assert retry.intent.approach is Approach.FAVOR and retry.intent.skill_used == "晓风拂柳"
        assert f"<hooks>\n- {retry.label}（换个手段）" in llm.calls[-1][1]  # 菜单先于叙事算好，作端倪交给说书人

        messages, spent, judged = await _turn(container, llm, ChooseOption(player_id=pid, option_id=retry.id))
        assert (spent, judged) == (1, 0)  # 点选：气运裁决，只花叙事一次
        parley = (await container.store.load(pid))[-1].event
        assert isinstance(parley, Parleyed) and parley.approach is Approach.FAVOR
        assert parley.outcome in (SocialOutcome.NOTHING, SocialOutcome.REBUFFED)  # 气运也越不出区间
        done = messages[-1]
        assert isinstance(done, TurnCompleted) and done.status.pursuits[0].note.endswith("已试：言辞、人情")
        assert all(o.why != "换个手段" for o in done.options)  # 求艺的手段只有言辞与人情：都试过了
    finally:
        await container.aclose()


def _remedy_on_the_ground(bp: WorldBlueprint) -> WorldBlueprint:
    """正典里金创药在木婉清身上，不入流的玩家讨不来也偷不到——验收用例让它落在无量山的地上（唯一的改动）。"""
    items = tuple(
        i.model_copy(update={"owner_id": None, "location_id": "loc:无量山"}) if i.id == "itm:金创药" else i for i in bp.items
    )
    return bp.model_copy(update={"items": items})


async def test_the_covert_route_and_what_you_carry(settings: Settings) -> None:
    """
    暗取：点选「摸走」归气运（区间里好一格不存在、差一格是败露，只能留在未遂），文本骗貂请地下城主，败露即到手、到手即中毒；
    结果已定的文本回合不请地下城主；通天草不能服用（NO_USE），金创药疗伤，服药只有一句白描（HealthChanged(source=item) 不出声）。
    """
    bp = _remedy_on_the_ground(canon())
    guile = _json(action_type="TAKE", target_entity="闪电貂", approach="计谋")
    exposed = _json(outcome="败露", narrative_hint="钟灵一把揪住你衣袖，尖声叫了起来")
    llm = ScriptedLLM(
        "剑湖宫前。", "你的手缩了回来。",
        guile, exposed, "貂儿反口一咬。",
        _json(action_type="MOVE", target_entity="出大门"), "你出了宫门。",
        _json(action_type="TAKE", target_entity="通天草"), "你拔起一株草。",
        _json(action_type="USE", item_used="通天草"), "草叶苦涩。",
        _json(action_type="TAKE", target_entity="金创药"), "你拾起药瓶。",
        _json(action_type="USE", item_used="金创药"), "药力透入伤处。",
    )
    container = await build_container(settings, blueprint=bp, llm=llm)
    try:
        pid = await spawned_at(container, "剑湖宫")
        opening = (await play(container, ResumePlayer(player_id=pid, quiet=True)))[-1]
        assert isinstance(opening, TurnCompleted)
        steal = next(o for o in opening.options if o.intent.approach is Approach.STEALTH)
        assert steal.intent.target_entity == "闪电貂" and steal.risk is not None and steal.risk.value == "有险"
        _, spent, judged = await _turn(container, llm, ChooseOption(player_id=pid, option_id=steal.id))
        assert (spent, judged) == (1, 0)
        tried = (await container.store.load(pid))[-1].event
        assert isinstance(tried, Maneuvered) and tried.outcome is CovertOutcome.FOILED

        messages, spent, judged = await _turn(container, llm, SubmitText(player_id=pid, text="骗钟灵把闪电貂借我玩玩"))
        assert (spent, judged) == (3, 1)
        assert _gm_calls(llm)[0][2]["properties"]["outcome"]["enum"] == ["FOILED", "EXPOSED", "CAUGHT"]
        events = [e.event for e in await container.store.load(pid)]
        assert any(isinstance(e, Maneuvered) and e.outcome is CovertOutcome.EXPOSED for e in events)
        bite = events[-1]
        assert isinstance(bite, HealthChanged) and bite.source == "blow" and bite.source_id == "itm:闪电貂" and bite.delta < 0
        done = messages[-1]
        assert isinstance(done, TurnCompleted) and done.status.health == "轻伤" and "闪电貂" in done.status.inventory
        assert [(b.name, b.attitude, b.cause) for b in done.status.bonds] == [("钟灵", "敌视", "识破你的骗局")]

        for text in ("出大门", "拾起通天草"):
            _, spent, judged = await _turn(container, llm, SubmitText(player_id=pid, text=text))
            assert (spent, judged) == (2, 0)  # 结果已定：意图与叙事，地下城主一次也不请
        messages, _, _ = await _turn(container, llm, SubmitText(player_id=pid, text="服下通天草"))
        refused = (await container.store.load(pid))[-1].event
        assert isinstance(refused, ActionFailed) and refused.reason_code == "NO_USE"

        await _turn(container, llm, SubmitText(player_id=pid, text="拾起金创药"))
        messages, spent, judged = await _turn(container, llm, SubmitText(player_id=pid, text="敷上金创药"))
        resolved, done = messages[0], messages[-1]
        assert isinstance(resolved, TurnResolved) and resolved.facts == ("阿星以金创药疗伤。",)  # 回气血那条不出声
        assert "<settled_facts>\n1. 阿星以金创药疗伤。\n</settled_facts>" in llm.calls[-1][1]
        tail = [e.event for e in (await container.store.load(pid))[-2:]]
        assert isinstance(tail[0], ItemConsumed) and isinstance(tail[1], HealthChanged) and tail[1].source == "item"
        assert isinstance(done, TurnCompleted) and done.status.health == "安然无恙" and "金创药" not in done.status.inventory
        remembered = await container.pipeline._memory.recall(pid, "金创药 疗伤", 20, 10**6)
        assert remembered and all(m.text for m in remembered)  # 空串白描不入记忆
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
        for text in ("攻击左子穆", "拾起玉佩", "去崖下", "拾起卷轴", "参悟北冥神功", "参照卷轴苦练北冥神功"):
            await say(container, pid, text)
        history = await container.store.load(pid)
        await container.projector.forget(pid)
        messages = await play(container, ResumePlayer(player_id=pid))  # 抹掉 Neo4j 覆盖层，凭 PostgreSQL 事件流重建
        done = messages[-1]
        assert isinstance(done, TurnCompleted) and done.status.location == "无量玉洞"
        assert done.status.health == "轻伤" and set(done.status.inventory) == {"玉佩", "北冥神功卷轴"}
        assert sum(isinstance(e.event, SkillPracticed) for e in history) == 2
        assert len(done.status.skills) == 1 and done.status.skills[0].startswith("北冥神功（")
    finally:
        await container.aclose()
