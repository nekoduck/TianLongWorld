"""
[INPUT]: 依赖 pydantic v2 的 BaseModel / Field，依赖 domain/lore 的 Source / Stance / Trigger；WorldBlueprint 只在类型标注里，
         geography.ways 与 models 的 CharacterStatus 在闸门函数体内调用时才取（models 运行期导入本模块，反向只能在调用时，不成环）
[OUTPUT]: 对外提供 世界舞台的名词——
          局势：FrontKind（清场 / 对决）、FrontEnd（了结 / 受阻）、Front（正典里的一股推进中的局势：为首者在前的一伙人、对决的另一方、
          依次经过之处、几时起、每站停留几刻、缘由与出处，子代理撰写、经 friction 闸门入库）、FRONTS_MAX、stage_errors(bp)（局势的蓝图闸门）、
          FrontProgress（此世此刻局势推进到哪一站，.active / .leader）；
          对峙：ChallengeEnd（应对 / 离去 / 坍缩 / 平息）、Challenge（悬在你身上的一次盘问、喝止或敌意：谁、为何、在哪、何时、挂了哪只时钟）、
          Collateral（你被卷进的局势：哪股局势、种类、在哪、何时、挂了哪只时钟）、Sighting（一位 NPC 最近一次看见你的样子：在哪、何时、特质、物件、签名摘要）、
          Knowledge（一位 NPC 所知的你：见过与听说的特质、见过与丢在你手里的物件——议程目标签名的来源）
[POS]: domain 的「世界本份」名词（只有名词；推进、感知、施压在 domain/fronts 与 domain/friction，以免与 events 成环）：
       局势是正典里本就在推进的事（帮派清场、高手对决），不是为玩家刷出来的怪——为首者与同伙都是 T=0 在场的原著人物，路线是图谱上走得通的路，
       推进到玩家所在之处时不再绕开他：清场的人喝令他离开，对决的刀剑不长眼。对峙与波及都折叠进 PlayerState，坍缩走时钟的坍缩表
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from app.domain.lore import Source, Stance, Trigger

if TYPE_CHECKING:
    from app.domain.models import WorldBlueprint

FRONTS_MAX = 8
FRONT_NAME_CHARS = 12
FRONT_BASIS_CHARS = 40
FRONT_START_MAX = 8 * 96  # 局势至多在投胎后七日内发动（一刻十五分钟、一日九十六刻）
DWELL_MAX = 96  # 每站至多停留一日


class _Stage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class FrontKind(StrEnum):
    SWEEP = "清场"  # 一伙人依次走过几处地方，喝令闲杂人等离开
    DUEL = "对决"  # 两方在一处约斗，刀剑无眼


class FrontEnd(StrEnum):
    DONE = "了结"  # 走完了路线 / 斗完了
    BROKEN = "受阻"  # 为首者（或对决的任一方）倒下、被制住、带伤


class Front(_Stage):
    """
    正典里一股推进中的局势。actors 为首者居首；清场的 path 是依次经过之处，首站须是这伙人 T=0 同在之处、相邻两站须有路（不走坠落）；
    对决只有一处（双方 T=0 都在那里），在 [start_tick, start_tick + dwell) 之间相持，到点由狭路相逢的裁决定胜负。
    """

    id: str = Field(pattern=r"^front:\S+$")
    name: str = Field(min_length=2, max_length=FRONT_NAME_CHARS)
    kind: FrontKind
    actors: tuple[str, ...] = Field(min_length=1, max_length=6)
    rivals: tuple[str, ...] = Field(default=(), max_length=6)
    path: tuple[str, ...] = Field(min_length=1, max_length=12)
    start_tick: int = Field(ge=0, le=FRONT_START_MAX)
    dwell: int = Field(ge=1, le=DWELL_MAX)
    basis: str = Field(min_length=1, max_length=FRONT_BASIS_CHARS)
    sources: tuple[Source, ...] = Field(min_length=1)


class FrontProgress(_Stage):
    """此世此刻一股局势推进到了哪里（FrontAdvanced / FrontEnded 折叠）。stop 是路线上的第几站，since 是到达这一站之刻。"""

    front_id: str
    kind: FrontKind
    name: str
    actors: tuple[str, ...]
    rivals: tuple[str, ...] = ()
    stop: int = Field(ge=0)
    location_id: str
    since: int = Field(ge=0)
    ended: FrontEnd | None = None

    @property
    def active(self) -> bool:
        return self.ended is None

    @property
    def leader(self) -> str:
        return self.actors[0]


class ChallengeEnd(StrEnum):
    ANSWERED = "应对"  # 你冲着他做了点什么（攀谈、交涉、赠物、出手……）
    LEFT = "离去"  # 你走开了（喝止的时钟随之消散，盘问与敌意留下）
    COLLAPSED = "坍缩"  # 置之不理，时钟满了
    FADED = "平息"  # 他走开了、局势推进走了


class Challenge(_Stage):
    """悬在你身上的一次对峙：一位 NPC 在此处、此刻对你起了盘问、喝止或敌意。你不理他，他身上的时钟一回合推一格。"""

    npc_id: str
    stance: Stance
    trigger: Trigger
    location_id: str
    tick: int = Field(ge=0)
    clock_id: str | None = None


class Collateral(_Stage):
    """你被卷进的一股局势：只要你还在那里、局势还在那里，它挂的时钟一回合推一格。"""

    source_id: str
    kind: FrontKind
    location_id: str
    tick: int = Field(ge=0)
    clock_id: str | None = None


@dataclass(frozen=True, slots=True)
class Knowledge:
    """一位 NPC 所知的那人（玩家）模样：他亲眼见过的、传到他所在之处的消息里描述的特质，见过的与丢在那人手里的物件——议程的目标签名只能取自这里。"""

    traits: frozenset[str] = frozenset()
    items: frozenset[str] = frozenset()


class Sighting(_Stage):
    """一位 NPC 最近一次看见你的样子：他认人只凭这些（与传到他那里的消息）。"""

    location_id: str
    tick: int = Field(ge=0)
    traits: tuple[str, ...] = ()
    items: tuple[str, ...] = ()
    digest: str = ""


# ============================================================
#  闸门 —— 人须是 T=0 在场的原著人物、路须走得通、对决双方须同在一处
# ============================================================
def stage_errors(bp: WorldBlueprint) -> list[str]:
    from app.domain.geography import TravelMethod, ways  # models 运行期导入本模块：反向只能在调用时取
    from app.domain.models import CharacterStatus

    chars = {c.id: c for c in bp.characters}
    places = {loc.id for loc in bp.locations}
    roads = {pair for pair, w in ways(bp).items() if w.travel_method is not TravelMethod.FALL}
    errors: list[str] = []
    if len(bp.fronts) > FRONTS_MAX:
        errors.append(f"局势过多：{len(bp.fronts)} > {FRONTS_MAX}")
    seen: set[str] = set()
    enlisted: dict[str, str] = {}
    for front in bp.fronts:
        where = f"局势 {front.id}"
        if front.id in seen:
            errors.append(f"重复的局势 id：{front.id}")
        seen.add(front.id)
        people = (*front.actors, *front.rivals)
        if len(set(people)) != len(people):
            errors.append(f"{where} 的人有重复")
        for who in people:
            c = chars.get(who)
            if c is None:
                errors.append(f"{where} 的人不在蓝图里：{who}")
            elif c.status is not CharacterStatus.ALIVE or c.arrives_with or c.location_id is None:
                errors.append(f"{where} 的 {who} T=0 不在场（已故、后来才到场或不在任何地方）")
            if who in enlisted and enlisted[who] != front.id:
                errors.append(f"{where} 的 {who} 已在局势 {enlisted[who]} 里")
            enlisted.setdefault(who, front.id)
        errors += [f"{where} 的地点不存在：{p}" for p in front.path if p not in places]
        if len(set(front.path)) != len(front.path):
            errors.append(f"{where} 的路线重复经过同一处")
        home = front.path[0]
        strays = [w for w in people if w in chars and chars[w].location_id != home]
        if strays:
            errors.append(f"{where} 的 {'、'.join(strays)} T=0 不在首站 {home}")
        match front.kind:
            case FrontKind.SWEEP:
                if front.rivals:
                    errors.append(f"{where} 是清场，不该有对手")
                errors += [f"{where} 的 {a} → {b} 没有走得通的路" for a, b in zip(front.path, front.path[1:], strict=False)
                           if (a, b) not in roads]
            case FrontKind.DUEL:
                if not front.rivals:
                    errors.append(f"{where} 是对决，须有对手")
                if len(front.path) != 1:
                    errors.append(f"{where} 是对决，只在一处")
    return errors
