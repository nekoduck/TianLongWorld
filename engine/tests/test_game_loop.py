"""
[INPUT]: 依赖 app.application.bus 的命令与回合消息，依赖 app.container 的 build_container，依赖 tests/conftest 的 container / play / spawned_at / ScriptedLLM / wire / sse，
         依赖 tests/test_option_metrics 的 canon（入库的正典蓝图，含掌故），依赖 application/resolution_agent 的 Resolver / Resolution / 气运种子，依赖 domain 的 ResolutionOutput / envelope 与时钟事件
[OUTPUT]: CQRS 游戏环路端到端用例：完整的逻辑死线剧情（入门 → 参照典籍练到略有小成 → 制敌夺剑 → 物归原主直升信赖 → 拜师一阳指；驳回入账且花一刻）、
          极端找死的永久死亡、重伤后避开仇人调息疗伤、选项点选与防伪、断线重连即重放、投影自愈、静观只写一条 TimePassed（白描不出声、时辰走一刻）、
          ruled() 剥去世界心跳与 H-Agent（时间、余波、生态、行军与议程）取规则定案的事件——「最后一条」仍是这一招本身的结果、
          叙事失败不影响真相、出界的推演（制住了得手不在区间里的对手）整份作废按确定性裁决结算、在场者的人物行写明恩怨、记忆召回带上焦点与在场者、
          gm() / start() / tick() 写 ResolutionOutput 形状的推演、Reading 地下城主替身；
          语义物理引擎端到端：交涉推演挂上疑心 + 留细节 + 折名望 → 入账、白描、<clocks> / <emerged> 进叙事、快照召回、状态栏亮出暗流与名望 →
          挂着时钟点选仍零次地下城主 → 结果已定的闲谈因时钟请一次、只动时钟 / 事实 / 名望 → 推满坍缩（敌视、名望 −5、略有恶名）；
          此地的危机坍缩即受创三十并被迫脱身；点选零次地下城主且气运好过确定性裁决时对象身上补挂代价时钟；
          P1 验收（正典蓝图上 ScriptedLLM 直接给意图 JSON）：交涉路线（言辞求艺 → 地下城主推演、好一格欠下戒心 → 心事线索 → 菜单「换个手段」→ 点选归气运）、
          暗取路线（点选零次地下城主、文本骗貂败露到手即中毒且补挂失主的疑心、人情一栏写明缘由、眼前挂着时钟的结果已定回合请一次）、
          随身之物（通天草驳回 NO_USE、金创药疗伤且只有一句白描）、每回合至多三次调用（still() 关掉议程：这几条只测玩家的三路）、换个手段进可供性目录、
          真实三件套后端上的整局（设置 PG 与 Neo4j 环境变量时；时钟与微观事实经 Neo4j 覆盖层召回，交手的往事、痕迹与消息同样经覆盖层进快照，
          抹去重放后挂在你身上的时钟照样回来、时辰与重建前一致）
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
from app.application.resolution_agent import Resolution, Resolver, _draw, fortune_seed
from app.config import Settings
from app.container import Container, build_container
from app.domain.events import (
    ActionFailed,
    ActivityStarted,
    AgendaConcluded,
    AgendaIssued,
    AgendaPlanned,
    ClockAdvanced,
    ClockCollapsed,
    ClockStarted,
    DomainEvent,
    EncounterBegan,
    EncounterResolved,
    FactEmerged,
    FactTokenSpawned,
    HealthChanged,
    ItemConsumed,
    ItemDecayed,
    ItemPilfered,
    Maneuvered,
    Moved,
    NpcMoved,
    NpcWounded,
    Parleyed,
    PlayerDied,
    RelationChanged,
    RenownChanged,
    RumorSpread,
    SkillPracticed,
    TimePassed,
    TraceLeft,
)
from app.domain.intent import Approach
from app.domain.models import WorldBlueprint
from app.domain.outcomes import CovertOutcome, SocialOutcome
from app.domain.resolution import ResolutionOutput
from app.domain.rules import envelope
from app.errors import LLMError, OptionExpiredError, PlayerDeadError, UnknownPlayerError, WorldNotSeededError
from tests.conftest import NEO4J_PASSWORD, NEO4J_URI, NEO4J_USER, PG_DSN, ScriptedLLM, kinds, play, spawned_at, sse
from tests.test_option_metrics import canon
from tests.world import WORLD

HEARTBEAT = (
    TimePassed, ActivityStarted, TraceLeft, FactTokenSpawned, RumorSpread, ItemDecayed, ItemPilfered,
    AgendaPlanned, AgendaIssued, AgendaConcluded, NpcMoved, EncounterBegan, EncounterResolved, NpcWounded,
)


def still(settings: Settings) -> Settings:
    """关掉宏观议程：正典里有核心 NPC，议程大模型会在第一招时立一轮——P1 的三路验收只测玩家这一侧，H-Agent 另见 test_agents_loop。"""
    return settings.model_copy(update={"npc_agenda": False})


async def ruled(container: Container, pid: str) -> list[DomainEvent]:
    """事件流里规则定案的那些（剥去世界心跳与 H-Agent：时间、余波、生态、行军与议程）——「最后一条」仍是这一招本身的结果。"""
    return [e.event for e in await container.store.load(pid) if not isinstance(e.event, HEARTBEAT)]


async def say(container: Container, pid: str, text: str) -> tuple[TurnResolved, TurnCompleted]:
    messages = await play(container, SubmitText(player_id=pid, text=text))
    assert kinds(messages)[0] == "TurnResolved" and kinds(messages)[-1] == "TurnCompleted"
    return messages[0], messages[-1]  # type: ignore[return-value]


def gm(
    severity: str = "爆炸",
    deltas: dict[str, int] | None = None,
    clocks: tuple[dict[str, Any], ...] = (),
    facts: tuple[str, ...] = (),
    trigger: str = "无",
) -> str:
    """一份 ResolutionOutput 形状的推演（ScriptedLLM 替地下城主交卷）：推理四段在前，符号层在后。"""
    return json.dumps({
        "collision": "两相比较。", "severity": severity, "cost": "代价已计。", "convergence": "收敛如下。",
        "deltas": [{"key": k, "value": v} for k, v in (deltas or {}).items()],
        "clock_mutations": list(clocks), "new_facts": list(facts), "action_trigger": trigger,
    }, ensure_ascii=False)


def start(name: str, kind: str, anchor: str, *, steps: int, maximum: int = 4, then: str = "") -> dict[str, Any]:
    return {"op": "新建", "clock": name, "kind": kind, "anchor": anchor, "maximum": maximum, "steps": steps, "consequence": then}


def tick(name: str, steps: int = 1) -> dict[str, Any]:
    return {"op": "推进", "clock": name, "steps": steps}


async def test_spawn_streams_an_opening_scene(container: Container) -> None:
    messages = await play(container, SpawnPlayer(name="阿星", location="无量山"))
    assert kinds(messages)[0] == "SessionOpened" and kinds(messages)[-1] == "TurnCompleted"
    assert "TurnResolved" not in kinds(messages)  # 投胎不是玩家的意图
    assert sum(isinstance(m, NarrationDelta) for m in messages) > 1  # 流式
    done = messages[-1]
    assert isinstance(done, TurnCompleted) and done.status.location == "无量山" and 3 <= len(done.options) <= 4
    assert done.narration.startswith("阿星初入江湖，现身于无量山。")
    assert done.status.time == "第一日·辰正"  # 投胎在第一日辰正，投胎本身不走时间


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
    refused, passed = (e.event for e in history[-2:])
    assert isinstance(refused, ActionFailed)  # 失败也入账
    assert isinstance(passed, TimePassed) and passed.ticks == 1  # 也花了时间：驳回只花一刻


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


async def test_observe_only_passes_time(container: Container) -> None:
    """静观不改变世界，却也花时间：只写一条 TimePassed（一刻），白描不出声，状态栏的时辰往前走了一刻。"""
    pid = await spawned_at(container, "无量山")
    resolved, done = await say(container, pid, "闭目养神")
    events = [e.event for e in await container.store.load(pid)]
    assert resolved.facts == () and len(events) == 2
    assert isinstance(events[-1], TimePassed) and events[-1].ticks == 1
    assert done.status.time == "第一日·辰正一刻"


async def test_an_out_of_envelope_reading_is_discarded_whole(settings: Settings) -> None:
    """
    地下城主推演出越级取胜（制住了左子穆——得手不在区间里）：整份推演作废，按确定性裁决（轻伤，气血带中值）结算，
    推演里的时钟、事实、名望一概不收；叙事大模型宣称"你一掌击毙了左子穆，夺得倚天剑"，事件流里照样只有轻伤退开。
    """
    intent = json.dumps({"action_type": "ATTACK", "target_entity": "左子穆", "narrative_style": "狂傲"}, ensure_ascii=False)
    greedy = gm(deltas={"制住:左子穆": 1, "名望": 5}, facts=("左子穆仰面倒在青石板上",),
                clocks=(start("左子穆的旧恨", "敌意", "左子穆", steps=1),))
    llm = ScriptedLLM("山风猎猎。", intent, greedy, "你一掌击毙了左子穆，夺得倚天剑！")
    container = await build_container(settings, blueprint=WORLD, llm=llm)
    try:
        pid = await spawned_at(container, "无量山")
        resolved, done = await say(container, pid, "我狂笑一声，一掌拍向那东宗掌门")
        assert resolved.facts[:2] == ("阿星徒手向左子穆出手——吃了点亏，带着轻伤退开。", "阿星受了伤（与左子穆交手）。")
        assert done.narration == "你一掌击毙了左子穆，夺得倚天剑！" and done.status.inventory == ()
        assert done.status.alive and done.status.health == "轻伤" and done.status.clocks == ()
        events = [e.event for e in await container.store.load(pid)]
        wound = next(e for e in events if isinstance(e, HealthChanged))
        assert wound.delta == -18  # 确定性裁决：轻伤气血带 [-25, -10] 的中值（向下取整）
        assert not any(isinstance(e, ClockStarted | FactEmerged | RenownChanged) for e in events)
        assert len(_gm_calls(llm)) == 1  # 合契约的推演不重采样：作废是闸门的事
        narration_prompt = llm.calls[-1][1]
        assert "<settled_facts>\n1. 阿星徒手向左子穆出手——吃了点亏" in narration_prompt
        assert "仰面倒" not in narration_prompt and "<gm_sketch>" not in narration_prompt and 'style="狂傲"' in narration_prompt
    finally:
        await container.aclose()


async def test_a_flight_is_narrated_where_the_fight_happened(settings: Settings) -> None:
    """重伤夺路而逃：叙事拿到的快照已是大理城，交手的无量山与龚光杰经 <fled_scene> 一并送到，不逼说书人在两条铁律间二选一。"""
    intent = json.dumps({"action_type": "ATTACK", "target_entity": "龚光杰"}, ensure_ascii=False)
    severe = gm(deltas={"气血": -50}, facts=("龚光杰的剑穗是新换的红绳",))
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
        assert '<location name="大理城"' in truth and "<gm_sketch>" not in truth
        assert "龚光杰的剑穗是新换的红绳。" in llm.calls[-1][1].split("<settled_facts>")[1]  # 推演的细节入账，经白描交给叙事
        assert "<emerged>" not in truth  # 它的主体不在逃抵之地：此世细节只随眼前之物召回
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
    """地下城主的调用：它的 schema 是 ResolutionOutput（推理层 severity + 符号层 deltas / clock_mutations）。"""
    return [c for c in llm.calls[since:] if c[2] is not None and {"severity", "clock_mutations"} <= c[2]["properties"].keys()]


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
    言辞求艺遇不肯：走交涉，地下城主只在区间（无果 / 碰壁）里推演，Parleyed 入账、开出一条心事线索，好过确定性裁决的那一格欠下戒心；
    下一份菜单给出没试过的手段（人情，why「换个手段」），它也在交给说书人的可供性目录里；点选它归气运，不请地下城主。
    """
    bp = canon()
    learn = _json(action_type="LEARN", target_entity="木婉清", skill_used="晓风拂柳", approach="言辞", aim="求艺")
    verdict = gm(facts=("木婉清的面幕上沾着几点露水",))
    llm = ScriptedLLM("山风猎猎。", learn, verdict, "木婉清不答。", "你又陪了几句好话。")
    container = await build_container(still(settings), blueprint=bp, llm=llm)
    try:
        pid = await spawned_at(container, "无量山")
        messages, spent, judged = await _turn(container, llm, SubmitText(player_id=pid, text="恳请木婉清传我晓风拂柳"))
        assert (spent, judged) == (3, 1)  # 意图、地下城主、叙事各一次
        resolved, done = messages[0], messages[-1]
        assert isinstance(resolved, TurnResolved) and isinstance(done, TurnCompleted)
        # 无果好过确定性裁决（碰壁）一格：等价交换的代价由领域补成木婉清身上的一格戒心；推演的细节照录
        assert resolved.facts == ("阿星以言辞向木婉清求艺，无果而终。", "暗流：木婉清的戒心（1/4）。", "木婉清的面幕上沾着几点露水。")
        system, brief, schema = _gm_calls(llm)[0]
        assert "- 如愿（" not in brief.split("<physics>")[1]  # 求艺够不上门槛：如愿不在区间里
        assert "所图" not in json.dumps(schema, ensure_ascii=False)  # 所图得成的属性键也就不在 schema 里
        assert "木婉清" in brief and not any(f in system + brief for f in _foreshadows(bp))  # 简报只用 T=0
        assert "木婉清的面幕上沾着几点露水。" in llm.calls[-1][1].split("<settled_facts>")[1]  # 推演的细节入账，交给叙事
        parley = next(e.event for e in reversed(await container.store.load(pid)) if isinstance(e.event, Parleyed))
        assert isinstance(parley, Parleyed) and parley.outcome is SocialOutcome.NOTHING and parley.subject_id == "art:晓风拂柳"
        assert [(p.label, p.note) for p in done.status.pursuits] == [("求艺 · 晓风拂柳", "尚无眉目；已试：言辞")]

        retry = next(o for o in done.options if o.why == "换个手段")
        assert retry.intent.approach is Approach.FAVOR and retry.intent.skill_used == "晓风拂柳"
        state, snap = await _state(container, pid)
        assert retry.id in {o.id for o in container.pipeline.options.catalogue(state, snap)}  # 目录先于叙事算好，交给说书人挑

        messages, spent, judged = await _turn(container, llm, ChooseOption(player_id=pid, option_id=retry.id))
        assert (spent, judged) == (1, 0)  # 点选：气运裁决，只花叙事一次
        parley = next(e.event for e in reversed(await container.store.load(pid)) if isinstance(e.event, Parleyed))
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
    暗取：点选「摸走」归气运（区间里好一格不存在、差一格是败露，只能留在未遂），文本骗貂请地下城主，败露即到手、到手即中毒，
    越出舒适区的得手由领域在钟灵身上补挂疑心；结果已定的文本回合只在眼前挂着时钟时请地下城主；通天草不能服用（NO_USE），金创药疗伤，服药只有一句白描（HealthChanged(source=item) 不出声）。
    """
    bp = _remedy_on_the_ground(canon())
    guile = _json(action_type="TAKE", target_entity="闪电貂", approach="计谋")
    exposed = gm(deltas={"所图": 1, "人情:钟灵": -1})
    llm = ScriptedLLM(
        "剑湖宫前。", "你的手缩了回来。",
        guile, exposed, "貂儿反口一咬。",
        _json(action_type="MOVE", target_entity="出大门"), gm(), "你出了宫门。",
        _json(action_type="TAKE", target_entity="通天草"), "你拔起一株草。",
        _json(action_type="USE", item_used="通天草"), "草叶苦涩。",
        _json(action_type="TAKE", target_entity="金创药"), "你拾起药瓶。",
        _json(action_type="USE", item_used="金创药"), "药力透入伤处。",
    )
    container = await build_container(still(settings), blueprint=bp, llm=llm)
    try:
        pid = await spawned_at(container, "剑湖宫")
        opening = (await play(container, ResumePlayer(player_id=pid, quiet=True)))[-1]
        assert isinstance(opening, TurnCompleted)
        steal = next(o for o in opening.options if o.intent.approach is Approach.STEALTH)
        assert steal.intent.target_entity == "闪电貂" and steal.risk is not None and steal.risk.value == "有险"
        _, spent, judged = await _turn(container, llm, ChooseOption(player_id=pid, option_id=steal.id))
        assert (spent, judged) == (1, 0)
        tried = (await ruled(container, pid))[-1]
        assert isinstance(tried, Maneuvered) and tried.outcome is CovertOutcome.FOILED

        messages, spent, judged = await _turn(container, llm, SubmitText(player_id=pid, text="骗钟灵把闪电貂借我玩玩"))
        assert (spent, judged) == (3, 1)
        assert "- 无痕（" not in _gm_calls(llm)[0][1].split("<physics>")[1]  # 好一格不存在：无痕不在区间里
        events = await ruled(container, pid)
        assert any(isinstance(e, Maneuvered) and e.outcome is CovertOutcome.EXPOSED for e in events)
        bite = events[-1]
        assert isinstance(bite, HealthChanged) and bite.source == "blow" and bite.source_id == "itm:闪电貂" and bite.delta < 0
        done = messages[-1]
        assert isinstance(done, TurnCompleted) and done.status.health == "轻伤" and "闪电貂" in done.status.inventory
        assert [(b.name, b.attitude, b.cause) for b in done.status.bonds] == [("钟灵", "敌视", "识破你的骗局")]

        # 败露是越出舒适区的得手：等价交换没付的格数由领域补成失主身上的疑心
        assert [(c.name, c.kind, c.progress) for c in done.status.clocks] == [("钟灵的疑心", "疑心", 2)]
        _, spent, judged = await _turn(container, llm, SubmitText(player_id=pid, text="出大门"))
        assert (spent, judged) == (3, 1)  # 结果已定，但眼前挂着钟灵的疑心：请地下城主一次
        _, spent, judged = await _turn(container, llm, SubmitText(player_id=pid, text="拾起通天草"))
        assert (spent, judged) == (2, 0)  # 结果已定、眼前又无暗流：意图与叙事，地下城主一次也不请
        messages, _, _ = await _turn(container, llm, SubmitText(player_id=pid, text="服下通天草"))
        refused = (await ruled(container, pid))[-1]
        assert isinstance(refused, ActionFailed) and refused.reason_code == "NO_USE"

        await _turn(container, llm, SubmitText(player_id=pid, text="拾起金创药"))
        messages, spent, judged = await _turn(container, llm, SubmitText(player_id=pid, text="敷上金创药"))
        resolved, done = messages[0], messages[-1]
        assert isinstance(resolved, TurnResolved) and resolved.facts == ("阿星以金创药疗伤。",)  # 回气血那条不出声
        assert "<settled_facts>\n1. 阿星以金创药疗伤。\n</settled_facts>" in llm.calls[-1][1]
        tail = (await ruled(container, pid))[-2:]
        assert isinstance(tail[0], ItemConsumed) and isinstance(tail[1], HealthChanged) and tail[1].source == "item"
        assert isinstance(done, TurnCompleted) and done.status.health == "安然无恙" and "金创药" not in done.status.inventory
        remembered = await container.pipeline._memory.recall(pid, "金创药 疗伤", 20, 10**6)
        assert remembered and all(m.text for m in remembered)  # 空串白描不入记忆
    finally:
        await container.aclose()


# ============================================================
#  语义物理引擎：推演入账（时钟 / 事实 / 名望）→ 快照召回 → 叙事与状态栏 → 坍缩成硬结算
# ============================================================
async def test_the_semantic_physics_engine_end_to_end(settings: Settings) -> None:
    """
    交涉回合的推演挂上一只疑心、留下一条细节、折损名望：事件入账、白描进 turn_resolved 与 <settled_facts>、
    新快照召回时钟与细节（<clocks> / <emerged> 进叙事）、状态栏亮出暗流与名望；挂着时钟时点选仍零次地下城主；
    结果已定的闲谈因挂着时钟请地下城主一次，却只动得了时钟、事实与名望；再一推满格坍缩——左子穆敌视、名声落到略有恶名。
    """
    befriend = _json(action_type="TALK", target_entity="左子穆", approach="言辞", aim="结交")
    first = gm("暗流", {"名望": -3}, (start("左子穆的疑心", "疑心", "左子穆", steps=2, then="识破你的来意"),),
               ("左子穆袖口沾着几点墨迹",))
    chat = _json(action_type="TALK", target_entity="辛双清")
    idle = gm("暗流", {"名望": -2, "人情:辛双清": -1}, (tick("左子穆的疑心"),), ("辛双清腰间悬着一柄短剑",))
    probe = _json(action_type="TALK", target_entity="左子穆", approach="言辞", aim="打探")
    burst = gm("爆炸", clocks=(tick("左子穆的疑心"),))
    llm = ScriptedLLM("山风猎猎。", befriend, first, "左子穆捻须不语。", "你四下看了看。",
                      chat, idle, "辛双清淡淡应了一声。", probe, burst, "左子穆脸色一沉。")
    container = await build_container(settings, blueprint=WORLD, llm=llm)
    try:
        pid = await spawned_at(container, "无量山")
        messages, spent, judged = await _turn(container, llm, SubmitText(player_id=pid, text="向左子穆套套近乎"))
        assert (spent, judged) == (3, 1)
        resolved, done = messages[0], messages[-1]
        assert isinstance(resolved, TurnResolved) and isinstance(done, TurnCompleted)
        events = [e.event for e in await container.store.load(pid)][1:]
        assert isinstance(events[0], Parleyed) and events[0].outcome is SocialOutcome.NOTHING
        hung = next(e for e in events if isinstance(e, ClockStarted))
        assert (hung.clock.name, hung.clock.kind.value, hung.clock.anchor_id, hung.clock.progress) == ("左子穆的疑心", "疑心", "chr:左子穆", 2)
        fact = next(e for e in events if isinstance(e, FactEmerged))
        assert fact.text == "左子穆袖口沾着几点墨迹" and fact.subject_ids == ("chr:左子穆",)
        assert next(e for e in events if isinstance(e, RenownChanged)).delta == -3
        assert "暗流：左子穆的疑心（2/4）。" in resolved.facts and "左子穆袖口沾着几点墨迹。" in resolved.facts
        assert any(line.startswith("阿星的名声坏了几分") for line in resolved.facts)
        narration_prompt = llm.calls[-1][1]
        assert "暗流：左子穆的疑心（2/4）。" in narration_prompt.split("<settled_facts>")[1]
        assert "- 左子穆的疑心｜疑心｜挂在左子穆｜2/4｜满则：识破你的来意" in narration_prompt.split("<clocks>")[1]
        assert "- 左子穆袖口沾着几点墨迹" in narration_prompt.split("<emerged>")[1]
        assert "clk:" not in narration_prompt and "emg:" not in narration_prompt
        snap = await container.reader.local_snapshot(pid)  # 下一回合的快照召回挂在眼前之人身上的时钟与点了他的细节
        assert [(c.name, c.progress) for c in snap.clocks] == [("左子穆的疑心", 2)]
        assert [e.text for e in snap.emerged] == ["左子穆袖口沾着几点墨迹"]
        assert [(c.name, c.kind, c.progress, c.maximum) for c in done.status.clocks] == [("左子穆的疑心", "疑心", 2, 4)]
        assert done.status.renown == "籍籍无名"

        state, snap = await _state(container, pid)
        look = next(o for o in done.options if (env := envelope(o.intent, state, snap)) and not env.contested)
        _, spent, judged = await _turn(container, llm, ChooseOption(player_id=pid, option_id=look.id))
        assert (spent, judged) == (1, 0)  # 点选从不为时钟请人：只花叙事一次

        before = len(await container.store.load(pid))
        messages, spent, judged = await _turn(container, llm, SubmitText(player_id=pid, text="和辛双清闲聊几句"))
        assert (spent, judged) == (3, 1)  # 结果已定，但此景挂着时钟：请地下城主一次
        assert _gm_calls(llm)[-1][2]["properties"]["action_trigger"]["enum"] == ["无"]
        idle_events = [e.event for e in (await container.store.load(pid))[before:]]
        assert not any(isinstance(e, RelationChanged) for e in idle_events)  # 结果已定之事动不了旁人的人情
        assert {type(e) for e in idle_events} >= {RenownChanged, ClockAdvanced, FactEmerged}  # 时钟、事实、名望照收
        assert next(e for e in idle_events if isinstance(e, RenownChanged)).delta == -2
        assert messages[-1].status.clocks[0].progress == 3

        before = len(await container.store.load(pid))
        messages, spent, judged = await _turn(container, llm, SubmitText(player_id=pid, text="向左子穆打听剑湖宫的事"))
        assert (spent, judged) == (3, 1)
        burst_events = [e.event for e in (await container.store.load(pid))[before:]]
        collapsed = next(e for e in burst_events if isinstance(e, ClockCollapsed))
        assert collapsed.name == "左子穆的疑心" and collapsed.consequence == "识破你的来意"
        assert any(isinstance(e, RelationChanged) and e.character_id == "chr:左子穆" and e.attitude.value == "敌视"
                   for e in burst_events)  # 疑心满了：挂处之人翻脸
        assert any(isinstance(e, RenownChanged) and e.delta == -5 for e in burst_events)
        resolved, done = messages[0], messages[-1]
        assert "左子穆的疑心满了：识破你的来意。" in resolved.facts
        assert done.status.clocks == () and done.status.renown == "略有恶名"  # −3 −2 −5 = −10
        assert ("左子穆", "敌视") in [(b.name, b.attitude) for b in done.status.bonds]
        assert (await container.reader.local_snapshot(pid)).clocks == ()  # 坍缩即退场
    finally:
        await container.aclose()


async def _state(container: Container, pid: str) -> tuple[Any, Any]:
    player = await container.pipeline.load(pid)
    return player.state, await container.pipeline.snapshot(player)


async def test_a_peril_clock_collapses_into_a_forced_flight(settings: Settings) -> None:
    """此地挂着的危机满了：受创三十（留一口气）、被迫沿退路夺路而逃——哪怕这一举只是静观四周（结果已定之事也得付暗流的账）。"""
    befriend = _json(action_type="TALK", target_entity="辛双清", approach="言辞", aim="结交")
    flood = gm("暗流", clocks=(start("山洪将至", "危机", "无量山", steps=3, then="山洪冲下山道"),))
    look = _json(action_type="OBSERVE")
    burst = gm("爆炸", clocks=(tick("山洪将至"),), facts=("山道上的碎石还在滚落",))
    llm = ScriptedLLM("山风猎猎。", befriend, flood, "山间隐隐有雷声。", look, burst, "洪水奔腾而下。")
    container = await build_container(settings, blueprint=WORLD, llm=llm)
    try:
        pid = await spawned_at(container, "无量山")
        _, done = await say(container, pid, "与辛双清攀谈结交")
        assert [(c.name, c.kind, c.progress) for c in done.status.clocks] == [("山洪将至", "危机", 3)]
        before = len(await container.store.load(pid))
        messages, spent, judged = await _turn(container, llm, SubmitText(player_id=pid, text="静观四周"))
        assert (spent, judged) == (3, 1)
        events = [e.event for e in (await container.store.load(pid))[before:]]
        assert any(isinstance(e, ClockCollapsed) and e.name == "山洪将至" for e in events)
        assert any(isinstance(e, HealthChanged) and e.delta == -30 for e in events)
        flight = next(e for e in events if isinstance(e, Moved))
        assert flight.fleeing and flight.to_location_id == "loc:大理城"
        done = messages[-1]
        assert isinstance(done, TurnCompleted) and done.status.location == "大理城" and done.status.alive
        assert done.status.clocks == ()
    finally:
        await container.aclose()


async def test_clicks_never_consult_the_gm_and_fortune_pays_its_way(settings: Settings) -> None:
    """点选零次地下城主；气运掷出好过确定性裁决的一格，等价交换的代价由领域补成对象身上的一只凶险时钟。"""
    llm = ScriptedLLM(*["山风猎猎。"] * 40)
    container = await build_container(settings, blueprint=WORLD, llm=llm)
    try:
        for n in range(30):  # 种子含玩家 id：换人直到有人对某个有赌注的选项掷出好一格（每人约四分之一）
            pid = await spawned_at(container, "无量山", name=f"阿星{n}")
            state, snap = await _state(container, pid)
            lucky = [
                (o, env) for o in container.pipeline.options.generate(state, snap)
                if (env := envelope(o.intent, state, snap)) is not None and env.contested and env.canonical is not None
                and env.admissible.index(_draw(env, fortune_seed(env, state))) < env.admissible.index(env.canonical)
            ]
            if lucky:
                break
        else:
            pytest.fail("三十人里竟无一人走运")
        option, env = lucky[0]
        before = len(await container.store.load(pid))
        _, spent, judged = await _turn(container, llm, ChooseOption(player_id=pid, option_id=option.id))
        assert (spent, judged) == (1, 0)
        events = [e.event for e in (await container.store.load(pid))[before:]]
        cost = [e for e in events if isinstance(e, ClockStarted) and e.cause == "代价"]
        assert cost and cost[0].clock.anchor_id == env.target_id and cost[0].clock.kind.threat
    finally:
        await container.aclose()


class Reading(Resolver):
    """地下城主替身：按次序交出推演（JSON 经 ResolutionOutput 校验），用完即空提议；记下被请了几次。"""

    def __init__(self, *outputs: str) -> None:
        self._outputs = [ResolutionOutput.model_validate_json(o) for o in outputs]
        self.consulted = 0

    async def resolve(self, env: Any, scene: Any, state: Any, intent: Any, said: str | None) -> Resolution:
        self.consulted += 1
        return Resolution(self._outputs.pop(0) if self._outputs else None, "地下城主")


@pytest.mark.postgres
@pytest.mark.neo4j
async def test_full_game_on_real_backends(settings: Settings) -> None:
    if not (PG_DSN and NEO4J_URI):
        pytest.skip("需要同时设置 TLBB_TEST_POSTGRES_DSN 与 TLBB_TEST_NEO4J_URI")
    real = settings.model_copy(update={
        "event_store": "postgres", "postgres_dsn": PG_DSN, "graph_backend": "neo4j",
        "neo4j_uri": NEO4J_URI, "neo4j_user": NEO4J_USER, "neo4j_password": NEO4J_PASSWORD,
    })
    gm_reading = Reading(gm("暗流", {"气血": -15}, (
        start("左子穆的杀意", "敌意", "左子穆", steps=1, then="拔剑寻你拼命"),
        start("掌心发麻", "危机", "你", steps=1, maximum=8, then="寒毒攻心"),
    ), ("左子穆的剑鞘磨得发亮",)))
    container = await build_container(real, blueprint=WORLD, resolver=gm_reading)
    try:
        pid = await spawned_at(container, "无量山")
        await say(container, pid, "攻击左子穆")
        snap = await container.reader.local_snapshot(pid)  # Neo4j 的覆盖层：(:Clock)-[:ON]-> 与 (:Emerged)-[:ABOUT]-> 在下一张快照里召回
        assert sorted((c.name, c.anchor_id, c.progress) for c in snap.clocks) == [
            ("左子穆的杀意", "chr:左子穆", 1), ("掌心发麻", pid, 1),
        ]
        assert [(e.text, e.subject_ids) for e in snap.emerged] == [("左子穆的剑鞘磨得发亮", ("chr:左子穆",))]
        # 世界心跳经 Neo4j 覆盖层：交手在无量山留下往事与痕迹，消息从无量山传开
        assert [(a.kind.value, set(a.participants)) for a in snap.activities] == [("交手", {pid, "chr:左子穆"})]
        assert len(snap.traces) == 1 and [r.origin_id for r in snap.rumors] == ["loc:无量山"]
        for text in ("拾起玉佩", "去崖下", "拾起卷轴", "参悟北冥神功", "参照卷轴苦练北冥神功"):
            _, last = await say(container, pid, text)  # 身上挂着时钟：每个文本回合都请替身一次，推演用完即空提议
        history = await container.store.load(pid)
        await container.projector.forget(pid)
        messages = await play(container, ResumePlayer(player_id=pid))  # 抹掉 Neo4j 覆盖层，凭 PostgreSQL 事件流重建
        done = messages[-1]
        assert isinstance(done, TurnCompleted) and done.status.location == "无量玉洞"
        assert done.status.time == last.status.time != "第一日·辰正"  # 时辰随事件流重建：重放的是同一段光阴
        assert done.status.health == "轻伤" and set(done.status.inventory) == {"玉佩", "北冥神功卷轴"}
        assert sum(isinstance(e.event, SkillPracticed) for e in history) == 2
        assert len(done.status.skills) == 1 and done.status.skills[0].startswith("北冥神功（")
        assert [(c.name, c.kind) for c in done.status.clocks] == [("掌心发麻", "危机")]  # 挂在你身上的随身走，重建后照样召回
        assert gm_reading.consulted == 6
    finally:
        await container.aclose()
