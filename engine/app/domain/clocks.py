"""
[INPUT]: 依赖 pydantic v2 的 BaseModel / Field / model_validator，依赖 enum 的 StrEnum，依赖 hashlib 的 sha1
[OUTPUT]: 对外提供 ClockKind（疑心 / 敌意 / 危机 / 进展）、NarrativeClock（叙事时钟实体：id、语义名称、种类、挂在谁身上、进度、阈值、满则如何）、
          clock_id()（挂处 + 名称 → 确定性 id）、CLOCK_SIZES / CLOCKS_MAX / PER_ANCHOR / STEP_MAX / NAME_CHARS / CONSEQUENCE_CHARS 上限、
          started / advanced / retired 三个纯函数（时钟表的写时复制操作，evolve 与内存图谱共用）
[POS]: domain 的「暗流」本体：一件事还没炸开，却在一格一格地逼近——某人的疑心、某人的杀意、某处的危机、一段交情的进展。
       时钟属于玩家的平行世界（由 ClockStarted / ClockAdvanced / ClockCollapsed / ClockCleared 折叠而来），不属于原著正典；
       它挂在一个实体身上（在场之人 chr: / 此地 loc: / 可见之物 itm: / 玩家自己 ply:），快照只召回挂在眼前之物上的那几只。
       名称与满则如何由地下城主推演时动态生成，种类是封闭的四种——满格之后发生什么，领域按种类有一张确定性的坍缩表（resolution.py），
       大模型的措辞只是给这张表配上语义。时钟从不自己走：每一格都是一条入账的事件
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import hashlib
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

CLOCK_SIZES: tuple[int, ...] = (4, 6, 8)  # 阈值只有三档：四格是迫在眉睫，八格是长线暗流
CLOCKS_MAX = 6  # 一个世界同时悬着的时钟上限：再多就不是暗流，是噪音
PER_ANCHOR = 2  # 同一实体身上至多挂两只
STEP_MAX = 3  # 一次推进或回退至多三格（坍缩除外：满了就是满了）
NAME_CHARS = 12
CONSEQUENCE_CHARS = 30


class ClockKind(StrEnum):
    SUSPICION = "疑心"  # 某人对你起疑：满则识破，翻脸，名声受损
    ENMITY = "敌意"  # 某人的怒火或杀意：满则剑拔弩张
    PERIL = "危机"  # 局势或环境的险恶逼近：满则受创，被迫脱身
    PROGRESS = "进展"  # 有利的暗流（一段交情、一桩图谋）：满则如愿以偿的那一步

    @property
    def threat(self) -> bool:
        """对玩家不利的时钟：等价交换的代价可以用它的格数来付。"""
        return self is not ClockKind.PROGRESS


def clock_id(anchor_id: str, name: str) -> str:
    """同一实体身上同名的时钟就是同一只：重提旧事即推进它，而不是再挂一只。"""
    return "clk:" + hashlib.sha1(f"{anchor_id}|{name}".encode()).hexdigest()[:10]


class NarrativeClock(BaseModel):
    """一只悬着的时钟。progress 恒小于 maximum：满格的那一刻它就坍缩并退场（ClockCollapsed），不会以满格的样子留在世上。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^clk:[0-9a-f]{10}$")
    name: str = Field(min_length=1, max_length=NAME_CHARS)
    kind: ClockKind
    anchor_id: str = Field(pattern=r"^(chr|loc|itm|ply):.+")
    progress: int = Field(ge=0)
    maximum: Literal[4, 6, 8]
    consequence: str = Field(default="", max_length=CONSEQUENCE_CHARS)

    @model_validator(mode="after")
    def _below_threshold(self) -> Self:
        if self.progress >= self.maximum:
            raise ValueError(f"时钟「{self.name}」已满（{self.progress}/{self.maximum}）：满格即坍缩退场，不能悬着")
        return self

    @property
    def remaining(self) -> int:
        return self.maximum - self.progress


# ============================================================
#  时钟表的写时复制操作 —— evolve 与内存图谱共用，id 唯一、次序稳定（按 id）
# ============================================================
def started(clocks: tuple[NarrativeClock, ...], clock: NarrativeClock) -> tuple[NarrativeClock, ...]:
    return tuple(sorted((*(c for c in clocks if c.id != clock.id), clock), key=lambda c: c.id))


def advanced(clocks: tuple[NarrativeClock, ...], clock_id: str, steps: int) -> tuple[NarrativeClock, ...]:
    """推进（正）或回退（负），钳在 [0, maximum − 1]：满格由 ClockCollapsed 明写，折叠从不替它坍缩。"""
    return tuple(
        c.model_copy(update={"progress": max(0, min(c.maximum - 1, c.progress + steps))}) if c.id == clock_id else c
        for c in clocks
    )


def retired(clocks: tuple[NarrativeClock, ...], clock_id: str) -> tuple[NarrativeClock, ...]:
    return tuple(c for c in clocks if c.id != clock_id)
