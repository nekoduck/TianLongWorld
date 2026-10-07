"""
[INPUT]: 依赖 app.schemas 的契约模型与常量，依赖 app.events 的 LifeBegan / TurnResolved / WorldEventRecorded / LifeEvent，
         依赖 app.lore 的 SHICHEN / MASTER_ARTS / OpeningSeed
[OUTPUT]: 对外提供视图模型 Turn、LocalEnvironment、LifeView、Decision；
          投影 begin / apply / project_life / project_world / game_state / snapshot_of；
          裁决 decide_opening / decide_turn；记账原语 resolve / apply_tags / observe / match
[POS]: app 的决定论运行时内核，全部是纯函数（显式入参、无 I/O、无日志、无全局可变状态）：
       裁决 decide_* 把大模型的结构化意图校验成确切事实（事件），投影 apply / project_* 把事件折叠成状态树——
       f(当前状态, 玩家动作 + 已校验意图) -> 事件 -> 下一状态。被拒绝的意图以 Decision.rejections 交还编排器记日志
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from collections.abc import Sequence
from uuid import UUID

from app.events import LifeBegan, LifeEvent, TurnResolved, WorldEventRecorded
from app.lore import MASTER_ARTS, SHICHEN, OpeningSeed
from app.schemas import (
    MAX_TAGS,
    NO_PLAYER_CHANGE,
    ActionType,
    DirectorOutput,
    Frozen,
    GameState,
    Label,
    LocalDelta,
    Options,
    PlayerDelta,
    PlayerSnapshot,
    PlayerState,
    TagDelta,
    WorldEvent,
    WorldState,
)

MAX_ENTITIES = 12
MAX_TIME_STEP = 6  # 一回合时辰至多前进半天：环形时辰里更大的跨度与"倒退"无法区分，一律视为非法
DEATH_MARKS = ("死", "亡", "毙", "绝")
DEFAULT_DEATH = "气绝身亡"


# ============================================================
#  视图模型 —— 事件折叠出的物化视图，全部不可变
# ============================================================
class Turn(Frozen):
    action: str  # 空串表示开局
    scene: str


class LocalEnvironment(Frozen):
    """局部视野：当前地点与在场的有名有姓者。换地图时强制清空。"""

    location: str
    entities: tuple[str, ...]


class LifeView(Frozen):
    life_id: UUID
    world_id: UUID
    player: PlayerState
    local: LocalEnvironment
    window: tuple[Turn, ...]  # 滑动窗口：只保留最新 N 回合，更早的直接截断
    options: Options | None
    dead: bool
    version: int  # 已折叠的事件数 = 追加时的乐观并发版本号


class Decision(Frozen):
    """一回合裁决的产物：待追加的事实，以及被运行时拒绝的意图（供编排器记日志与测试断言）。"""

    turn: TurnResolved
    world_events: tuple[WorldEventRecorded, ...]
    rejections: tuple[str, ...]


# ============================================================
#  投影 —— 事件 -> 状态
# ============================================================
def begin(life_id: UUID, event: LifeBegan) -> LifeView:
    return LifeView(
        life_id=life_id,
        world_id=event.world_id,
        player=event.player,
        local=LocalEnvironment(location=event.player.location, entities=event.entities),
        window=(Turn(action="", scene=event.scene),),
        options=event.options,
        dead=False,
        version=1,
    )


def apply(view: LifeView, event: TurnResolved, window: int) -> LifeView:
    return LifeView(
        life_id=view.life_id,
        world_id=view.world_id,
        player=_evolve(view.player, event.snapshot, event.changes),
        local=LocalEnvironment(location=event.snapshot.location, entities=event.entities),
        window=(*view.window, Turn(action=event.action, scene=event.scene))[-window:],
        options=event.options,
        dead=event.died,
        version=view.version + 1,
    )


def project_life(life_id: UUID, events: Sequence[LifeEvent], window: int) -> LifeView:
    if not events or not isinstance(events[0], LifeBegan):
        raise ValueError(f"life 流 {life_id} 必须以 LifeBegan 开头")
    view = begin(life_id, events[0])
    for event in events[1:]:
        if not isinstance(event, TurnResolved):
            raise ValueError(f"life 流 {life_id} 中出现重复的 LifeBegan")
        view = apply(view, event, window)
    return view


def project_world(events: Sequence[WorldEventRecorded]) -> WorldState:
    return WorldState(major_events=tuple(recorded.event for recorded in events))


def game_state(view: LifeView, world: WorldState) -> GameState:
    return GameState(player_state=view.player, world_state=world)


def snapshot_of(player: PlayerState) -> PlayerSnapshot:
    """玩家状态中由大模型每回合重写的那一半：兜底裁决与 Mock 以此为提议快照的起点。"""
    return PlayerSnapshot(
        location=player.location, time=player.time, weather=player.weather, health_status=player.health_status
    )


def _evolve(player: PlayerState, snapshot: PlayerSnapshot, changes: PlayerDelta) -> PlayerState:
    return PlayerState(
        location=snapshot.location,
        time=snapshot.time,
        weather=snapshot.weather,
        health_status=snapshot.health_status,
        buffs_debuffs=apply_tags(player.buffs_debuffs, changes.buffs_debuffs),
        social_traits=apply_tags(player.social_traits, changes.social_traits),
        inventory=apply_tags(player.inventory, changes.inventory),
        martial_arts=apply_tags(player.martial_arts, changes.martial_arts),
    )


# ============================================================
#  裁决 —— 已校验的意图 -> 事件
# ============================================================
def decide_opening(world_id: UUID, seed: OpeningSeed, out: DirectorOutput) -> LifeBegan:
    """开局：状态完全取自种子。大模型只贡献开场叙事、选项与在场者；开局判死、开局授予标签一律不予采纳。"""
    if out.options is None:
        raise ValueError("开局裁决必须带选项（编排器应在采纳前拒绝 game_over 的开局）")
    return LifeBegan(
        world_id=world_id,
        player=seed.player,
        scene=out.scene_description,
        options=out.options,
        entities=_unique(out.local_delta.arrived)[:MAX_ENTITIES],
    )


def decide_turn(view: LifeView, action_type: ActionType, action: str, out: DirectorOutput, *, condemned: bool) -> Decision:
    """
    一回合的决定论裁决：
    - 规则层判死（condemned）：死亡封印，大模型的一切增减与世界大事作废——死刑不容借叙事赦免，也不得污染世界线
    - 快照：时辰须属十二时辰且只能前进；死者的生命体征必须写明死状
    - 四本账：移除项解析为清单原名，绝学不得由大模型授予
    - 局部环境：同地只认到场/离场，换地图强制清空
    """
    died = condemned or out.game_over
    snapshot, snapshot_notes = _validate_snapshot(view.player, out.next_state, died)
    change_notes: tuple[str, ...] = ()
    world: tuple[WorldEvent, ...] = ()
    if condemned:
        changes = NO_PLAYER_CHANGE
    else:
        changes, change_notes = _resolve_player(view.player, out.player_delta)
        world = _unique_events(out.world_events)
    turn = TurnResolved(
        action_type=action_type,
        action=action,
        scene=out.scene_description,
        snapshot=snapshot,
        changes=changes,
        entities=observe(view.local, snapshot.location, out.local_delta),
        options=None if died else out.options,
        died=died,
    )
    recorded = tuple(WorldEventRecorded(life_id=view.life_id, event=event) for event in world)
    return Decision(turn=turn, world_events=recorded, rejections=(*snapshot_notes, *change_notes))


def _validate_snapshot(before: PlayerState, proposed: PlayerSnapshot, died: bool) -> tuple[PlayerSnapshot, tuple[str, ...]]:
    notes: list[str] = []
    time = _shichen(proposed.time)
    if time is None or not _time_moves_forward(before.time, time):
        notes.append(f"拒绝非法时辰：{before.time} → {proposed.time}")
        time = before.time
    health = proposed.health_status
    if died and not any(mark in health for mark in DEATH_MARKS):
        notes.append(f"死者生命体征改写为死状：{health}")
        health = DEFAULT_DEATH
    snapshot = PlayerSnapshot(location=proposed.location, time=time, weather=proposed.weather, health_status=health)
    return snapshot, tuple(notes)


def _shichen(text: str) -> str | None:
    """宽容大模型的修饰（"子时三刻"），但结果必须落在十二时辰之内。"""
    return next((hour for hour in SHICHEN if hour in text), None)


def _time_moves_forward(before: str, after: str) -> bool:
    if before not in SHICHEN:
        return True
    return (SHICHEN.index(after) - SHICHEN.index(before)) % len(SHICHEN) <= MAX_TIME_STEP


def _resolve_player(player: PlayerState, delta: PlayerDelta) -> tuple[PlayerDelta, tuple[str, ...]]:
    buffs, n1 = resolve(player.buffs_debuffs, delta.buffs_debuffs)
    traits, n2 = resolve(player.social_traits, delta.social_traits)
    items, n3 = resolve(player.inventory, delta.inventory)
    arts, n4 = resolve(player.martial_arts, delta.martial_arts, forbidden=MASTER_ARTS)
    changes = PlayerDelta(buffs_debuffs=buffs, social_traits=traits, inventory=items, martial_arts=arts)
    return changes, (*n1, *n2, *n3, *n4)


# ============================================================
#  记账原语
# ============================================================
def resolve(tags: tuple[str, ...], delta: TagDelta, *, forbidden: tuple[str, ...] = ()) -> tuple[TagDelta, tuple[str, ...]]:
    """
    把增减意图解析为确切事实：移除项映射到清单原名（唯一包含匹配，多义不动）；
    新增项去重、滤掉禁授标签与已有项、受容量约束。遗漏不等于失去——没点名移除的一律保留。
    """
    notes: list[str] = []
    kept = list(tags)
    removed: list[str] = []
    for name in delta.remove:
        held = match(kept, name)
        if held is None:
            notes.append(f"忽略未持有或指代不明的移除：{name}")
            continue
        kept.remove(held)
        removed.append(held)
    added: list[str] = []
    for name in delta.add:
        if any(art in name for art in forbidden):
            notes.append(f"拒绝大模型授予的绝学：{name}")
        elif name not in kept and name not in added:
            added.append(name)
    room = MAX_TAGS - len(kept)
    if len(added) > room:
        notes.append(f"标签已满，舍弃新增：{added[room:]}")
        added = added[:room]
    return TagDelta(add=tuple(added), remove=tuple(removed)), tuple(notes)


def apply_tags(tags: tuple[str, ...], change: TagDelta) -> tuple[str, ...]:
    return (*(tag for tag in tags if tag not in change.remove), *change.add)


def observe(local: LocalEnvironment, location: str, delta: LocalDelta) -> tuple[Label, ...]:
    """
    局部环境实体账：同一地图内只认到场与离场的增减（遗漏不等于离场）；换地图时强制清空，只留新地点的到场者。
    子地点（"无锡松鹤楼" → "无锡松鹤楼二楼"）视作同一地图，以免措辞变化误清在场的高手。
    """
    same_map = location in local.location or local.location in location
    base = local.entities if same_map else ()
    departed = {held for name in delta.departed if (held := match(list(base), name)) is not None}
    kept = [entity for entity in base if entity not in departed]
    kept += [name for name in _unique(delta.arrived) if name not in kept]
    return tuple(kept[:MAX_ENTITIES])


def match(tags: list[str], name: str) -> str | None:
    """精确匹配优先；否则接受唯一的包含关系（「弯刀」↔「契丹弯刀」），多义时宁可不动。"""
    if name in tags:
        return name
    candidates = [tag for tag in tags if name in tag or tag in name]
    return candidates[0] if len(candidates) == 1 else None


def _unique(names: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(names))


def _unique_events(events: Sequence[WorldEvent]) -> tuple[WorldEvent, ...]:
    seen: set[str] = set()
    kept: list[WorldEvent] = []
    for event in events:
        if event.event_desc not in seen:
            seen.add(event.event_desc)
            kept.append(event)
    return tuple(kept)
