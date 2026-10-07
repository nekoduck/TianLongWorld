"""
[INPUT]: 依赖 app.schemas 的 WorldState / DirectorOutput / MAX_ITEMS，依赖 app.errors 的 SessionDeadError / SessionBusyError
[OUTPUT]: 对外提供 evolve / reconcile（状态推进与物品记账）、Turn、Session（含 acting 守卫、presence 在场判定素材、advance 推进）、SessionStore（LRU 内存仓库）
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
from app.schemas import MAX_ITEMS, DirectorOutput, WorldState

logger = logging.getLogger(__name__)


# ============================================================
#  状态推进 —— 快照照单全收，随身物品只认增减
# ============================================================
def evolve(state: WorldState, out: DirectorOutput) -> WorldState:
    inventory = reconcile(state.inventory, out.items_gained, out.items_lost)
    return WorldState(**out.next_state.model_dump(), inventory=inventory)


def reconcile(inventory: list[str], gained: list[str], lost: list[str]) -> list[str]:
    """
    物品守恒：只有被点名失去的才会离身，大模型的遗漏不等于失去。
    先失后得——"水囊"喝空写作 失去「水囊」+ 得到「空水囊」，先失才不会与新得之物撞名。
    """
    kept = list(inventory)
    for name in lost:
        held = _held(kept, name)
        if held is None:
            logger.info("忽略未持有或指代不明的失去：%s ∉ %s", name, kept)
        else:
            kept.remove(held)
    for name in gained:
        if name not in kept:
            kept.append(name)
    if len(kept) > MAX_ITEMS:
        logger.warning("随身物品超过 %d 件，截去最后得到的：%s", MAX_ITEMS, kept[MAX_ITEMS:])
    return kept[:MAX_ITEMS]


def _held(inventory: list[str], name: str) -> str | None:
    """精确匹配优先；否则接受唯一的包含关系（「弯刀」↔「契丹弯刀」），多义时宁可不删。"""
    if name in inventory:
        return name
    candidates = [item for item in inventory if name in item or item in name]
    return candidates[0] if len(candidates) == 1 else None


@dataclass(frozen=True)
class Turn:
    """一回合的叙事记忆。四个状态字段装不下"黑衣人正盯着你"，涌现叙事靠这段短记忆维持连贯。"""

    action: str  # 空串表示开局
    scene: str


@dataclass
class Session:
    id: UUID
    state: WorldState
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

    def create(self, state: WorldState, opening_scene: str, present: tuple[str, ...] = ()) -> Session:
        return self._put(uuid4(), state, (Turn("", opening_scene),), present)

    def get_or_rehydrate(self, session_id: UUID, client_state: WorldState) -> Session:
        """服务端会话优先；丢失时以客户端快照冷启动（开发期热重载、进程重启后无缝续玩）。"""
        session = self._sessions.get(session_id)
        if session is None:
            return self._put(session_id, client_state)
        self._sessions.move_to_end(session_id)
        return session

    def _put(
        self, session_id: UUID, state: WorldState, turns: tuple[Turn, ...] = (), present: tuple[str, ...] = ()
    ) -> Session:
        session = Session(session_id, state, deque(turns, maxlen=self._history_turns), present)
        self._sessions[session_id] = session
        while len(self._sessions) > self._capacity:
            self._sessions.popitem(last=False)
        return session
