"""
[INPUT]: 依赖 pydantic v2 的 BaseModel / ConfigDict，依赖 domain/rules 的 adjudicate / command / retreat / Approval，
         依赖 domain/geography 的 Direction / TravelMethod / DiscoveryStatus / COMPASS，依赖 domain/commands 的 Command / TICKS_PER_SHICHEN / TICKS_PER_DAY，
         依赖 domain/intent 的 ActionType / PlayerIntent，依赖 domain/models 的 Attitude，依赖 domain/snapshot 的 LocalSnapshot / ExitView；PlayerState 仅作类型标注
[OUTPUT]: 对外提供 NavigationOption（id「nav-<摘要>」、方位、去处（已知名或「未知区域」）、交通方式、耗时刻数与中文时长、认知、是否脱身之路、
          只在服务端的 underlying_command 与只读属性 intent）、navigation(state, snap)（每条获准的出路一项，按 COMPASS 次序再按把手）、
          duration_label(ticks)（「一刻」「约半个时辰」「约一个时辰」「约半日」「约两日」）
[POS]: application 的方位导航：移动从交互选项里剥离，单独下发——前端在方位按钮上悬停时看得到去处、交通方式、耗时与认知。
       一切取自快照的 ExitView（空间属性图 CONNECTS_TO 的 direction / travel_method / time_cost 与玩家的 discovery）：
       未知的去处只说「未知区域」，指令的 target_entity 永远是方位把手 snap.handle(出路)（「东」「东·二」）——绝不用出口标签或地名，
       免得 turn_resolved 的意图把迷雾里的地名露给玩家；id 的摘要取「此地 + 指令意图」，换了地方同一个「东」是另一个 id，
       过期的点选在新地方落空。每项都经 rules.adjudicate 核验、rules.command 定指令与耗时（移动以那条出路的耗时为准），与玩家打字走同一条规则。
       retreat：仇人（敌视且行动自如者）在侧时，rules.retreat 会走的那一条（先走来路，绝不逃回险地，去处站着仇人的不算）——脱身席从菜单搬到了这里。
       导航是 (状态, 快照) 的纯函数：点选时服务端重算 affordances ∪ navigation，按 id 取回指令；死者没有导航
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from app.domain import rules
from app.domain.commands import TICKS_PER_DAY, TICKS_PER_SHICHEN, Command
from app.domain.geography import COMPASS, Direction, DiscoveryStatus, TravelMethod
from app.domain.intent import ActionType, PlayerIntent
from app.domain.models import Attitude
from app.domain.snapshot import ExitView, LocalSnapshot

if TYPE_CHECKING:
    from app.domain.aggregates import PlayerState


class NavigationOption(BaseModel):
    """一条出路：前端渲染在方位按钮上，玩家点的是 id，服务端执行的是 underlying_command（MOVE + 方位把手）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    direction: Direction
    target: str  # 去处：已知即其名，未知即「未知区域」
    travel_method: TravelMethod
    time_cost: int  # 刻
    time_label: str  # 「一刻」「约一个时辰」「约两日」
    discovery: DiscoveryStatus
    retreat: bool = False  # 仇人在侧时的脱身之路（rules.retreat 会走的那一条）
    underlying_command: Command  # 只在服务端

    @property
    def intent(self) -> PlayerIntent:
        return self.underlying_command.intent


# ============================================================
#  时长 —— 一刻十五分钟，一个时辰八刻，一日九十六刻
# ============================================================
_COUNT = "零一两三四五六七八九十"
_HALF_DAY = TICKS_PER_DAY // 2


def duration_label(ticks: int) -> str:
    """
    耗时的中文说法：不足半个时辰按刻（「一刻」「两刻」「三刻」）；不足三个半时辰按时辰取整（「约半个时辰」「约一个时辰」…「约四个时辰」）；
    再往上按半日取整（「约半日」「约一日」「约一日半」…「约七日」）。
    """
    ticks = max(1, ticks)
    half = TICKS_PER_SHICHEN // 2
    if ticks < half:
        return f"{_COUNT[ticks]}刻"
    if ticks < TICKS_PER_SHICHEN * 3 // 4:
        return "约半个时辰"
    shichen = (ticks + half) // TICKS_PER_SHICHEN  # 四舍五入
    if shichen <= 4:
        return f"约{_COUNT[shichen]}个时辰"
    halves = (ticks + _HALF_DAY // 2) // _HALF_DAY  # 半日的个数，四舍五入
    if halves <= 1:
        return "约半日"
    days, rest = divmod(halves, 2)
    return f"约{_numeral(days)}日{'半' if rest else ''}"


def _numeral(n: int) -> str:
    return _COUNT[n] if n <= 10 else str(n)  # 导航的耗时至多七日（MAX_TIME_COST）


# ============================================================
#  导航 —— 每条获准的出路一项
# ============================================================
def _digest(here: str, intent: PlayerIntent) -> str:
    """此地 + 指令意图的摘要：同一个「东」换了地方就是另一个 id；去处不进摘要（未知之地的名字不能从 id 反推出来）。"""
    return hashlib.sha1(f"{here}|{intent.model_dump_json(exclude={'motivation'})}".encode()).hexdigest()[:8]


def _foes(snap: LocalSnapshot) -> bool:
    return any(c.attitude is Attitude.HOSTILE and not c.subdued for c in snap.characters)


def _order(snap: LocalSnapshot, way: ExitView) -> tuple[int, str]:
    return COMPASS.index(way.direction), snap.handle(way)


def navigation(state: PlayerState, snap: LocalSnapshot) -> tuple[NavigationOption, ...]:
    if not state.alive:
        return ()
    escape = rules.retreat(state, snap) if _foes(snap) else None
    items: list[NavigationOption] = []
    for way in sorted(snap.exits, key=lambda e: _order(snap, e)):
        intent = PlayerIntent(action_type=ActionType.MOVE, target_entity=snap.handle(way))
        verdict = rules.adjudicate(intent, state, snap)
        if not isinstance(verdict, rules.Approval) or verdict.target != way.to_id:
            continue
        command = rules.command(intent, state, snap)
        items.append(NavigationOption(
            id=f"nav-{_digest(snap.location.id, command.intent)}",
            direction=way.direction,
            target=way.shown_name,
            travel_method=way.travel_method,
            time_cost=command.time_cost,
            time_label=duration_label(command.time_cost),
            discovery=way.discovery,
            retreat=escape is not None and escape.to_id == way.to_id and escape.label == way.label,
            underlying_command=command,
        ))
    return tuple(items)


__all__ = ["NavigationOption", "duration_label", "navigation"]
