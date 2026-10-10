"""
[INPUT]: 依赖 pydantic v2 的 BaseModel / TypeAdapter / Field(discriminator)，依赖 domain/models 的 Attitude / Remedy，依赖 domain/clocks 的 NarrativeClock，
         依赖 domain/intent 的 ActionType / Approach / Aim，依赖 domain/combat 的 CombatOutcome，依赖 domain/outcomes 的 SocialOutcome / CovertOutcome
[OUTPUT]: 对外提供 不可变领域事件 DomainEvent 基类及 PlayerSpawned / Moved（fleeing 标明夺路而逃）/ ItemTransferred / SkillPracticed /
          SkillExecuted（approach 手段）/ HealthChanged（source：blow 伤人 / rest 调息 / item 服药）/ Conversed（topic_id 话题）/
          RelationChanged（basis 关系称谓或缘由类别）/ ActionFailed（unlock 怎样才行、aim / approach 当时的所图与手段、
          target_id / subject_id 落了地的对象与标的）/ PlayerDied / Parleyed 交涉（subject_id 所图的标的）/ FactLearned 得知见闻 /
          ItemConsumed 用掉随身之物 / Maneuvered 暗中取物、
          语义物理引擎的六种事件 ClockStarted / ClockAdvanced（负为回退）/ ClockCollapsed（满格坍缩）/ ClockCleared（销毁）/
          FactEmerged（推演出的微观事实）/ RenownChanged（名望涨落）、
          世界心跳的七种事件 TimePassed（时间走了几刻）/ ActivityStarted / TraceLeft / FactTokenSpawned / RumorSpread（消息又传到几处）/
          ItemDecayed（露天无主之物朽坏）/ ItemPilfered（遗落之物被人顺手拿走），Moved.motivation 此行所为、
          空间认知与分层 NPC 生态的八种事件 PlacesLearned（问路得知）/ AgendaPlanned / AgendaIssued / AgendaConcluded（宏观议程）/
          NpcMoved（微观行军，witnessed 玩家看见的一面）/ EncounterBegan / EncounterResolved（相撞中断与裁决）/ NpcWounded（战力洗牌）、
          世界本份的九种事件 TraitAcquired（后来添上的特质：刀疤）/ ItemShown（亮出藏着的东西）/ PlayerNoticed（一位 NPC 看见了你这副模样）/
          ThreatDeclared（被你触犯的 NPC 起了盘问、喝止或敌意）/ ChallengeEnded（对峙了结）/ FrontAdvanced / FrontEnded（局势推进与了结）/
          CollateralStruck / CollateralEnded（被卷进局势与脱身）、编剧代理的三种事件 KarmaThreadOpened / KarmaThreadResolved（因果线立下与了结）/
          ChapterOpened（命运弧光开新章）、PlayerSpawned.traits 命格（旧账缺省由 id 现算）、ItemTransferred.concealed 到手即藏（旧账缺省视作外露）、
          AnyEvent 判别联合、EVENT_ADAPTER（JSONB 编码）、
          decode_event()（JSONB 解码：先经上抛器把旧账升级为现行词汇）、EventEnvelope（流内版本 + 事件 id + 记录时间）
[POS]: domain 的事实词汇：世界此刻的一切都由这些事件经纯函数折叠而来；事件一经写入永不修改，
       新增事件类型只需在此加一个类并挂进 AnyEvent（开闭），时间戳只在信封上，事件本体保持确定性以便裁决可单测。
       词汇演进靠上抛（upcast）而不是改历史：废弃的 SkillLearned（"获得即学会"）在读出时折算为一次 SkillPracticed，
       旧的「受挫」折算为「轻伤」，旧账里 cause=="调息疗伤" 而没有 source 的气血回升读作 source="rest"——账本里的字节永远是当年写下的样子。
       一切新字段都有缺省值，旧账照读；唯一需要上抛的是 HealthChanged.source。
       地下城主推演出的时钟、微观事实与名望涨落，经领域闸门（resolution.py）定案后才写成这里的事件——大模型从不直接落账。
       世界心跳的事件由 application/world_clock 按 domain/heartbeat 的纯函数算出：时间、余波与生态同样是入账的事实，不是读取时的幻象；
       痕迹的消散与活动的了结是时间的纯函数，不另写事件。
       世界本份的事件由 domain/friction 与 domain/fronts 的纯函数按图谱快照算出（感知、施压、局势推进、波及），不调大模型；
       编剧代理的事件只记伏笔与潜台词，不改变任何物理事实——它们经 domain/screenplay 的闸门才入账
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import json
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from app.domain.agenda import AgendaEnd, Encounter, EncounterKind, NpcAgenda, SkirmishOutcome
from app.domain.ambient import Activity, EnvironmentalTrace, FactToken
from app.domain.arc import Chapter
from app.domain.clocks import NarrativeClock
from app.domain.combat import CombatOutcome
from app.domain.commands import MAX_TIME_COST
from app.domain.intent import ActionType, Aim, Approach
from app.domain.karma import KarmaThread
from app.domain.lore import Stance, Trigger
from app.domain.models import Attitude, Material, Remedy
from app.domain.outcomes import CovertOutcome, SocialOutcome
from app.domain.signature import KIND_OF, trait_errors
from app.domain.stage import ChallengeEnd, FrontEnd, FrontKind


class DomainEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PlayerSpawned(DomainEvent):
    type: Literal["PlayerSpawned"] = "PlayerSpawned"
    player_id: str
    name: str
    location_id: str
    aptitude: float = Field(default=1.0, ge=0.5, le=1.5)  # 悟性系数：根骨天定，折算一切修习所得
    traits: tuple[str, ...] | None = None  # 命格（相貌 / 口音 / 装束 / 印记）：旧账缺省为 None，折叠时由 id 现算（signature.fated）

    @field_validator("traits")
    @classmethod
    def _fated(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if value is not None and (errors := trait_errors(value)):
            raise ValueError("；".join(errors))
        return value


class Moved(DomainEvent):
    type: Literal["Moved"] = "Moved"
    from_location_id: str
    to_location_id: str
    exit_label: str
    fleeing: bool = False  # 重伤后夺路而逃（而非自行离去）：出发地从此是逃离过的险地；旧账缺省即自行离去
    motivation: str = Field(default="", max_length=24)  # 此行所为：跨进新地方时与眼前所见对照（短期记忆）；旧账缺省为空


class ItemTransferred(DomainEvent):
    """物品易手。持有者 id 自带种类前缀（loc: / chr: / ply:），物品在任一时刻只有一个持有者。"""

    type: Literal["ItemTransferred"] = "ItemTransferred"
    item_id: str
    from_holder: str
    to_holder: str
    concealed: bool | None = None  # 到了玩家手上即藏起（暗中得手、或本就藏得住的小物件）；None 为旧账或与玩家无涉，视作外露


class SkillPracticed(DomainEvent):
    """
    修习一次：入门与精进是同一种事实。武学的火候不是一个布尔值，而是历次修习所得熟练度之和（reduce）经悟性折算的结果；
    获得秘籍（ItemTransferred 入行囊）只是多了一件东西，一分熟练度也不给。
    """

    type: Literal["SkillPracticed"] = "SkillPracticed"
    skill_id: str
    proficiency_gained: int = Field(ge=1)
    source_id: str | None = None  # 传功点拨之人（chr:）、所凭典籍（itm:），闭门苦练为 None


class SkillExecuted(DomainEvent):
    """出手。skill_id 为空即徒手；item_id 只是叙事素材——兵器不改境界，世上没有天降神兵。"""

    type: Literal["SkillExecuted"] = "SkillExecuted"
    skill_id: str | None
    target_id: str
    item_id: str | None = None
    outcome: CombatOutcome
    approach: Approach = Approach.PLAIN  # 武力 / 计谋……旧账缺省为寻常


class HealthChanged(DomainEvent):
    """
    气血涨落：负为受伤、正为复原。折叠时钳在 [0, MAX_HP]；归零不等于死——死亡永远是一条明写的 PlayerDied。
    source 说的是"哪一类涨落"：blow 交手或险物所伤、rest 调息、item 服药敷药；source_id 是伤人者（chr:）或所用之物（itm:）。
    """

    type: Literal["HealthChanged"] = "HealthChanged"
    delta: int
    cause: str
    source_id: str | None = None  # 伤人者（chr:）或所用之物；自行调息为 None
    source: Literal["blow", "rest", "item"] = "blow"


class Conversed(DomainEvent):
    type: Literal["Conversed"] = "Conversed"
    npc_id: str
    topic_id: str | None = None  # 落了地的话题（实体或见闻 id）


class RelationChanged(DomainEvent):
    type: Literal["RelationChanged"] = "RelationChanged"
    character_id: str
    attitude: Attitude
    cause: str
    basis: str = ""  # 关系称谓（「师徒」）或缘由类别（「物归原主」）：供人情阶梯的例外与恩怨的写法辨认


class ActionFailed(DomainEvent):
    """失败同样是历史：被规则驳回的意图入账，叙事与记忆都据此知道"你试过、没成"。"""

    type: Literal["ActionFailed"] = "ActionFailed"
    action: ActionType
    target: str | None
    reason_code: str
    reason: str
    unlock: str = ""  # 怎样才行（「信赖」「略有小成」……）：供心事线索与选项提示
    aim: Aim | None = None
    approach: Approach = Approach.PLAIN
    target_id: str | None = None  # 落了地的对象（不肯传功的师父、物在其手的人）：心事线索按 id 立键；旧账没有它，不开线索
    subject_id: str | None = None  # 落了地的标的（所求的武学、所取之物）


class PlayerDied(DomainEvent):
    type: Literal["PlayerDied"] = "PlayerDied"
    cause: str
    killer_id: str | None = None


class Parleyed(DomainEvent):
    """
    交涉一场：对谁、图什么、凭什么手段、结局如何。leverage_ids 是领域从已知见闻里按 unlock 边确定性选出的筹码，大模型不提议；
    subject_id 是所图的标的（求艺之武学 art:、讨要之物 itm:、打探之见闻 fact:），没有标的（结交、化解、无可打探）为 None。
    """

    type: Literal["Parleyed"] = "Parleyed"
    npc_id: str
    aim: Aim
    approach: Approach
    outcome: SocialOutcome
    leverage_ids: tuple[str, ...] = ()
    subject_id: str | None = None


class FactLearned(DomainEvent):
    """得知一件见闻（fact:）：从谁那里听来（chr:）。"""

    type: Literal["FactLearned"] = "FactLearned"
    fact_id: str
    source_id: str


class ItemConsumed(DomainEvent):
    """用掉一件随身之物（服药、敷药）：它从此不在任何地方。气血的回升另由 HealthChanged(source="item") 明写。"""

    type: Literal["ItemConsumed"] = "ItemConsumed"
    item_id: str
    effect: Remedy


class Maneuvered(DomainEvent):
    """暗中取物（计谋 / 潜行）：向谁下手、得手与否、察觉与否。易手另由 ItemTransferred 明写。"""

    type: Literal["Maneuvered"] = "Maneuvered"
    item_id: str
    target_id: str
    approach: Approach
    outcome: CovertOutcome


# ============================================================
#  语义物理引擎 —— 叙事时钟、微观事实、名望：推演经领域闸门定案后的落账形态
# ============================================================
class ClockStarted(DomainEvent):
    """一只时钟挂上某个实体：名称、种类、阈值与满则如何都在 clock 里，初始进度即 clock.progress。"""

    type: Literal["ClockStarted"] = "ClockStarted"
    clock: NarrativeClock
    cause: str = ""


class ClockAdvanced(DomainEvent):
    """推进（steps > 0）或回退（steps < 0）。满格不经此事件，而是一条 ClockCollapsed。name / progress / maximum 让白描不必回查时钟表。"""

    type: Literal["ClockAdvanced"] = "ClockAdvanced"
    clock_id: str
    steps: int = Field(ge=-8, le=8)
    name: str = ""
    progress: int = 0  # 推进 / 回退之后的进度
    maximum: int = 0
    cause: str = ""


class ClockCollapsed(DomainEvent):
    """时钟满格：暗流坍缩为硬结算。它的后果另由同一批里的明写事件落账（翻脸、受创、脱身……），这条只宣告它退场。"""

    type: Literal["ClockCollapsed"] = "ClockCollapsed"
    clock_id: str
    name: str
    consequence: str = ""


class ClockCleared(DomainEvent):
    """时钟化解或作罢：不再悬着，也不再有后果。"""

    type: Literal["ClockCleared"] = "ClockCleared"
    clock_id: str
    name: str = ""
    cause: str = ""


class FactEmerged(DomainEvent):
    """
    推演出的一条微观事实（≤40 字的一行中文）：不改任何属性、归属、生死与位置，只是此世此刻确实发生了的细节。
    fact_id = emg:<正文摘要>，subject_ids 是正文里点了名的场景实体，图谱据此把它挂在人、物、地上，下回合在场即召回。
    """

    type: Literal["FactEmerged"] = "FactEmerged"
    fact_id: str
    text: str = Field(min_length=1, max_length=40)
    subject_ids: tuple[str, ...] = ()


class RenownChanged(DomainEvent):
    """名望涨落：江湖上怎么说你。折叠时钳在 [−100, 100]，对外只露语义标签（progression.Renown）。"""

    type: Literal["RenownChanged"] = "RenownChanged"
    delta: int = Field(ge=-20, le=20)
    cause: str


# ============================================================
#  世界心跳 —— 时间流逝与它带来的余波：活动、痕迹、消息、日常生态
# ============================================================
class TimePassed(DomainEvent):
    """
    时间走了 ticks 刻（一条命令的 time_cost）。折叠时 tick 累加，到期的痕迹消散、已结束且没了痕迹的活动一同抹去——
    消散不另写事件：它是时间的纯函数，两套图谱各按同一条规则剪枝。
    """

    type: Literal["TimePassed"] = "TimePassed"
    ticks: int = Field(ge=1, le=MAX_TIME_COST)


class ActivityStarted(DomainEvent):
    """此地开始了一件事（交手、人群溃散）：同 id 再起即覆盖（人群再受惊，溃散的时辰往后延）。"""

    type: Literal["ActivityStarted"] = "ActivityStarted"
    activity: Activity


class TraceLeft(DomainEvent):
    """此地留下一道痕迹：几刻之后自行消散。"""

    type: Literal["TraceLeft"] = "TraceLeft"
    trace: EnvironmentalTrace


class FactTokenSpawned(DomainEvent):
    """一件公开发生的事成了一枚消息：此刻只有发源地知道，此后随时间沿路传开（RumorSpread）。"""

    type: Literal["FactTokenSpawned"] = "FactTokenSpawned"
    token: FactToken


class RumorSpread(DomainEvent):
    """消息又传到了几处（按先后）：那里的人从此知道这件事。"""

    type: Literal["RumorSpread"] = "RumorSpread"
    token_id: str
    location_ids: tuple[str, ...] = Field(min_length=1)


class ItemDecayed(DomainEvent):
    """露天的无主之物按物料朽坏：它从此不在任何地方（与用掉之物一样折进 consumed）。"""

    type: Literal["ItemDecayed"] = "ItemDecayed"
    item_id: str
    material: Material


class ItemPilfered(DomainEvent):
    """无主或遗落在地的东西被此地的人顺手拿走：持有者的一次易手，与玩家无涉（不进焦点、不记来路、不了结心事）。"""

    type: Literal["ItemPilfered"] = "ItemPilfered"
    item_id: str
    from_holder: str
    to_holder: str


# ============================================================
#  空间认知与分层 NPC 生态 —— 问路得知、宏观议程、微观行军、相撞中断与裁决、战力洗牌
# ============================================================
class PlacesLearned(DomainEvent):
    """问路得知了几处地方：此后它们作为去处时不再是「未知区域」。"""

    type: Literal["PlacesLearned"] = "PlacesLearned"
    location_ids: tuple[str, ...] = Field(min_length=1)
    source_id: str | None = None  # 指路的人（chr:）


class AgendaPlanned(DomainEvent):
    """一轮宏观议程的规划（初临江湖、新的一日、江湖震动）：不论大模型给没给出议程都入账，下一轮按它算冷却。"""

    type: Literal["AgendaPlanned"] = "AgendaPlanned"
    tick: int = Field(ge=0)
    cause: str = ""


class AgendaIssued(DomainEvent):
    """领域闸门放行的一条议程：同一位 NPC 的新议程覆盖旧议程，启程之刻即 agenda.issued_tick。"""

    type: Literal["AgendaIssued"] = "AgendaIssued"
    agenda: NpcAgenda


class AgendaConcluded(DomainEvent):
    type: Literal["AgendaConcluded"] = "AgendaConcluded"
    npc_id: str
    how: AgendaEnd
    tick: int = Field(ge=0)


class NpcMoved(DomainEvent):
    """NPC 沿路走了一跳，tick 是抵达之刻。witnessed 是玩家看见的那一面：他来到你所在之处、或从你身边离开；别处的行军不出声。"""

    type: Literal["NpcMoved"] = "NpcMoved"
    npc_id: str
    from_location_id: str
    to_location_id: str
    tick: int = Field(ge=0)
    witnessed: Literal["", "来到", "离开"] = ""


class EncounterBegan(DomainEvent):
    """行军被相撞打断：一次待裁决的中断。"""

    type: Literal["EncounterBegan"] = "EncounterBegan"
    encounter: Encounter


class EncounterResolved(DomainEvent):
    """
    中断坍缩成结局：撞见的结局是闸门放行的时钟 / 事实 / 名望（同批另行入账），狭路相逢的结局是 SkirmishOutcome；
    npc_ids 里的 NPC 驻足到 resume_tick 才再上路。witnessed：玩家就在当场。
    """

    type: Literal["EncounterResolved"] = "EncounterResolved"
    encounter_id: str
    kind: EncounterKind
    location_id: str
    npc_ids: tuple[str, ...] = ()
    outcome: SkirmishOutcome | None = None
    by: Literal["规则", "地下城主"] = "规则"
    resume_tick: int = Field(ge=0)
    witnessed: bool = False


class NpcWounded(DomainEvent):
    """NPC 受了伤：到 until_tick 之前交手时战力打一档折扣，也不再赶路（战力洗牌）。"""

    type: Literal["NpcWounded"] = "NpcWounded"
    npc_id: str
    until_tick: int = Field(ge=0)
    cause: str = ""


# ============================================================
#  世界本份 —— 命格与外显、被看见、被触犯、对峙了结、局势推进与波及
# ============================================================
class TraitAcquired(DomainEvent):
    """命格之外后来添上的特质（重伤于刀兵之下 → 面有刀疤）：此后旁人眼里的你就是这副模样。"""

    type: Literal["TraitAcquired"] = "TraitAcquired"
    trait: str
    cause: str = Field(default="", max_length=24)

    @field_validator("trait")
    @classmethod
    def _known(cls, value: str) -> str:
        if value not in KIND_OF:
            raise ValueError(f"不认识的特质：{value}")
        return value


class ItemShown(DomainEvent):
    """你把藏在身上的东西亮给人看（出示信物）：它从此外露，在场之人都看得见。to_id 是出示的对象（chr:）。"""

    type: Literal["ItemShown"] = "ItemShown"
    item_id: str
    to_id: str | None = None


class PlayerNoticed(DomainEvent):
    """一位 NPC 看见了你：这副模样（外露的特质与物件，digest 是签名摘要）第一次落进他眼里，此后他凭它认人。"""

    type: Literal["PlayerNoticed"] = "PlayerNoticed"
    npc_id: str
    location_id: str
    tick: int = Field(ge=0)
    traits: tuple[str, ...] = ()
    items: tuple[str, ...] = ()
    digest: str = ""


class ThreatDeclared(DomainEvent):
    """
    世界本份：一位 NPC 被你触犯（trigger），对你起了盘问、喝止或敌意（stance）。basis 是缘由的白描（人设原文、物名、局势名），
    item_id 是他认出的那件东西，clock_id 是随之挂上或推进的那只时钟（挂在他身上；满则按坍缩表结算）。
    """

    type: Literal["ThreatDeclared"] = "ThreatDeclared"
    npc_id: str
    stance: Stance
    trigger: Trigger
    basis: str = Field(default="", max_length=24)
    location_id: str
    tick: int = Field(ge=0)
    item_id: str | None = None
    clock_id: str | None = None


class ChallengeEnded(DomainEvent):
    """对峙了结：你应对了他、你走开了、置之不理而时钟满了、或他走开了。"""

    type: Literal["ChallengeEnded"] = "ChallengeEnded"
    npc_id: str
    how: ChallengeEnd
    tick: int = Field(ge=0)


class FrontAdvanced(DomainEvent):
    """
    一股局势推进到路线上的第 stop 站（自带种类、名字与一伙人，白描与折叠都不回查正典）。
    同伴的挪步另有 NpcMoved（witnessed 留空），玩家看见的那一面只在这里：一伙人来到你所在之处、或从你身边离开。
    """

    type: Literal["FrontAdvanced"] = "FrontAdvanced"
    front_id: str
    kind: FrontKind
    name: str
    actors: tuple[str, ...] = Field(min_length=1)
    rivals: tuple[str, ...] = ()
    stop: int = Field(ge=0)
    location_id: str
    tick: int = Field(ge=0)
    witnessed: Literal["", "来到", "离开"] = ""


class FrontEnded(DomainEvent):
    type: Literal["FrontEnded"] = "FrontEnded"
    front_id: str
    how: FrontEnd
    tick: int = Field(ge=0)


class CollateralStruck(DomainEvent):
    """局势推进到你所在之处，你被卷了进去（被波及者）：clock_id 是随之挂上的那只时钟。"""

    type: Literal["CollateralStruck"] = "CollateralStruck"
    source_id: str
    kind: FrontKind
    location_id: str
    tick: int = Field(ge=0)
    clock_id: str | None = None


class CollateralEnded(DomainEvent):
    type: Literal["CollateralEnded"] = "CollateralEnded"
    source_id: str
    how: ChallengeEnd
    tick: int = Field(ge=0)


# ============================================================
#  编剧代理 —— 因果线（伏笔与回收）与命运弧光（章回主题）；不改变任何物理事实
# ============================================================
class KarmaThreadOpened(DomainEvent):
    """一条因果线挂上了墙：by 标明出自编剧大模型的评估，还是规则的确定性退路。"""

    type: Literal["KarmaThreadOpened"] = "KarmaThreadOpened"
    thread: KarmaThread
    by: Literal["规则", "编剧"] = "规则"


class KarmaThreadResolved(DomainEvent):
    """一条因果线了结：狭路重逢、恩怨化解、物归原主、人死仇消……"""

    type: Literal["KarmaThreadResolved"] = "KarmaThreadResolved"
    thread_id: str
    how: str = Field(min_length=1, max_length=12)
    tick: int = Field(ge=0)


class ChapterOpened(DomainEvent):
    """命运弧光转入新阶段，开新的一章：主题、基调、意象与潜台词从此约束说书人的笔触。"""

    type: Literal["ChapterOpened"] = "ChapterOpened"
    chapter: Chapter
    by: Literal["规则", "编剧"] = "规则"


AnyEvent = Annotated[
    PlayerSpawned
    | Moved
    | ItemTransferred
    | SkillPracticed
    | SkillExecuted
    | HealthChanged
    | Conversed
    | RelationChanged
    | ActionFailed
    | PlayerDied
    | Parleyed
    | FactLearned
    | ItemConsumed
    | Maneuvered
    | ClockStarted
    | ClockAdvanced
    | ClockCollapsed
    | ClockCleared
    | FactEmerged
    | RenownChanged
    | TimePassed
    | ActivityStarted
    | TraceLeft
    | FactTokenSpawned
    | RumorSpread
    | ItemDecayed
    | ItemPilfered
    | PlacesLearned
    | AgendaPlanned
    | AgendaIssued
    | AgendaConcluded
    | NpcMoved
    | EncounterBegan
    | EncounterResolved
    | NpcWounded
    | TraitAcquired
    | ItemShown
    | PlayerNoticed
    | ThreatDeclared
    | ChallengeEnded
    | FrontAdvanced
    | FrontEnded
    | CollateralStruck
    | CollateralEnded
    | KarmaThreadOpened
    | KarmaThreadResolved
    | ChapterOpened,
    Field(discriminator="type"),
]

EVENT_ADAPTER: TypeAdapter[AnyEvent] = TypeAdapter(AnyEvent)


# ============================================================
#  上抛器 —— 旧词汇 → 现行词汇。只在读出时生效，账本本身一字不改
# ============================================================
LEGACY_MASTERY_POINTS = 45  # 旧账的"学会"按融会贯通（悟性 1.0）的熟练度折算：当年学会了，今天就不该变成初窥门径


def _skill_learned(raw: dict[str, Any]) -> dict[str, Any]:
    return {"type": "SkillPracticed", "skill_id": raw["skill_id"], "proficiency_gained": LEGACY_MASTERY_POINTS,
            "source_id": raw.get("source_id")}


def _skill_executed(raw: dict[str, Any]) -> dict[str, Any]:
    return {**raw, "outcome": "轻伤"} if raw.get("outcome") == "受挫" else raw


def _health_changed(raw: dict[str, Any]) -> dict[str, Any]:
    """source 之前的旧账：调息写的是 cause="调息疗伤"、没有来源——读作 rest，其余一律是 blow（缺省）。"""
    return {**raw, "source": "rest"} if "source" not in raw and raw.get("cause") == "调息疗伤" else raw


UPCASTERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "SkillLearned": _skill_learned,
    "SkillExecuted": _skill_executed,
    "HealthChanged": _health_changed,
}


def decode_event(payload: str | bytes | dict[str, Any]) -> AnyEvent:
    """账本 → 事件：先上抛、再校验。两种事件账本实现都只经这一个入口读出事件。"""
    raw = payload if isinstance(payload, dict) else json.loads(payload)
    upcast = UPCASTERS.get(raw.get("type", ""))
    return EVENT_ADAPTER.validate_python(upcast(raw) if upcast else raw)


class EventEnvelope(BaseModel):
    """事件在流中的位置。version 从 1 起严格连续；乐观并发、投影检查点、记忆主键都以它为准。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stream_id: str
    version: int = Field(ge=1)
    event_id: UUID
    recorded_at: datetime
    event: AnyEvent
