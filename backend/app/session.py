"""
[INPUT]: 依赖 app.schemas 的 GameState / PlayerState / WorldState / TagDelta / WorldDelta / DirectorOutput / LEDGERS / MAX_TAGS / MAX_EVENTS，
         依赖 app.errors 的 SessionDeadError / SessionBusyError
[OUTPUT]: 对外提供 evolve（状态推进）、reconcile（标签账）、chronicle（世界台账）、Turn、Session（含 acting 守卫、
          presence 在场判定素材、advance 推进）、SessionStore（LRU 内存仓库）
[POS]: app 的会话状态层，是世界状态的唯一权威；被 director/pipeline.py 读写，不感知 HTTP 与大模型
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import logging
from collections import OrderedDict, deque
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID, uuid4

from app.errors import SessionBusyError, SessionDeadError
from app.schemas import (
    LEDGERS,
    MAX_EVENTS,
    MAX_TAGS,
    DirectorOutput,
    GameState,
    PlayerState,
    TagDelta,
    WorldDelta,
    WorldState,
)

logger = logging.getLogger(__name__)

_EVENT_CHARS = 80


# ============================================================
#  状态推进 —— 快照照单全收；标签账与世界台账只认增减
# ============================================================
def evolve(state: GameState, out: DirectorOutput) -> GameState:
    before, delta = state.player_state, out.player_delta
    ledgers = {name: reconcile(getattr(before, name), getattr(delta, name)) for name in LEDGERS}
    player = PlayerState(**out.next_state.model_dump(), **ledgers)
    world = WorldState(major_events=chronicle(state.world_state.major_events, out.world_delta))
    return GameState(player_state=player, world_state=world)


def reconcile(tags: list[str], delta: TagDelta) -> list[str]:
    """
    标签守恒：只有被点名移除的才会消失，大模型的遗漏不等于失去。
    先减后加——"水囊"喝空写作 移除「水囊」+ 新增「空水囊」，先减才不会与新增之物撞名。
    """
    kept = list(tags)
    for name in delta.remove:
        held = _match(kept, name)
        if held is None:
            logger.info("忽略未持有或指代不明的移除：%s ∉ %s", name, kept)
        else:
            kept.remove(held)
    for name in delta.add:
        if name not in kept:
            kept.append(name)
    if len(kept) > MAX_TAGS:
        logger.warning("标签超过 %d 个，截去最后新增的：%s", MAX_TAGS, kept[MAX_TAGS:])
    return kept[:MAX_TAGS]


def chronicle(events: list[str], delta: WorldDelta) -> list[str]:
    """
    世界台账：只增不删。
    - 合并只在必要时生效：台账放不下本回合的新事件时，按需采纳，多余的合并一律忽略——合并会损失细节，能不并就不并
    - 合并必须点名至少两条现存旧事件——大模型无法借"合并"抹掉历史
    - 新事件追加在末尾
    - 仍超 MAX_EVENTS 时确定性兜底：把最旧两条折叠为一条，条数受控而信息不丢
    """
    ledger = list(events)
    fresh = [e for e in dict.fromkeys(delta.events_added) if e not in ledger]
    for merge in delta.events_merged:
        if len(ledger) + len(fresh) <= MAX_EVENTS:
            logger.info("台账放得下，忽略不必要的合并：%s", merge.sources)
            continue
        found = [_match(ledger, source) for source in merge.sources]
        if None in found or len(set(found)) < 2:
            logger.info("合并须点名至少两条现存事件，已忽略：%s", merge.sources)
            continue
        first = min(ledger.index(e) for e in found)
        ledger = [e for e in ledger if e not in found]
        ledger.insert(first, merge.into)
    ledger.extend(fresh)
    while len(ledger) > MAX_EVENTS:
        folded = _fold(ledger[0], ledger[1])
        logger.warning("台账超过 %d 条，折叠最旧两条：%s", MAX_EVENTS, folded)
        ledger[:2] = [folded]
    return ledger


def _fold(a: str, b: str) -> str:
    joined = f"{a}；{b}"
    return joined if len(joined) <= _EVENT_CHARS else joined[: _EVENT_CHARS - 1] + "…"


def _match(tags: list[str], name: str) -> str | None:
    """精确匹配优先；否则接受唯一的包含关系（「弯刀」↔「契丹弯刀」），多义时宁可不动。"""
    if name in tags:
        return name
    candidates = [tag for tag in tags if name in tag or tag in name]
    return candidates[0] if len(candidates) == 1 else None


@dataclass(frozen=True)
class Turn:
    """一回合的叙事记忆。四个状态字段装不下"黑衣人正盯着你"，涌现叙事靠这段短记忆维持连贯。"""

    action: str  # 空串表示开局
    scene: str


@dataclass
class Session:
    id: UUID
    state: GameState
    history: deque[Turn]
    present: tuple[str, ...] = ()  # 导演给出的在场人物真实姓名
    dead: bool = False
    busy: bool = False

    def presence(self) -> str:
        """致死预判的"在场"素材：优先用导演的结构化名单；名单缺席（Mock / 冷启动）时退回上一幕原文。"""
        if self.present:
            return "、".join(self.present)
        return self.history[-1].scene if self.history else ""

    @contextmanager
    def acting(self) -> Iterator[None]:
        """回合守卫：死者不得行动，上一招未落定不得出下一招。"""
        if self.dead:
            raise SessionDeadError("此身已死，江湖再无你的传说。请重新投胎。")
        if self.busy:
            raise SessionBusyError("上一招尚未落定，莫要心急。")
        self.busy = True
        try:
            yield
        finally:
            self.busy = False

    def advance(self, action: str, out: DirectorOutput) -> None:
        self.state = evolve(self.state, out)
        self.history.append(Turn(action, out.scene_description))
        self.present = tuple(out.present)
        self.dead = out.game_over


class SessionStore:
    """纯内存会话仓库。OrderedDict 实现 LRU：超出容量时淘汰最久未活动的会话。"""

    def __init__(self, capacity: int = 1000, history_turns: int = 4) -> None:
        self._sessions: OrderedDict[UUID, Session] = OrderedDict()
        self._capacity = capacity
        self._history_turns = history_turns

    def create(self, state: GameState, opening_scene: str, present: tuple[str, ...] = ()) -> Session:
        return self._put(uuid4(), state, (Turn("", opening_scene),), present)

    def get_or_rehydrate(self, session_id: UUID, client_state: GameState) -> Session:
        """服务端会话优先；丢失时以客户端快照冷启动（开发期热重载、进程重启后无缝续玩）。"""
        session = self._sessions.get(session_id)
        if session is None:
            return self._put(session_id, client_state)
        self._sessions.move_to_end(session_id)
        return session

    def _put(
        self, session_id: UUID, state: GameState, turns: tuple[Turn, ...] = (), present: tuple[str, ...] = ()
    ) -> Session:
        session = Session(session_id, state, deque(turns, maxlen=self._history_turns), present)
        self._sessions[session_id] = session
        while len(self._sessions) > self._capacity:
            self._sessions.popitem(last=False)
        return session
