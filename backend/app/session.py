"""
[INPUT]: 依赖 app.schemas 的 WorldState / DirectorOutput，依赖 app.errors 的 SessionDeadError / SessionBusyError
[OUTPUT]: 对外提供 Turn、Session（含 acting 守卫与 advance 推进）、SessionStore（LRU 内存仓库）
[POS]: app 的会话状态层，是世界状态的唯一权威；被 director/pipeline.py 读写，不感知 HTTP 与大模型
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from collections import OrderedDict, deque
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from app.errors import SessionBusyError, SessionDeadError
from app.schemas import DirectorOutput, WorldState


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
    dead: bool = False
    busy: bool = False

    def memory_text(self) -> str:
        """近几回合的场景原文，供致死预判检查"某高手是否在场"。"""
        return "\n".join(t.scene for t in self.history)

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
        self.state = out.next_state
        self.history.append(Turn(action, out.scene_description))
        self.dead = out.game_over


class SessionStore:
    """纯内存会话仓库。OrderedDict 实现 LRU：超出容量时淘汰最久未活动的会话。"""

    def __init__(self, capacity: int = 1000, history_turns: int = 4) -> None:
        self._sessions: OrderedDict[UUID, Session] = OrderedDict()
        self._capacity = capacity
        self._history_turns = history_turns

    def create(self, state: WorldState, opening_scene: str) -> Session:
        return self._put(uuid4(), state, Turn("", opening_scene))

    def get_or_rehydrate(self, session_id: UUID, client_state: WorldState) -> Session:
        """服务端会话优先；丢失时以客户端快照冷启动（开发期热重载、进程重启后无缝续玩）。"""
        session = self._sessions.get(session_id)
        if session is None:
            return self._put(session_id, client_state)
        self._sessions.move_to_end(session_id)
        return session

    def _put(self, session_id: UUID, state: WorldState, *turns: Turn) -> Session:
        session = Session(session_id, state, deque(turns, maxlen=self._history_turns))
        self._sessions[session_id] = session
        while len(self._sessions) > self._capacity:
            self._sessions.popitem(last=False)
        return session
