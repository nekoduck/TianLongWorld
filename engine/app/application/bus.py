"""
[INPUT]: 依赖 pydantic v2 的 BaseModel / Field，依赖 domain/intent 的 PlayerIntent，依赖 application/options 的 ActionOption，
         依赖 application/navigation 的 NavigationOption
[OUTPUT]: 对外提供 命令 Command / SpawnPlayer / ResumePlayer（quiet 只重新接上、不复述此景）/ SubmitText / ChooseOption（option_id 是交互选项或导航项的 id）、
          回合消息 SessionOpened / TurnResolved / NarrationDelta / TurnCompleted（options 交互选项 3~4 席 + navigation 方位导航，两者分开下发）与 PlayerStatus（境界 / 伤势 / 武学火候 / 名望皆为语义标签，
          另有人情 Bond、心事 Pursuit 两栏与眼前的叙事时钟 ClockInfo，由 application/status 现算；时辰 time「第一日·辰正」取自快照）、
          CommandHandler 抽象、CommandBus（按命令类型分派到处理器，返回回合消息的异步流）
[POS]: application 的边界契约：命令进、消息流出。presentation 只认识这里的类型，不认识聚合根、图谱与大模型；
       处理器以异步流回传消息，流式叙事因此是协议的一等公民而非事后补丁。新增命令 = 新命令类 + 新处理器 + 注册一行（开闭）。
       PlayerStatus 只做加法：新栏位一律有缺省值，旧客户端照读；时辰是世界心跳的读数——每条命令都花时间，状态栏据此报时。
       意图风味封装：TurnCompleted.options 的每一席是 ActionOption（玩家看见 flavor_text，服务端执行 underlying_command），
       移动从交互选项里剥离为 TurnCompleted.navigation（每条获准的出路一项：方位、去处或「未知区域」、交通方式、耗时、认知、是否脱身之路）；
       两者的指令都只在服务端，点选只带 id
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.application.navigation import NavigationOption
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
    option_id: str  # 交互选项（ActionOption.id）或导航项（NavigationOption.id，nav- 开头）


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


class ClockInfo(_Message):
    """眼前的一只叙事时钟：语义名称、种类与格数。id 与挂处的 id 从不下发——前端只画进度条。"""

    name: str  # 「钟灵的戒心」
    kind: str  # 疑心 / 敌意 / 危机 / 进展
    progress: int
    maximum: int  # 4 / 6 / 8


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
    clocks: tuple[ClockInfo, ...] = ()  # 眼前的暗流：至多 4 只（status.clocks）
    renown: str = ""  # 名望的语义标签（籍籍无名……）；空即旧服务端未填
    time: str = ""  # 时辰「第一日·辰正」（快照的 time_label；死者停在最后时刻）；空即旧服务端未填
    traits: tuple[str, ...] = ()  # 命格特质（相貌 / 口音 / 装束 / 印记）：旁人眼里的你（status.traits）；空即旧服务端未填


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
    options: tuple[ActionOption, ...]  # 交互选项 3~4 席：说书人挑中并配了风味的，不足由退路菜单补（朴素标签）
    status: PlayerStatus
    game_over: bool
    navigation: tuple[NavigationOption, ...] = ()  # 方位导航：每条获准的出路一项，死者没有


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
