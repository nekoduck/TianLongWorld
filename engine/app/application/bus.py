"""
[INPUT]: 依赖 pydantic v2 的 BaseModel / Field，依赖 domain/intent 的 PlayerIntent，依赖 application/options 的 ActionOption
[OUTPUT]: 对外提供 命令 Command / SpawnPlayer / ResumePlayer（quiet 只重新接上、不复述此景）/ SubmitText / ChooseOption、
          回合消息 SessionOpened / TurnResolved / NarrationDelta / TurnCompleted 与 PlayerStatus（境界 / 伤势 / 武学火候皆为语义标签，
          另有人情 Bond 与心事 Pursuit 两栏，由 application/status 现算）、
          CommandHandler 抽象、CommandBus（按命令类型分派到处理器，返回回合消息的异步流）
[POS]: application 的边界契约：命令进、消息流出。presentation 只认识这里的类型，不认识聚合根、图谱与大模型；
       处理器以异步流回传消息，流式叙事因此是协议的一等公民而非事后补丁。新增命令 = 新命令类 + 新处理器 + 注册一行（开闭）。
       PlayerStatus 只做加法：新栏位一律有缺省值，旧客户端照读
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.application.options import ActionOption
from app.domain.intent import PlayerIntent


# ============================================================
#  命令
# ============================================================
class Command(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SpawnPlayer(Command):
    name: str = Field(min_length=1, max_length=12)
    location: str | None = None  # 投胎地点（id 或地名）；须是图谱里的出生点，留空则由图谱确定性分配


class ResumePlayer(Command):
    player_id: str
    quiet: bool = False  # 只重新接上：不复述此景（不调大模型），只下发当前的选项与状态——断线重连、选项过期时用


class SubmitText(Command):
    player_id: str
    text: str = Field(min_length=1, max_length=200)


class ChooseOption(Command):
    player_id: str
    option_id: str


# ============================================================
#  回合消息
# ============================================================
class _Message(BaseModel):
    model_config = ConfigDict(frozen=True)


class Bond(_Message):
    """人情一栏：对你态度不是漠然的人。"""

    name: str
    attitude: str  # 敌视 / 戒备 / 友善 / 信赖
    cause: str = ""  # 缘由：「你打伤其得意门徒」；不知缘由则空


class Pursuit(_Message):
    """心事一栏：未了的所图。打探的线索只写对象，见闻正文从不上状态栏。"""

    label: str  # 「求艺 · 白虹贯日」「打探 · 左子穆」
    note: str = ""  # 「口风已松；已试：言辞」


class PlayerStatus(_Message):
    """状态栏：只有语义标签，没有数值——熟练度与气血是领域内部的整数，玩家看到的永远是火候与伤势。"""

    name: str
    location: str
    tier: str  # 火候折算后的境界
    health: str  # 伤势：安然无恙 / 轻伤 / 重伤 / 奄奄一息
    alive: bool
    death_cause: str | None = None
    inventory: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()  # 「北冥神功（略有小成）」：武学连同火候
    bonds: tuple[Bond, ...] = ()  # 人情：在场者优先、至多 6 条（status.bonds）
    pursuits: tuple[Pursuit, ...] = ()  # 心事：至多 3 条（status.pursuits）


class SessionOpened(_Message):
    player_id: str
    name: str


class TurnResolved(_Message):
    """命令侧落定：事件已入账、图谱已投影。facts 是本回合事件的白描，先于叙事送达。"""

    intent: PlayerIntent | None
    facts: tuple[str, ...]


class NarrationDelta(_Message):
    text: str


class TurnCompleted(_Message):
    narration: str
    options: tuple[ActionOption, ...]
    status: PlayerStatus
    game_over: bool


type TurnMessage = SessionOpened | TurnResolved | NarrationDelta | TurnCompleted


# ============================================================
#  总线
# ============================================================
class CommandHandler[C: Command](ABC):
    @abstractmethod
    def handle(self, command: C) -> AsyncIterator[TurnMessage]: ...


class CommandBus:
    def __init__(self) -> None:
        self._handlers: dict[type[Command], CommandHandler[Any]] = {}

    def register[C: Command](self, command_type: type[C], handler: CommandHandler[C]) -> None:
        if command_type in self._handlers:
            raise ValueError(f"{command_type.__name__} 已有处理器")
        self._handlers[command_type] = handler

    def dispatch(self, command: Command) -> AsyncIterator[TurnMessage]:
        handler = self._handlers.get(type(command))
        if handler is None:
            raise LookupError(f"没有处理 {type(command).__name__} 的处理器")
        return handler.handle(command)
