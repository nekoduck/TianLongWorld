"""
[INPUT]: 依赖 application/ports 的 JsonSchema，依赖 briefs/combat 的 safe / join，依赖 briefs/scene 的 world_section / player_section，
         依赖 briefs/physics 的 physics_section / schema（撞见沿用地下城主结果已定之事的物理边界与输出契约），
         依赖 domain/npc 的 STAY / SkirmishStakes / wounded，依赖 domain/agenda 的 NpcAgenda / Encounter / SkirmishOutcome / AGENDA_CHARS，
         依赖 domain/heartbeat 的 Atlas / position，依赖 domain/commands 的 time_label / daylight / TICKS_PER_DAY，
         依赖 domain/models 的 Character / CharacterRelation / Era / RelationKind，依赖 domain/lore 的 Persona，
         依赖 domain/resolution 的 Envelope，依赖 domain/snapshot 的 LocalSnapshot；PlayerState 仅作类型标注
[OUTPUT]: 对外提供 Brief（一份简报：正文 text、结构化输出契约 schema、此景的名字 names——事实预筛先剔掉它们再查原著名录）、
          agenda_brief()（宏观议程：每位能领议程的核心 NPC 的本名称号 / 门派 / 境界 / 性情 / 此刻所在 / 执念与好恶 / 对玩家的态度 / 此地情报 / 开篇仇敌与羁绊 /
          眼下议程 / 可去之处与路程（近者在前、至多 DESTINATIONS_MAX 处）；契约 agendas 数组，npc 枚举核心 NPC 本名、target 枚举候选去处名 ∪「留守」）、
          encounter_brief()（撞见：<encounter> 来者、来意、对你的态度 + 地下城主的物理快照、玩家状态与结果已定的物理边界；来意与去处过 veil）、
          veil()（来意里玩家叫不出名的地名抹成「别处」：说书人的 errands 与撞见的判官共用）、
          skirmish_brief()（狭路相逢：来者与在此者、开篇仇怨、此地传闻、可裁结局的含义与确定性裁决；契约 reasoning → outcome 枚举可裁结局 → fact）、
          SKIRMISH_MEANING、RUMORS_MAX / DESTINATIONS_MAX（每人的消息与去处条数上限：载荷有界）、distance()（道路耗时 → 路程的语义）
[POS]: application/briefs 的 H-Agent 简报：分层 NPC 生态里大模型只在两处发言——宏观层立议程、裁决层坍缩相撞，这里把它们要看的绝对事实打包。
       局部认知：一位 NPC 知道的只有自己的人设、开篇的恩怨与传到他所在之处的消息（FactToken.reached），别处发生而消息未到之事一概不进；
       玩家身在何处不进议程简报——NPC 不为撞上玩家而出门（被动沙盒）。不露任何 id（chr: / loc: / tok: / enc:），每个插值逐值转义；
       后文剧情（Character.foreshadow）从不进任何简报
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import TYPE_CHECKING

from app.application.briefs.combat import join, safe
from app.application.briefs.physics import physics_section, schema
from app.application.briefs.scene import player_section, world_section
from app.application.ports import JsonSchema
from app.domain.agenda import AGENDA_CHARS, Encounter, NpcAgenda, SkirmishOutcome
from app.domain.commands import TICKS_PER_DAY, daylight, time_label
from app.domain.heartbeat import Atlas, position
from app.domain.lore import Persona
from app.domain.models import Character, CharacterRelation, Era, RelationKind
from app.domain.npc import STAY, SkirmishStakes, destinations, wounded
from app.domain.resolution import Envelope
from app.domain.snapshot import LocalSnapshot

if TYPE_CHECKING:
    from app.domain.aggregates import PlayerState

RUMORS_MAX = 4  # 每处至多写四条传到那里的消息：新者在前
DESTINATIONS_MAX = 24  # 每人至多列二十四处可去之处：路程近者在前（闸门认的是三跳以内的全部，简报只给最近的这些）
_WEIGHT = {1: "寻常", 2: "要紧", 3: "志在必得"}
VEIL = "别处"  # 玩家叫不出名的地方在来意里的写法
_NUM = "零一二三四五六七八九十"
_SHICHEN = 8  # 一个时辰八刻


@dataclass(frozen=True, slots=True)
class Brief:
    """一份交给大模型的简报：正文、结构化输出契约、此景出现过的名字（事实预筛先剔掉它们，免得「左子穆」里的「子穆」之类误伤）。"""

    text: str
    schema: JsonSchema
    names: tuple[str, ...] = ()


def _cn(n: int) -> str:
    """计数用的中文数字：两个时辰、三日（十以上照写阿拉伯数字——路程远到那一步，数字比字面更清楚）。"""
    return "两" if n == 2 else _NUM[n] if 0 <= n <= 10 else str(n)


def distance(ticks: int) -> str:
    """道路耗时 → 路程的语义：一个时辰内 / 约三个时辰 / 约半日 / 约两日。"""
    if ticks <= _SHICHEN:
        return "一个时辰内"
    if ticks < TICKS_PER_DAY // 2:
        return f"约{_cn(math.ceil(ticks / _SHICHEN))}个时辰"
    if ticks < TICKS_PER_DAY:
        return "约半日"
    return f"约{_cn(math.ceil(ticks / TICKS_PER_DAY))}日"


def veil(text: str, state: PlayerState, scene: LocalSnapshot, atlas: Atlas) -> str:
    """
    探索迷雾守到来意里：议程意图是大模型写的，常带目标地名（「去大理城寻辛双清」）——玩家叫不出名的地方（没到过、没问过路、
    不是眼前认得的去处、也不是名胜）一律抹成「别处」；处所的简称（「·」右侧两字以上）同样抹掉。名字长者先配，认得的名字原样留下（「大理城」不被「大理」吃掉一半）。
    """
    known = {*state.visited, *state.heard, *atlas.renowned, scene.location.id, *(e.to_id for e in scene.exits if e.known)}
    seen: set[str] = set()
    unseen: set[str] = set()
    for lid, name in atlas.names.items():
        if lid.startswith("loc:") and name:
            tail = name.rsplit("·", 1)[-1]
            (seen if lid in known else unseen).update((name, *([tail] if len(tail) >= 2 else [])))
    hidden = unseen - seen  # 同一个名字只要有一处认得就留
    if not hidden:
        return text
    pattern = re.compile("|".join(re.escape(n) for n in sorted(seen | hidden, key=len, reverse=True)))
    return pattern.sub(lambda m: VEIL if m.group(0) in hidden else m.group(0), text)


def _when(tick: int) -> str:
    return f"{time_label(tick)}｜{'白昼' if daylight(tick) else '黑夜'}"


def _titled(c: Character) -> str:
    return c.name + (f"（{'、'.join(c.titles)}）" if c.titles else "")


def _rumors(state: PlayerState, location_id: str | None) -> list[str]:
    """传到某处的消息正文（局部认知：发源地与沿路传到之处才知道），新者在前、至多 RUMORS_MAX 条。"""
    if location_id is None:
        return []
    heard = sorted((t for t in state.tokens if location_id in t.reached), key=lambda t: (-t.born_tick, t.id))
    return [t.text for t in heard[:RUMORS_MAX]]


def _route_ticks(atlas: Atlas, route: Sequence[str]) -> int:
    return sum(atlas.cost(a, b) for a, b in pairwise(route))


def _ties(c: Character, atlas: Atlas, relations: Sequence[CharacterRelation]) -> tuple[list[str], list[str]]:
    """开篇的仇敌与羁绊（后文才结下的不算）：「辛双清（东西宗相争）」「龚光杰（师徒）」。"""
    foes: list[str] = []
    bonds: list[str] = []
    for r in relations:
        if r.era is not Era.OPENING or c.id not in (r.source_id, r.target_id):
            continue
        other = atlas.names.get(r.target_id if r.source_id == c.id else r.source_id)
        if not other:
            continue
        if r.kind is RelationKind.ENEMY:
            foes.append(other + (f"（{r.note}）" if r.note else ""))
        else:
            bonds.append(f"{other}（{r.kind.value}）")
    return foes, bonds


# ============================================================
#  宏观层 —— 议程简报与契约
# ============================================================
def _npc_block(
    c: Character, state: PlayerState, atlas: Atlas, persona: Persona | None, relations: Sequence[CharacterRelation],
) -> tuple[list[str], list[str]]:
    """一位核心 NPC 的绝对事实与他可去之处的名字。"""
    here = position(state, atlas, c.id)
    place = atlas.names.get(here or "", "不知何处")
    home_name = atlas.names.get(c.location_id or "")
    home = "（居所）" if here == c.location_id else (f"（居所：{home_name}）" if home_name else "")
    regard = state.attitude_of(c.id).value + (f"（恩怨：{cause}）" if (cause := state.attitude_causes.get(c.id)) else "")
    foes, bonds = _ties(c, atlas, relations)
    agenda = state.agendas.get(c.id)
    doing = (
        f"前往{atlas.names.get(agenda.target_id, '某处')}——{agenda.intent}（{_WEIGHT[agenda.priority]}）" if agenda else "无"
    )
    reach: list[tuple[int, int, str]] = []
    for order, dest in enumerate(destinations(state, atlas, c.id)):
        route = atlas.path(here, dest) if here else ()
        if len(route) >= 2 and dest in atlas.names:
            reach.append((_route_ticks(atlas, route), order, dest))
    nearest = sorted(reach)[:DESTINATIONS_MAX]  # 近者在前：载荷有界，不随地图稠密而膨胀
    names = [atlas.names[dest] for _, _, dest in nearest]
    goes = [f"{atlas.names[dest]}（{distance(ticks)}）" for ticks, _, dest in nearest]
    rumors = _rumors(state, here)
    lines = [
        f"{_titled(c)}｜{c.faction or '无门无派'}｜境界{c.tier.value}｜性情{c.disposition.value}",
        f"此刻所在：{place}{home}",
        f"执念：{persona.worry if persona and persona.worry else '无'}",
        f"好：{join(persona.likes if persona else ())}｜恶：{join(persona.dislikes if persona else ())}",
        f"对{state.name}：{regard}",
        f"开篇仇敌：{join(foes)}｜羁绊：{join(bonds)}",
        f"眼下议程：{doing}",
        "此地情报：" + ("；".join(rumors) if rumors else "没有任何消息传到此地——别处的事，此人一无所知"),
        f"可去之处：{join(goes)}",
    ]
    return [safe(line) for line in lines], names


def agenda_brief(
    state: PlayerState, atlas: Atlas, npcs: Iterable[str], personas: Mapping[str, Persona],
    relations: Sequence[CharacterRelation], cause: str,
) -> Brief:
    """
    宏观议程的简报：缘由、时辰、玩家名（只为读懂消息里的人，他身在何处不写），每位能领议程的核心 NPC 一段 <npc>。
    契约：agendas 数组（至多每人一条），npc 枚举这几位的本名，target 枚举他们可去之处的名字 ∪「留守」，intent 字符串，priority 1~3。
    """
    blocks: list[str] = []
    who: list[str] = []
    places: set[str] = set()
    for cid in npcs:
        c = atlas.characters.get(cid)
        if c is None:
            continue
        lines, names = _npc_block(c, state, atlas, personas.get(cid), relations)
        blocks += ["<npc>", *lines, "</npc>"]
        who.append(c.name)
        places.update(names)
    text = "\n".join([
        f"<time>{safe(_when(state.tick))}</time>",
        f"<cause>{safe(cause)}</cause>",
        f"<player>{safe(state.name)}（玩家：消息里提到的这个人；他此刻身在何处，各人并不知道）</player>",
        *blocks,
    ])
    item = {
        "type": "object",
        "properties": {
            "npc": {"type": "string", "enum": who},
            "target": {"type": "string", "enum": [*sorted(places), STAY]},
            "intent": {"type": "string", "maxLength": AGENDA_CHARS},
            "priority": {"type": "integer", "minimum": 1, "maximum": 3},
        },
        "required": ["npc", "target", "intent", "priority"],
        "additionalProperties": False,
    }
    contract: JsonSchema = {
        "type": "object",
        "properties": {"agendas": {"type": "array", "maxItems": max(1, len(who)), "items": item}},
        "required": ["agendas"],
        "additionalProperties": False,
    }
    return Brief(text, contract, tuple(who))


# ============================================================
#  裁决层 —— 撞见（沿用地下城主结果已定之事的简报与契约）
# ============================================================
def _scene_names(scene: LocalSnapshot) -> tuple[str, ...]:
    """此景出现过的名字，长名在前（「无量剑法」不能被「无量剑」剔成「法」）；未知的去处只认出口标签，不认地名。"""
    names = {scene.player_name, scene.location.name, *(n for x in scene.exits for n in x.names)}
    for group in (scene.characters, scene.items, scene.skills):
        for view in group:
            names.update(view.names)
    return tuple(sorted((n for n in names if n), key=len, reverse=True))


def encounter_brief(
    encounter: Encounter, env: Envelope, scene: LocalSnapshot, state: PlayerState,
    agenda: NpcAgenda | None, atlas: Atlas,
) -> Brief:
    """
    撞见的简报：<encounter> 写明这不是玩家的举动、来者是谁、为何而来（他的议程：此地正是去处，或途经此地往别处去）、对你的态度与恩怨；
    其后是地下城主的 <time> … <known> 物理快照、<player> 与结果已定的 <physics>。来者须在快照里（调用方先核）。
    来意与去处过 veil：判官写下的细节会进玩家的回合，迷雾里的地名一个字也不给它。
    """
    comer = scene.character(encounter.npc_id)
    name = (comer.name + (f"（{'、'.join(comer.titles)}）" if comer.titles else "")) if comer else scene.label(encounter.npc_id)
    if agenda is None:
        errand = "来意：不明"
    elif agenda.target_id == encounter.location_id:
        errand = f"来意：{veil(agenda.intent, state, scene, atlas)}（{_WEIGHT[agenda.priority]}；此地正是他的去处）"
    else:
        there = veil(atlas.names.get(agenda.target_id, VEIL), state, scene, atlas)
        errand = f"来意：{veil(agenda.intent, state, scene, atlas)}（{_WEIGHT[agenda.priority]}；途经此地，正往{there}去）"
    cause = state.attitude_causes.get(encounter.npc_id)
    regard = f"对你{env.regard.value}" + (f"（恩怨：{cause}）" if cause else "")
    lines = ["种类：撞见——不是你的举动：来者与你在此不期而遇", f"来者：{name}｜{errand}｜{regard}"]
    text = "\n".join([
        "<encounter>", *(safe(line) for line in lines), "</encounter>",
        *world_section(scene, state, encounter.npc_id),
        *player_section(scene, state),
        *physics_section(env, None, scene),
    ])
    return Brief(text, schema(env, scene), _scene_names(scene))


# ============================================================
#  裁决层 —— 狭路相逢
# ============================================================
SKIRMISH_MEANING = {
    SkirmishOutcome.COMER_WINS: "{a}占了上风，{b}落败带伤、往回退去",
    SkirmishOutcome.HOLDER_WINS: "{b}占了上风，{a}落败带伤、往回退去",
    SkirmishOutcome.BOTH_HURT: "两人动了手，双双挂彩，各自的打算都落了空",
    SkirmishOutcome.QUARREL: "只是言语交锋、互相叫骂，没有动手",
    SkirmishOutcome.PASS: "彼此避让，相安无事",
}


def _side(role: str, c: Character, state: PlayerState, persona: Persona | None, agenda: NpcAgenda | None,
          places: Mapping[str, str]) -> str:
    hurt = "（带伤：交手折一档）" if wounded(state, c.id) else ""
    errand = f"{agenda.intent}（正往{places.get(agenda.target_id, '别处')}去）" if agenda else "无"
    return (
        f"{role}：{_titled(c)}｜{c.faction or '无门无派'}｜境界{c.tier.value}{hurt}｜性情{c.disposition.value}｜"
        f"执念：{persona.worry if persona and persona.worry else '无'}｜来意：{errand}"
    )


def skirmish_brief(
    stakes: SkirmishStakes, state: PlayerState, atlas: Atlas, personas: Mapping[str, Persona],
    relations: Sequence[CharacterRelation],
) -> Brief:
    """
    狭路相逢的简报：地点与时辰、来者（走进这处地方的那一位）与在此者、两人的开篇仇怨、此地传闻、可裁结局（含义里填实两人的名字）与确定性裁决。
    契约：reasoning（一两句推理）→ outcome（枚举可裁结局）→ fact（一条细节，可空）。
    """
    enc, a, b = stakes.encounter, stakes.comer, stakes.holder
    place = atlas.names.get(enc.location_id, "某处")
    feud = next(
        (r.note or r.kind.value for r in relations
         if r.era is Era.OPENING and r.kind is RelationKind.ENEMY and {r.source_id, r.target_id} == {a.id, b.id}),
        "开篇结下的仇怨",
    )
    rumors = _rumors(state, enc.location_id)
    outcomes = [
        f"- {o.value}：{SKIRMISH_MEANING[o].format(a=a.name, b=b.name)}" + ("｜← 确定性裁决" if o is stakes.canonical else "")
        for o in stakes.admissible
    ]
    lines = [
        "<encounter>", safe(f"狭路相逢｜{place}｜{_when(enc.tick)}"), "</encounter>",
        "<people>",
        safe(_side("来者", a, state, personas.get(a.id), state.agendas.get(a.id), atlas.names)),
        safe(_side("在此者", b, state, personas.get(b.id), state.agendas.get(b.id), atlas.names)),
        "</people>",
        f"<feud>{safe(f'开篇仇敌：{feud}')}</feud>",
        "<rumors>", *(safe(f"- {r}") for r in rumors), *([] if rumors else ["（没有任何消息传到此地）"]), "</rumors>",
        "<physics>", "可裁结局（只能挑其一）：", *(safe(o) for o in outcomes), "</physics>",
    ]
    contract: JsonSchema = {
        "type": "object",
        "properties": {
            "reasoning": {"type": "string"},
            "outcome": {"type": "string", "enum": [o.value for o in stakes.admissible]},
            "fact": {"type": "string"},
        },
        "required": ["reasoning", "outcome", "fact"],
        "additionalProperties": False,
    }
    names = sorted({*a.names, *b.names, place}, key=len, reverse=True)
    return Brief("\n".join(lines), contract, tuple(names))
