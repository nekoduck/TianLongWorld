"""
[INPUT]: 依赖 domain/events 的全部领域事件（含 P1 的 Parleyed / FactLearned / ItemConsumed / Maneuvered 与语义物理引擎的时钟 / 微观事实 / 名望事件）与 EventEnvelope，
         依赖 domain/clocks 的 NarrativeClock / started / advanced / retired，依赖 domain/ambient 的 Activity / EnvironmentalTrace / FactToken 与写时复制操作，
         依赖 domain/commands 的 SPAWN_TICK，依赖 domain/agenda 的 NpcAgenda / Encounter，依赖 domain/models 的 Attitude，依赖 domain/progression 的 MAX_HP / Mastery / Vitality /
         mastery_of / vitality / aptitude_for，依赖 domain/combat 的 CombatOutcome / CombatProposal，依赖 domain/stakes 的 Proposal，依赖 domain/resolution 的 ResolutionOutput，
         依赖 domain/threads 的 Thread / fold，依赖 domain/intent 的 Approach / PlayerIntent，
         依赖 domain/rules 的 decide()（裁决），依赖 app.errors 的 UnknownPlayerError / PlayerDeadError
[OUTPUT]: 对外提供 PlayerState（不可变状态值：practice 熟练度之和、aptitude 悟性、hp 气血、came_from 来路、fled_from 逃离过的险地、
          focus 近来打过交道的人与物（至多 FOCUS_SIZE 个，新者在前）与 focus_fresh 上一个主动作是否正与它打交道、attitude_causes 人情的缘由、
          threads 心事线索（至多 6 条）、known_facts 已知见闻、consumed 用掉之物、recent_approaches 近来用过的手段（至多 RECENT_SIZE 个）、
          attempts 对每个对象出过几次有赌注的招（FortuneResolver 的种子）、taken_from 物品最初从谁手里到你身上，
          clocks 悬着的叙事时钟、emerged 推演出的微观事实（新者在前、至多 EMERGED_MAX 条）、renown 名望点数，
          tick 世界时间、motivation 此行所为、activities / traces / tokens 此世的活动、痕迹与消息，
          visited / heard 到过与问路得知的地方（去处的认知）、agendas / npc_at / npc_since / npc_wounds / agenda_tick / encounters 分层 NPC 生态的此世状态，
          世界本份的 traits 命格特质 / hidden_items 藏在身上的东西 / sightings 谁见过你什么样子 / challenges 悬着的对峙 / collateral 被卷进的局势 / fronts 局势推进到哪里，
          编剧代理的 karma 未了的因果线 / arc_marks 弧光标记 / chapter 当下这一章，
          mastery / vitality / inventory 现算）、FOCUS_SIZE、RECENT_SIZE、EMERGED_MAX、EmergedFact、
          evolve(state, event) 纯函数折叠、Player 聚合根（apply / from_history / replay / spawn / ensure_alive / mastery / decide）
[POS]: domain 的一致性边界：一位玩家的平行世界就是一条事件流，世界在这条流上相对原著的全部偏离（位置、行囊、武学火候、气血、
       被制住之人、人情冷暖、物品易手）都是 PlayerState 的字段。没有状态表——当前状态只能由 evolve 从头折叠事件流算出；
       渐进式状态只做加法：熟练度 = Σ SkillPracticed.proficiency_gained，气血 = MAX_HP + Σ HealthChanged.delta（钳位），
       火候与伤势是读取时经 progression 折算的语义标签，从不入账。
       焦点（focus）与恩怨缘由（attitude_causes）同样只由现有事件折叠：选项跟着剧情走、叙事知道仇从何来，都不需要新的事件；
       焦点「不新鲜」的判据认 HealthChanged.source=="rest"（服药的气血回升不算走开）。
       P1 的四种新事件各有折叠：Parleyed / Maneuvered 进焦点、手段与尝试次数，心事线索由 threads.fold 开合；
       FactLearned 记入已知见闻；ItemConsumed 记入 consumed，行囊派生时把它排除（易手覆盖照旧，用掉之物从此不在任何人身上）；
       ItemTransferred 第一次到你手上时记下来路 taken_from（此后转手再拿回来也不改）：从物主手里拿来的东西再还给他，
       只是易手，不是物归原主——经第三人转一道手也洗不白。
       语义物理引擎的落账同样只做折叠：时钟按 id 挂上、推进回退（钳在阈值之下）、坍缩或销毁即退场；微观事实按正文摘要去重、新者在前；
       名望只做加法并钳在 ±RENOWN_MAX。
       世界心跳同样只做折叠：TimePassed 累加 tick 并剪掉到期的痕迹与随之消散的已结束活动；活动、痕迹、消息按 id 挂上（超上限请走最旧的），
       消息传到之处只增不减；朽坏之物折进 consumed（与用掉之物一样从此不在任何地方）；被人顺手拿走只改持有者——不进焦点、不记来路、不了结心事。
       空间认知与 NPC 生态同样只做折叠：投胎之地与每个去处进 visited、问路得知进 heard；议程按 NPC 挂上（启程之刻进 npc_since：立议程之刻与驻足到之刻取较晚者，
       同一批里裁决之后再立的新议程不抹掉驻足）、了结即摘下；
       NPC 每走一跳改 npc_at 与 npc_since；中断挂进 encounters、裁决之后摘下并让当事 NPC 驻足到 resume_tick；受伤记到 npc_wounds。
       世界本份同样只做折叠：投胎即定命格（PlayerSpawned.traits，旧账由 id 现算），刀疤后来添上；东西到手时 concealed 即藏起、亮出（ItemShown）或交出去即不再藏；
       PlayerNoticed 记下他看见的样子；ThreatDeclared 挂上对峙（每人一条，新覆盖旧）、ChallengeEnded 摘下；局势按 FrontAdvanced 推进、FrontEnded 留下了结的标记；
       CollateralStruck / CollateralEnded 挂上与摘下被卷进的局势。
       编剧代理只记伏笔与潜台词：每条事件经 screenplay.mark 折成弧光标记（至多 ARC_WINDOW 枚）；因果线新者在前、至多 KARMA_MAX 条，
       议程带着 karma_id 立下即把那条线转为回响，了结即摘下；ChapterOpened 换上新的一章。
       内存图谱投影（infrastructure/persistence/memory_graph.py）复用同一个 evolve，投影与真相因此同构
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from functools import reduce

from app.domain import rules
from app.domain.agenda import Encounter, NpcAgenda
from app.domain.ambient import (
    Activity,
    EnvironmentalTrace,
    FactToken,
    elapse,
    reached,
    with_activity,
    with_token,
    with_trace,
)
from app.domain.arc import ARC_WINDOW, PROLOGUE, ArcPhase, Chapter
from app.domain.clocks import NarrativeClock, advanced, retired, started
from app.domain.combat import CombatOutcome, CombatProposal
from app.domain.commands import SPAWN_TICK
from app.domain.events import (
    ActionFailed,
    ActivityStarted,
    AgendaConcluded,
    AgendaIssued,
    AgendaPlanned,
    ChallengeEnded,
    ChapterOpened,
    ClockAdvanced,
    ClockCleared,
    ClockCollapsed,
    ClockStarted,
    CollateralEnded,
    CollateralStruck,
    Conversed,
    DomainEvent,
    EncounterBegan,
    EncounterResolved,
    EventEnvelope,
    FactEmerged,
    FactLearned,
    FactTokenSpawned,
    FrontAdvanced,
    FrontEnded,
    HealthChanged,
    ItemConsumed,
    ItemDecayed,
    ItemPilfered,
    ItemShown,
    ItemTransferred,
    KarmaThreadOpened,
    KarmaThreadResolved,
    Maneuvered,
    Moved,
    NpcMoved,
    NpcWounded,
    Parleyed,
    PlacesLearned,
    PlayerDied,
    PlayerNoticed,
    PlayerSpawned,
    RelationChanged,
    RenownChanged,
    RumorSpread,
    SkillExecuted,
    SkillPracticed,
    ThreatDeclared,
    TimePassed,
    TraceLeft,
    TraitAcquired,
)
from app.domain.intent import Approach, PlayerIntent
from app.domain.karma import KARMA_MAX, KarmaStatus, KarmaThread
from app.domain.models import Attitude
from app.domain.progression import (
    MAX_HP,
    RENOWN_MAX,
    Mastery,
    Renown,
    Vitality,
    aptitude_for,
    mastery_of,
    renown,
    vitality,
)
from app.domain.resolution import ResolutionOutput
from app.domain.screenplay import mark
from app.domain.signature import fated
from app.domain.snapshot import LocalSnapshot
from app.domain.stage import Challenge, Collateral, FrontProgress, Sighting
from app.domain.stakes import Proposal
from app.domain.threads import Thread
from app.domain.threads import fold as fold_threads
from app.errors import PlayerDeadError, UnknownPlayerError


@dataclass(frozen=True, slots=True)
class EmergedFact:
    """推演出的微观事实：正文与它点了名的场景实体（图谱据此把它挂在人、物、地上）。"""

    id: str
    text: str
    subject_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PlayerState:
    """
    玩家平行世界相对原著的全部偏离。映射字段在 evolve 中只做写时复制，从不原地修改。
    item_holders 只记离开过原位的物品：没出现在这里的物品仍在原著给它的位置。
    """

    player_id: str
    name: str
    location_id: str
    came_from: str | None = None  # 上一次移动的出发地：重伤逃脱时沿来路退回
    fled_from: frozenset[str] = frozenset()  # 夺路逃离过的险地：仇人不会挪窝，逃跑绝不逃回那里
    aptitude: float = 1.0
    hp: int = MAX_HP
    alive: bool = True
    death_cause: str | None = None
    practice: Mapping[str, int] = field(default_factory=dict)  # 武学 → 历次修习所得熟练度之和
    item_holders: Mapping[str, str] = field(default_factory=dict)
    subdued: frozenset[str] = frozenset()
    attitudes: Mapping[str, Attitude] = field(default_factory=dict)
    attitude_causes: Mapping[str, str] = field(default_factory=dict)  # 人物 → 最近一次人情变化的缘由（RelationChanged.cause）
    focus: tuple[str, ...] = ()  # 近来亲手打过交道的人与物，新者在前、至多 FOCUS_SIZE 个（定义见 _engaged）
    focus_fresh: bool = False  # 上一个主动作是否正与 focus[0] 打交道：走开、调息、碰壁之后它就只是「先前」的人与事
    threads: tuple[Thread, ...] = ()  # 心事线索：未了的所图，新者在前、至多 THREADS_MAX 条（threads.py）
    known_facts: frozenset[str] = frozenset()  # 已知见闻（fact:）
    consumed: frozenset[str] = frozenset()  # 用掉之物：从此不在行囊、不在任何地方
    recent_approaches: tuple[Approach, ...] = ()  # 近来出招用过的手段，新者在前、至多 RECENT_SIZE 个
    attempts: Mapping[str, int] = field(default_factory=dict)  # 对象 → 出过几次有赌注的招（出手 / 交涉 / 暗取）
    taken_from: Mapping[str, str] = field(default_factory=dict)  # 物品 → 它最初从谁那里到你手上（物归原主据此辨认偷来、讨来、夺来的）
    clocks: tuple[NarrativeClock, ...] = ()  # 悬着的叙事时钟（按 id 排序）
    emerged: tuple[EmergedFact, ...] = ()  # 推演出的微观事实，新者在前、至多 EMERGED_MAX 条
    renown_points: int = 0  # 名望：Σ RenownChanged，钳在 ±RENOWN_MAX
    tick: int = SPAWN_TICK  # 世界时间：SPAWN_TICK + Σ TimePassed.ticks（一刻十五分钟）
    motivation: str = ""  # 最近一次移动的此行所为（Moved.motivation）
    activities: tuple[Activity, ...] = ()  # 此世记着的活动（按 id 排序，至多 ACTIVITIES_MAX）：已结束的随痕迹一同消散
    traces: tuple[EnvironmentalTrace, ...] = ()  # 此世尚未消散的环境痕迹（按 id 排序，至多 TRACES_MAX）
    tokens: tuple[FactToken, ...] = ()  # 此世传开的消息（按 id 排序，至多 TOKENS_MAX）与它们已传到之处
    visited: frozenset[str] = frozenset()  # 到过的地方（投胎之地与每一次移动的去处）：去处的认知「亲历」
    heard: frozenset[str] = frozenset()  # 问路得知的地方（PlacesLearned）：去处的认知「问路」
    agendas: Mapping[str, NpcAgenda] = field(default_factory=dict)  # 核心 NPC → 进行中的议程
    npc_at: Mapping[str, str] = field(default_factory=dict)  # NPC → 此世此刻所在（只记离开过正典所在的）
    npc_since: Mapping[str, int] = field(default_factory=dict)  # NPC → 抵达当前所在（或启程 / 驻足到）之刻：下一跳从这一刻起算
    npc_wounds: Mapping[str, int] = field(default_factory=dict)  # NPC → 伤到哪一刻为止
    agenda_tick: int | None = None  # 上一轮宏观议程规划之刻（None：还没规划过）
    encounters: tuple[Encounter, ...] = ()  # 待裁决的中断（EncounterBegan 之后、EncounterResolved 之前）
    # 世界本份：命格与外显、被谁看见过、悬着的对峙、被卷进的局势、局势推进到哪里
    traits: tuple[str, ...] = ()  # 命格特质（相貌 / 口音 / 装束 / 印记）与后来添上的（刀疤）
    hidden_items: frozenset[str] = frozenset()  # 藏在身上的东西：旁人看不见，亮出来（ItemShown）之后才外露
    sightings: Mapping[str, Sighting] = field(default_factory=dict)  # NPC → 他最近一次看见你的样子
    challenges: tuple[Challenge, ...] = ()  # 悬着的对峙（每位 NPC 至多一条）：盘问、喝止、敌意
    collateral: tuple[Collateral, ...] = ()  # 被卷进的局势（每股局势至多一条）
    fronts: Mapping[str, FrontProgress] = field(default_factory=dict)  # 局势 → 此世推进到哪一站（了结的留着，免得重起）
    # 编剧代理：悬着的因果线与命运弧光
    karma: tuple[KarmaThread, ...] = ()  # 未了的因果线，新者在前、至多 KARMA_MAX 条
    arc_marks: tuple[ArcPhase, ...] = ()  # 弧光标记，新者在前、至多 ARC_WINDOW 枚（screenplay.mark）
    chapter: Chapter = PROLOGUE  # 当下这一章：主题、基调、意象、潜台词

    @property
    def skills(self) -> frozenset[str]:
        """已入门的武学：修习过至少一次。"""
        return frozenset(skill for skill, points in self.practice.items() if points > 0)

    def mastery(self, skill_id: str) -> Mastery | None:
        """火候 = 熟练度之和 × 悟性，未入门为 None。"""
        return mastery_of(self.practice.get(skill_id, 0), self.aptitude)

    @property
    def vitality(self) -> Vitality:
        return vitality(self.hp)

    @property
    def inventory(self) -> frozenset[str]:
        """行囊不是一张表，而是"此刻持有者是我"且没用掉的那些物品——由物品易手与用掉的历史推导。"""
        return frozenset(
            item for item, holder in self.item_holders.items() if holder == self.player_id and item not in self.consumed
        )

    def attitude_of(self, character_id: str) -> Attitude:
        return self.attitudes.get(character_id, Attitude.NEUTRAL)

    @property
    def renown(self) -> Renown:
        return renown(self.renown_points)

    def clock(self, clock_id: str) -> NarrativeClock | None:
        return next((c for c in self.clocks if c.id == clock_id), None)


# ============================================================
#  焦点 —— 玩家亲手打过交道的人与物，供选项的显著性打分与记忆召回
# ============================================================
FOCUS_SIZE = 4
RECENT_SIZE = 4
EMERGED_MAX = 24  # 微观事实只留最近的二十四条：它们是此世的细节，不是史书


def _engaged(event: DomainEvent, player_id: str) -> tuple[str, ...]:
    """
    一条事件里玩家亲手打过交道的实体，排在前面的更要紧：
      出手（SkillExecuted）→ 对手；攀谈（Conversed）→ 对方；
      易手（ItemTransferred）→ 另一端的人（从被制住者身上取、交还物主）在前，那件东西在后；
      修习（SkillPracticed）→ 传功点拨之人或所凭典籍（闭门苦练没有对象）；
      交涉（Parleyed）→ 对方；暗中取物（Maneuvered）→ 失主在前、那件东西在后。
    人情涟漪（RelationChanged）不算：目睹者并没有和你打交道，被你打的人已由出手记下；
    驳回（ActionFailed）的指称是原话而非实体 id，地点（Moved）是去处而非对象——都不进焦点。
    """
    match event:
        case SkillExecuted(target_id=target) | Conversed(npc_id=target):
            return (target,)
        case ItemTransferred(item_id=item, from_holder=giver, to_holder=taker):
            other = taker if giver == player_id else giver
            return (other, item) if other.startswith("chr:") else (item,)
        case SkillPracticed(source_id=str(source)):
            return (source,)
        case Parleyed(npc_id=target):
            return (target,)
        case Maneuvered(target_id=target, item_id=item):
            return (target, item)
    return ()


def _attempt(event: DomainEvent) -> tuple[str, Approach] | None:
    """有赌注的一招：对谁、用什么手段。尝试次数与近来手段由它们折叠。"""
    match event:
        case SkillExecuted(target_id=target, approach=approach) | Parleyed(npc_id=target, approach=approach):
            return target, approach
        case Maneuvered(target_id=target, approach=approach):
            return target, approach
    return None


def _refocus(focus: tuple[str, ...], engaged: tuple[str, ...]) -> tuple[str, ...]:
    return (*engaged, *(x for x in focus if x not in engaged))[:FOCUS_SIZE]


def _moves_on(event: DomainEvent) -> bool:
    """
    不与谁打交道的主动作：自行离去、调息（source="rest" 的气血回升；旧账经上抛器读作 rest）、闭门苦练。它们让焦点不再「新鲜」。
    交手的余波（人情涟漪、伤人者造成的气血涨落、身死、夺路而逃）属于上一招，不算；
    碰壁（ActionFailed）与静观一样什么也没改变——世界不变，菜单也该逐字不变。
    """
    match event:
        case Moved(fleeing=False) | SkillPracticed(source_id=None):
            return True
        case HealthChanged(source="rest"):
            return True
    return False


# ============================================================
#  纯函数折叠 —— (状态, 事件) → 新状态；不读时钟、不查数据库、不抛业务异常
# ============================================================
def evolve(state: PlayerState | None, event: DomainEvent) -> PlayerState:
    if isinstance(event, PlayerSpawned):
        return PlayerState(
            player_id=event.player_id, name=event.name, location_id=event.location_id, aptitude=event.aptitude,
            visited=frozenset({event.location_id}),
            traits=event.traits if event.traits is not None else fated(event.player_id),
        )
    if state is None:
        raise ValueError(f"事件流必须以 PlayerSpawned 开头，却遇到 {type(event).__name__}")
    if engaged := _engaged(event, state.player_id):
        state = replace(state, focus=_refocus(state.focus, engaged), focus_fresh=True)
    elif state.focus_fresh and _moves_on(event):
        state = replace(state, focus_fresh=False)
    if (threads := fold_threads(state.threads, event, state.player_id, state.attitudes)) != state.threads:
        state = replace(state, threads=threads)
    if (marked := mark(event, state.player_id)) is not None:
        state = replace(state, arc_marks=(marked, *state.arc_marks)[:ARC_WINDOW])
    if attempt := _attempt(event):
        target, approach = attempt
        state = replace(
            state,
            attempts={**state.attempts, target: state.attempts.get(target, 0) + 1},
            recent_approaches=(approach, *state.recent_approaches)[:RECENT_SIZE],
        )

    match event:
        case Moved(from_location_id=origin, to_location_id=destination, fleeing=fleeing, motivation=motivation):
            fled = state.fled_from | {origin} if fleeing else state.fled_from
            return replace(state, location_id=destination, came_from=origin, fled_from=fled, motivation=motivation,
                           visited=state.visited | {destination})
        case ItemTransferred(item_id=item, from_holder=giver, to_holder=holder, concealed=concealed):
            provenance = state.taken_from
            if holder == state.player_id and item not in provenance:  # 只记最初的来路：转手第三人再拿回来，洗不掉偷来的底子
                provenance = {**provenance, item: giver}
            hidden = state.hidden_items
            if holder == state.player_id:
                hidden = hidden | {item} if concealed else hidden - {item}
            elif giver == state.player_id:
                hidden = hidden - {item}
            return replace(state, item_holders={**state.item_holders, item: holder}, taken_from=provenance, hidden_items=hidden)
        case SkillPracticed(skill_id=skill, proficiency_gained=gained):
            return replace(state, practice={**state.practice, skill: state.practice.get(skill, 0) + gained})
        case HealthChanged(delta=delta):
            return replace(state, hp=min(MAX_HP, max(0, state.hp + delta)))
        case SkillExecuted(outcome=CombatOutcome.SUCCESS, target_id=target):
            return replace(state, subdued=state.subdued | {target})
        case RelationChanged(character_id=character, attitude=attitude, cause=cause):
            return replace(
                state,
                attitudes={**state.attitudes, character: attitude},
                attitude_causes={**state.attitude_causes, character: cause},
            )
        case PlayerDied(cause=cause):
            return replace(state, alive=False, death_cause=cause)
        case FactLearned(fact_id=fact):
            return replace(state, known_facts=state.known_facts | {fact})
        case ItemConsumed(item_id=item):
            return replace(state, consumed=state.consumed | {item})
        case ClockStarted(clock=clock):
            return replace(state, clocks=started(state.clocks, clock))
        case ClockAdvanced(clock_id=clock_id, steps=steps):
            return replace(state, clocks=advanced(state.clocks, clock_id, steps))
        case ClockCollapsed(clock_id=clock_id) | ClockCleared(clock_id=clock_id):
            return replace(state, clocks=retired(state.clocks, clock_id))
        case FactEmerged(fact_id=fact_id, text=text, subject_ids=subjects):
            fresh = EmergedFact(id=fact_id, text=text, subject_ids=subjects)
            return replace(state, emerged=(fresh, *(f for f in state.emerged if f.id != fact_id))[:EMERGED_MAX])
        case RenownChanged(delta=delta):
            return replace(state, renown_points=max(-RENOWN_MAX, min(RENOWN_MAX, state.renown_points + delta)))
        case TimePassed(ticks=ticks):
            now = state.tick + ticks
            activities, traces = elapse(state.activities, state.traces, now)
            return replace(state, tick=now, activities=activities, traces=traces)
        case ActivityStarted(activity=activity):
            return replace(state, activities=with_activity(state.activities, activity))
        case TraceLeft(trace=trace):
            return replace(state, traces=with_trace(state.traces, trace))
        case FactTokenSpawned(token=token):
            return replace(state, tokens=with_token(state.tokens, token))
        case RumorSpread(token_id=token_id, location_ids=places):
            return replace(state, tokens=reached(state.tokens, token_id, places))
        case ItemDecayed(item_id=item):
            return replace(state, consumed=state.consumed | {item})
        case ItemPilfered(item_id=item, to_holder=holder):
            return replace(state, item_holders={**state.item_holders, item: holder})
        case PlacesLearned(location_ids=places):
            return replace(state, heard=state.heard | set(places))
        case AgendaPlanned(tick=tick):
            return replace(state, agenda_tick=tick)
        case AgendaIssued(agenda=agenda):
            start = max(state.npc_since.get(agenda.npc_id, agenda.issued_tick), agenda.issued_tick)  # 新议程不抹掉驻足
            return replace(
                state,
                agendas={**state.agendas, agenda.npc_id: agenda},
                npc_since={**state.npc_since, agenda.npc_id: start},
                karma=_echoed(state.karma, agenda.karma_id, agenda.npc_id),
            )
        case AgendaConcluded(npc_id=npc):
            return replace(state, agendas={k: v for k, v in state.agendas.items() if k != npc})
        case NpcMoved(npc_id=npc, to_location_id=there, tick=tick):
            return replace(state, npc_at={**state.npc_at, npc: there}, npc_since={**state.npc_since, npc: tick})
        case EncounterBegan(encounter=encounter):
            return replace(state, encounters=(*(e for e in state.encounters if e.id != encounter.id), encounter))
        case EncounterResolved(encounter_id=done, npc_ids=npcs, resume_tick=resume):
            since = {**state.npc_since, **{n: max(state.npc_since.get(n, 0), resume) for n in npcs}}
            return replace(state, encounters=tuple(e for e in state.encounters if e.id != done), npc_since=since)
        case NpcWounded(npc_id=npc, until_tick=until):
            return replace(state, npc_wounds={**state.npc_wounds, npc: until})
        case TraitAcquired(trait=trait):
            return state if trait in state.traits else replace(state, traits=(*state.traits, trait))
        case ItemShown(item_id=item):
            return replace(state, hidden_items=state.hidden_items - {item})
        case PlayerNoticed(npc_id=npc, location_id=where, tick=tick, traits=traits, items=items, digest=digest):
            seen = Sighting(location_id=where, tick=tick, traits=traits, items=items, digest=digest)
            return replace(state, sightings={**state.sightings, npc: seen})
        case ThreatDeclared(npc_id=npc, stance=stance, trigger=trigger, location_id=where, tick=tick, clock_id=clock):
            challenge = Challenge(npc_id=npc, stance=stance, trigger=trigger, location_id=where, tick=tick, clock_id=clock)
            return replace(state, challenges=(*(c for c in state.challenges if c.npc_id != npc), challenge))
        case ChallengeEnded(npc_id=npc):
            return replace(state, challenges=tuple(c for c in state.challenges if c.npc_id != npc))
        case FrontAdvanced(front_id=front, kind=kind, name=name, actors=actors, rivals=rivals, stop=stop, location_id=where,
                           tick=tick):
            advanced_to = FrontProgress(front_id=front, kind=kind, name=name, actors=actors, rivals=rivals, stop=stop,
                                        location_id=where, since=tick)
            return replace(state, fronts={**state.fronts, front: advanced_to})
        case FrontEnded(front_id=front, how=how):
            if (current := state.fronts.get(front)) is None:
                return state
            return replace(state, fronts={**state.fronts, front: current.model_copy(update={"ended": how})})
        case CollateralStruck(source_id=source, kind=kind, location_id=where, tick=tick, clock_id=clock):
            caught = Collateral(source_id=source, kind=kind, location_id=where, tick=tick, clock_id=clock)
            return replace(state, collateral=(*(c for c in state.collateral if c.source_id != source), caught))
        case CollateralEnded(source_id=source):
            return replace(state, collateral=tuple(c for c in state.collateral if c.source_id != source))
        case KarmaThreadOpened(thread=thread):
            return replace(state, karma=(thread, *(t for t in state.karma if t.id != thread.id))[:KARMA_MAX])
        case KarmaThreadResolved(thread_id=done):
            return replace(state, karma=tuple(t for t in state.karma if t.id != done))
        case ChapterOpened(chapter=chapter):
            return replace(state, chapter=chapter)
        case SkillExecuted() | Conversed() | ActionFailed() | Parleyed() | Maneuvered():
            return state  # 只是历史（线索、焦点、手段、尝试次数已在上面折叠），不改变世界
    raise TypeError(f"未知的领域事件：{type(event).__name__}")


def _echoed(karma: tuple[KarmaThread, ...], thread_id: str | None, npc: str) -> tuple[KarmaThread, ...]:
    """一条议程出于某条因果线：那条线从未决转为回响，记下领了它的人（不重复）。"""
    if thread_id is None:
        return karma
    return tuple(
        t.model_copy(update={"status": KarmaStatus.ECHOED, "echoes": tuple(dict.fromkeys((*t.echoes, npc)))})
        if t.id == thread_id else t
        for t in karma
    )


# ============================================================
#  聚合根
# ============================================================
class Player:
    def __init__(self, player_id: str) -> None:
        self.id = player_id
        self.version = 0
        self._state: PlayerState | None = None

    @property
    def spawned(self) -> bool:
        return self._state is not None

    @property
    def state(self) -> PlayerState:
        if self._state is None:
            raise UnknownPlayerError(f"江湖中查无此人：{self.id}")
        return self._state

    def apply(self, event: DomainEvent) -> None:
        """吸收一条已发生的事实。重放与新事件走同一条路：聚合根没有第二种改变自己的方式。"""
        self._state = evolve(self._state, event)
        self.version += 1

    @classmethod
    def from_history(cls, player_id: str, history: Iterable[EventEnvelope]) -> "Player":
        player = cls(player_id)
        for envelope in history:
            if envelope.version != player.version + 1:
                raise ValueError(f"事件流断裂：期望版本 {player.version + 1}，得到 {envelope.version}")
            player.apply(envelope.event)
        return player

    @staticmethod
    def replay(events: Iterable[DomainEvent]) -> PlayerState | None:
        """不经聚合根、直接折叠一串事件：演示"状态 = reduce(evolve, 历史)"这一等式本身。"""
        return reduce(evolve, events, None)

    @staticmethod
    def spawn(player_id: str, name: str, location_id: str, aptitude: float | None = None) -> list[DomainEvent]:
        """投胎即定下悟性：缺省由 id 确定性抽出（根骨天定），写进事件后便与日后的抽法无关。"""
        gift = aptitude_for(player_id) if aptitude is None else aptitude
        return [PlayerSpawned(player_id=player_id, name=name, location_id=location_id, aptitude=gift)]

    def ensure_alive(self) -> PlayerState:
        """永久死亡：死者的事件流只读。命令在解析之前就该撞上这道门，免得为死人白白调用一次大模型。"""
        state = self.state
        if not state.alive:
            raise PlayerDeadError(f"{state.name}已经死了：{state.death_cause}")
        return state

    def mastery(self, skill_id: str) -> Mastery | None:
        """武学等级：本流里该武学全部 SkillPracticed 的熟练度之和（evolve 已逐条 reduce），经悟性系数折算。"""
        return self.state.mastery(skill_id)

    def decide(
        self,
        intent: PlayerIntent,
        snapshot: LocalSnapshot,
        proposal: ResolutionOutput | Proposal | CombatProposal | None = None,
    ) -> list[DomainEvent]:
        """
        命令侧入口：守住生死与身份两道门，其余交给纯函数裁决。proposal 是地下城主的推演（ResolutionOutput）或规则 / 气运的结局（Proposal），
        领域经语义物理闸门定案后才落为事件。返回尚未入账的事件，由调用方追加到事件流。
        """
        return rules.decide(intent, self.ensure_alive(), snapshot, proposal)
