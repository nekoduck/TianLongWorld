"""
[INPUT]: 依赖 pydantic v2 的 BaseModel / Field，依赖 enum 的 StrEnum，依赖 domain/lore 的 Source，依赖 domain/commands 的 TIME_COSTS / NEARBY_MOVE / same_place / MAX_TIME_COST；
         WorldBlueprint / Location 仅作类型标注（models 运行期导入本模块，反向只在 TYPE_CHECKING 里，不成环）
[OUTPUT]: 对外提供 空间属性图的词汇 Direction（东 / 南 / 西 / 北 / 东北 / 东南 / 西北 / 西南 / 上 / 下 / 内部 / 外部 / 不明，.opposite）、
          TravelMethod（步行 / 骑马 / 乘船 / 攀援 / 轻功 / 坠落）、DiscoveryStatus（亲历 / 问路 / 远眺 / 名胜 / 未知，.known）、UNKNOWN_PLACE「未知区域」、
          掌故式的正典注记 Passage（一条出路的方位、交通方式、耗时与出处）与 Sight（地标远眺可见、名胜天下皆知）、
          Way（一条出路的有效属性：撰写过的注记优先，否则由出口标签与处所名确定性推出，且与回程对齐——往返方位永远相反）与 ways(bp)、
          derive_direction / derive_method / derive_cost 推导、discovery()（玩家对一处去处的认知）、geography_errors(bp)（地理注记的蓝图闸门）
[POS]: domain 的空间拓扑：CONNECTS_TO 不再只是"通向哪里"，而是带方位、交通方式与耗时的边——移动的耗时、NPC 寻路的权重、导航的方位都读它。
       方位、交通方式与耗时是原著常识，像审计与掌故一样由 Claude 子代理离线撰写、经闸门入库（infrastructure/geo_gate.py），provenance 永远是推断；
       没撰写过的出路照样能走：方位从出口标签（「北上」「出宫往西北」「入练武厅」）与处所名的嵌套推出，耗时沿用命令耗时表（同一处所之内一刻、换处所四刻），交通方式缺省步行。
       探索迷雾是玩家的认知而不是世界的属性：亲历过（到过）、问路得知（PlacesLearned）、地标远眺可见、名胜天下皆知——都不是则下发「未知区域」，
       出口标签里的地名同样不得露给玩家（显示层只用方位与 shown_name）
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from app.domain.commands import MAX_TIME_COST, NEARBY_MOVE, TIME_COSTS, same_place
from app.domain.intent import ActionType
from app.domain.lore import Source

if TYPE_CHECKING:
    from app.domain.models import WorldBlueprint

UNKNOWN_PLACE = "未知区域"
BASIS_CHARS = 40


class Direction(StrEnum):
    EAST = "东"
    SOUTH = "南"
    WEST = "西"
    NORTH = "北"
    NORTHEAST = "东北"
    SOUTHEAST = "东南"
    NORTHWEST = "西北"
    SOUTHWEST = "西南"
    UP = "上"
    DOWN = "下"
    INSIDE = "内部"
    OUTSIDE = "外部"
    UNSPECIFIED = "不明"  # 只用于推不出方位的出路；撰写的注记不许写它

    @property
    def opposite(self) -> Direction:
        return _OPPOSITE[self]


_OPPOSITE = {
    Direction.EAST: Direction.WEST, Direction.WEST: Direction.EAST,
    Direction.SOUTH: Direction.NORTH, Direction.NORTH: Direction.SOUTH,
    Direction.NORTHEAST: Direction.SOUTHWEST, Direction.SOUTHWEST: Direction.NORTHEAST,
    Direction.SOUTHEAST: Direction.NORTHWEST, Direction.NORTHWEST: Direction.SOUTHEAST,
    Direction.UP: Direction.DOWN, Direction.DOWN: Direction.UP,
    Direction.INSIDE: Direction.OUTSIDE, Direction.OUTSIDE: Direction.INSIDE,
    Direction.UNSPECIFIED: Direction.UNSPECIFIED,
}
COMPASS: tuple[Direction, ...] = tuple(Direction)  # 导航的固定次序：东南西北、四隅、上下、内外、不明


class TravelMethod(StrEnum):
    WALK = "步行"
    RIDE = "骑马"
    BOAT = "乘船"
    CLIMB = "攀援"
    QINGGONG = "轻功"
    FALL = "坠落"  # 失足坠崖之类的单程下坠


class DiscoveryStatus(StrEnum):
    """玩家对一处去处的认知，由强到弱：到过、问过路、远远望得见、天下皆知；都不是即未知——名字下发为「未知区域」。"""

    VISITED = "亲历"
    TOLD = "问路"
    SIGHTED = "远眺"
    RENOWNED = "名胜"
    UNKNOWN = "未知"

    @property
    def known(self) -> bool:
        return self is not DiscoveryStatus.UNKNOWN


# ============================================================
#  正典注记 —— 由子代理撰写、经闸门入库，provenance 永远是推断
# ============================================================
class _Note(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Passage(_Note):
    """一条出路（from → to，须是蓝图里已有的出口）的方位、交通方式与耗时（刻，一刻十五分钟）。"""

    from_id: str = Field(pattern=r"^loc:.+")
    to_id: str = Field(pattern=r"^loc:.+")
    direction: Direction
    travel_method: TravelMethod = TravelMethod.WALK
    time_cost: int = Field(ge=1, le=MAX_TIME_COST)
    basis: str = Field(default="", max_length=BASIS_CHARS)  # 据何推断（一句中文）
    sources: tuple[Source, ...] = Field(min_length=1)


class Sight(_Note):
    """一处地方的可见性：landmark 地标——相邻之处远远望得见；renowned 名胜——天下皆知，未到也叫得出名字。"""

    location_id: str = Field(pattern=r"^loc:.+")
    landmark: bool = False
    renowned: bool = False
    sources: tuple[Source, ...] = Field(min_length=1)


# ============================================================
#  有效属性 —— 撰写的注记优先，否则确定性推出
# ============================================================
@dataclass(frozen=True, slots=True)
class Way:
    from_id: str
    to_id: str
    label: str
    direction: Direction
    travel_method: TravelMethod
    time_cost: int
    authored: bool = False


_COMPOUND = (Direction.NORTHEAST, Direction.SOUTHEAST, Direction.NORTHWEST, Direction.SOUTHWEST)
_CARDINAL = (Direction.EAST, Direction.SOUTH, Direction.WEST, Direction.NORTH)


def derive_direction(label: str, here: str, there: str) -> Direction:
    """
    推不出注记时的方位：处所名的嵌套优先（剑湖宫 → 剑湖宫·练武厅 是内部，反之外部），
    再看出口标签——四隅先于正向（「出宫往西北」是西北）、正向取最先出现的一个（「北上」是北、「南下」是南），
    再认 入 / 进 → 内部、出 → 外部、上 / 下；都不是即不明。标签里去处自己的名字先抹掉再看（地名里的方位字不算方位）。
    """
    if there.startswith(here + "·"):
        return Direction.INSIDE
    if here.startswith(there + "·"):
        return Direction.OUTSIDE
    for name in (there, there.rsplit("·", 1)[-1]):  # 去处的名字不是方位：「往无量山·西北角山坡」的西北是地名的一部分
        label = label.replace(name, "")
    if found := next((d for d in _COMPOUND if d.value in label), None):
        return found
    hits = [(label.index(d.value), d) for d in _CARDINAL if d.value in label]
    if hits:
        return min(hits)[1]
    for chars, direction in (("入进", Direction.INSIDE), ("出", Direction.OUTSIDE), ("上", Direction.UP), ("下", Direction.DOWN)):
        if any(c in label for c in chars):
            return direction
    return Direction.UNSPECIFIED


def derive_method(label: str) -> TravelMethod:
    for chars, method in (("坠", TravelMethod.FALL), ("船舟渡", TravelMethod.BOAT), ("攀爬", TravelMethod.CLIMB)):
        if any(c in label for c in chars):
            return method
    return TravelMethod.WALK


def derive_cost(here: str, there: str) -> int:
    return NEARBY_MOVE if same_place(here, there) else TIME_COSTS[ActionType.MOVE]


def ways(bp: WorldBlueprint) -> dict[tuple[str, str], Way]:
    """蓝图里每一条出口的有效属性，键为 (from_id, to_id)。两套图谱、世界物理（寻路）与 Cypher 编译共用这一个函数。"""
    names = {loc.id: loc.name for loc in bp.locations}
    authored = {(p.from_id, p.to_id): p for p in bp.passages}
    first: dict[tuple[str, str], Way] = {}
    for loc in bp.locations:
        for label, target in loc.exits.items():
            if target not in names:
                continue
            if (note := authored.get((loc.id, target))) is not None:
                first[(loc.id, target)] = Way(loc.id, target, label, note.direction, note.travel_method, note.time_cost, True)
            else:
                first[(loc.id, target)] = Way(
                    loc.id, target, label, derive_direction(label, loc.name, names[target]), derive_method(label),
                    derive_cost(loc.name, names[target]),
                )
    return {key: _aligned(key, way, first) for key, way in first.items()}


def _aligned(key: tuple[str, str], way: Way, first: dict[tuple[str, str], Way]) -> Way:
    """
    没撰写过的出路与回程对齐，往返方位永远相反：回程撰写过 → 取其反（耗时随回程，回程是坠落则不随——跳下去快、爬上来慢）；
    自己推不出而回程推得出 → 取其反；两头都推得出却不相反 → 以 (from, to) 较小的一头为准。撰写过的注记由闸门保证相反，原样不动。
    """
    back = first.get((key[1], key[0]))
    if way.authored or back is None:
        return way
    if back.authored:
        cost = way.time_cost if back.travel_method is TravelMethod.FALL else back.time_cost
        return Way(way.from_id, way.to_id, way.label, back.direction.opposite, way.travel_method, cost)
    if back.direction is Direction.UNSPECIFIED or back.direction is way.direction.opposite:
        return way
    if way.direction is Direction.UNSPECIFIED or key > (back.from_id, back.to_id):
        return Way(way.from_id, way.to_id, way.label, back.direction.opposite, way.travel_method, way.time_cost)
    return way


def discovery(
    location_id: str, *, visited: frozenset[str], heard: frozenset[str], landmarks: frozenset[str], renowned: frozenset[str]
) -> DiscoveryStatus:
    if location_id in visited:
        return DiscoveryStatus.VISITED
    if location_id in heard:
        return DiscoveryStatus.TOLD
    if location_id in landmarks:
        return DiscoveryStatus.SIGHTED
    if location_id in renowned:
        return DiscoveryStatus.RENOWNED
    return DiscoveryStatus.UNKNOWN


# ============================================================
#  闸门 —— 注记须落在已有的出口上，往返方位相反，耗时有界
# ============================================================
def geography_errors(bp: WorldBlueprint) -> list[str]:
    exits = {(loc.id, target) for loc in bp.locations for target in loc.exits.values()}
    places = {loc.id for loc in bp.locations}
    errors: list[str] = []
    seen: dict[tuple[str, str], Passage] = {}
    for p in bp.passages:
        key = (p.from_id, p.to_id)
        if key in seen:
            errors.append(f"重复的道路注记：{p.from_id} → {p.to_id}")
        seen[key] = p
        if key not in exits:
            errors.append(f"道路注记 {p.from_id} → {p.to_id} 不是蓝图里的一条出口")
        if p.direction is Direction.UNSPECIFIED:
            errors.append(f"道路注记 {p.from_id} → {p.to_id} 的方位不得写「不明」")
    for (a, b), p in seen.items():
        back = seen.get((b, a))
        if back is not None and back.direction is not p.direction.opposite:
            errors.append(f"往返方位不相反：{a} → {b} 是{p.direction}，{b} → {a} 却是{back.direction}")
    sights: set[str] = set()
    for s in bp.sights:
        if s.location_id in sights:
            errors.append(f"重复的可见性注记：{s.location_id}")
        sights.add(s.location_id)
        if s.location_id not in places:
            errors.append(f"可见性注记落在不存在的地点：{s.location_id}")
        if not (s.landmark or s.renowned):
            errors.append(f"可见性注记 {s.location_id} 既非地标也非名胜")
    return errors
