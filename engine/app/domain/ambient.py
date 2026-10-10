"""
[INPUT]: 依赖 pydantic v2 的 BaseModel / Field / model_validator，依赖 enum 的 StrEnum，依赖 hashlib 的 sha1
[OUTPUT]: 对外提供 此世的环境状态——ActivityKind（交手 / 溃散逃离）、ActivityState（进行中 / 已结束）、Activity（活动：参与者、地点、起讫刻、留下的痕迹）、
          EnvironmentalTrace（环境痕迹：地点、描述、出生刻、几刻消散）、FactToken（可寻址的消息：正文、主体、发源地、出生刻、每刻几处、传多远、已传到之处）、
          activity_id / trace_id / token_id 确定性 id、ACTIVITIES_MAX / TRACES_MAX / TOKENS_MAX / RADIUS_MAX / DECAY_MAX / TRACE_CHARS / TOKEN_CHARS 上限，
          以及 evolve 与内存图谱共用的写时复制操作 with_activity / with_trace / with_token / reached / elapse
[POS]: domain 的「过去式」本体：发生过的事不随玩家走开而蒸发——一场交手是此地一个已结束的活动，地上的血迹是一道几刻后才消散的痕迹，
       而这件事本身是一枚消息，从发源地沿路一刻一两处地传出去。三者都属于玩家的平行世界（由事件折叠而来），不属于正典：
       活动的「进行中 / 已结束」由此刻的 tick 现算，已结束的活动随它的痕迹一同消散；消息只增不减，在场之人知道的只是传到此地的那几枚（局部认知）。
       与 clocks.py 同构：时钟是"将要发生"的暗流，本模块是"已经发生"的余波
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import hashlib
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

ACTIVITIES_MAX = 12  # 一个世界同时记着的活动：再多就该是史书，不是此地的余波
TRACES_MAX = 12
TOKENS_MAX = 16  # 消息只留最近的十六枚：旧闻淡出
RADIUS_MAX = 6  # 一枚消息至多传出六处
DECAY_MAX = 4 * 96  # 痕迹至多留四日
TRACE_CHARS = 24
TOKEN_CHARS = 40


class ActivityKind(StrEnum):
    FIGHT = "交手"  # 玩家与某人动手：一刻即止，此后是此地「已结束」的往事，随血迹或狼藉一同消散
    ROUT = "溃散逃离"  # 人群受惊四散：进行中即人群不在原处做原来的事，结束即散去的人回来了


class ActivityState(StrEnum):
    ONGOING = "进行中"
    ENDED = "已结束"


def _digest(*parts: object) -> str:
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:10]


def activity_id(kind: ActivityKind, location_id: str, participants: tuple[str, ...], started_tick: int) -> str:
    return "act:" + _digest(kind, location_id, ",".join(participants), started_tick)


def trace_id(location_id: str, description: str, born_tick: int) -> str:
    return "trc:" + _digest(location_id, description, born_tick)


def token_id(text: str, origin_id: str, born_tick: int) -> str:
    return "tok:" + _digest(text, origin_id, born_tick)


class _Ambient(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Activity(_Ambient):
    """此地的一件事：谁、在哪、何时起、何时止。state 不存——tick 未到 ends_tick 即进行中，过了即已结束。"""

    id: str = Field(pattern=r"^act:[0-9a-f]{10}$")
    kind: ActivityKind
    participants: tuple[str, ...] = Field(min_length=1)  # chr: / ply: / swm:
    location_id: str = Field(pattern=r"^loc:.+")
    started_tick: int = Field(ge=0)
    ends_tick: int = Field(ge=0)
    trace_id: str | None = Field(default=None, pattern=r"^trc:[0-9a-f]{10}$")  # 结束之后，它随这道痕迹一同消散

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.ends_tick < self.started_tick:
            raise ValueError(f"活动 {self.id} 止于开始之前（{self.started_tick} → {self.ends_tick}）")
        return self

    def state(self, tick: int) -> ActivityState:
        return ActivityState.ONGOING if tick < self.ends_tick else ActivityState.ENDED


class EnvironmentalTrace(_Ambient):
    """一道痕迹：描述是旁人一眼看得见的样子（「地上点点血迹」），decay_ticks 刻之后消散。"""

    id: str = Field(pattern=r"^trc:[0-9a-f]{10}$")
    location_id: str = Field(pattern=r"^loc:.+")
    description: str = Field(min_length=1, max_length=TRACE_CHARS)
    born_tick: int = Field(ge=0)
    decay_ticks: int = Field(ge=1, le=DECAY_MAX)

    @property
    def expires_tick(self) -> int:
        return self.born_tick + self.decay_ticks

    def remaining(self, tick: int) -> int:
        return max(0, self.expires_tick - tick)


class FactToken(_Ambient):
    """
    一枚可寻址的消息：一件公开发生过的事（「某某把龚光杰打成重伤」），从发源地沿 CONNECTS_TO 每刻传开 speed 处、至多 radius 跳。
    reached 按传到的先后排列、恒以发源地开头——它就是从发源地广度优先展开的那张次序表的前缀，下一刻传到哪里因此是确定的。
    """

    id: str = Field(pattern=r"^tok:[0-9a-f]{10}$")
    text: str = Field(min_length=1, max_length=TOKEN_CHARS)
    subject_ids: tuple[str, ...] = ()
    origin_id: str = Field(pattern=r"^loc:.+")
    born_tick: int = Field(ge=0)
    speed: int = Field(ge=1, le=2)  # 每刻传开几处：有人群目睹的消息走得快
    radius: int = Field(ge=0, le=RADIUS_MAX)  # 传出几跳为止
    reached: tuple[str, ...] = ()
    signature: tuple[str, ...] = ()  # 消息里那人的模样（玩家所为时是他当时外露的特质）：听到消息的人凭它认人；旧账缺省为空

    @model_validator(mode="after")
    def _from_origin(self) -> Self:
        if not self.reached or self.reached[0] != self.origin_id:
            raise ValueError(f"消息 {self.id} 须从发源地 {self.origin_id} 传起")
        if len(set(self.reached)) != len(self.reached):
            raise ValueError(f"消息 {self.id} 重复传到同一处")
        return self


# ============================================================
#  写时复制 —— evolve 与内存图谱共用；超上限请走最旧的，次序按 id 稳定
# ============================================================
def with_activity(activities: tuple[Activity, ...], activity: Activity) -> tuple[Activity, ...]:
    kept = sorted((*(a for a in activities if a.id != activity.id), activity),
                  key=lambda a: (a.started_tick, a.id), reverse=True)[:ACTIVITIES_MAX]
    return tuple(sorted(kept, key=lambda a: a.id))


def with_trace(traces: tuple[EnvironmentalTrace, ...], trace: EnvironmentalTrace) -> tuple[EnvironmentalTrace, ...]:
    kept = sorted((*(t for t in traces if t.id != trace.id), trace),
                  key=lambda t: (t.born_tick, t.id), reverse=True)[:TRACES_MAX]
    return tuple(sorted(kept, key=lambda t: t.id))


def with_token(tokens: tuple[FactToken, ...], token: FactToken) -> tuple[FactToken, ...]:
    kept = sorted((*(t for t in tokens if t.id != token.id), token),
                  key=lambda t: (t.born_tick, t.id), reverse=True)[:TOKENS_MAX]
    return tuple(sorted(kept, key=lambda t: t.id))


def reached(tokens: tuple[FactToken, ...], token_id: str, location_ids: tuple[str, ...]) -> tuple[FactToken, ...]:
    """消息又传到几处（按先后追加、去重）；不存在的消息无事发生。"""
    return tuple(
        t.model_copy(update={"reached": (*t.reached, *(x for x in dict.fromkeys(location_ids) if x not in t.reached))})
        if t.id == token_id else t
        for t in tokens
    )


def elapse(
    activities: tuple[Activity, ...], traces: tuple[EnvironmentalTrace, ...], tick: int
) -> tuple[tuple[Activity, ...], tuple[EnvironmentalTrace, ...]]:
    """时间走到 tick：到期的痕迹消散；已结束的活动若没有痕迹、或它的痕迹已经消散，一同抹去。先痕迹、后活动。"""
    alive = tuple(t for t in traces if t.expires_tick > tick)
    left = {t.id for t in alive}
    kept = tuple(a for a in activities if tick < a.ends_tick or (a.trace_id is not None and a.trace_id in left))
    return kept, alive
