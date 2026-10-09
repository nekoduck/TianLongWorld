"""
[INPUT]: 依赖 pydantic v2 的 BaseModel / TypeAdapter / Field(discriminator)，依赖 application/bus 的命令与回合消息
[OUTPUT]: 对外提供 客户端帧 SpawnFrame / ResumeFrame（quiet：断线重连只重新接上）/ ActFrame / ChooseFrame 与 CLIENT_FRAME 判别联合解析器、
          to_command()（帧 → 命令）、to_frame()（回合消息 → 服务端 JSON 帧）、error_frame()、ProtocolError
[POS]: presentation 的线协议：WebSocket 上传什么、回什么只在这里定义。服务端帧：session / turn_resolved / narration_delta /
       turn_completed / error。选项只下发 id、标签、方向、why（上榜缘由，≤12 字）与 risk（风险档 稳妥 / 有险 / 凶险，只露区间最坏一端，有才下发），
       意图留在服务端——前端无从伪造指令，只能点选；turn_completed 的 status 只有语义标签（境界 tier、伤势 health、武学连同火候、
       人情 bonds {name, attitude, cause}、心事 pursuits {label, note}、名望 renown、眼前的暗流 clocks {name, kind, progress, maximum}），
       熟练度、气血与名望的整数从不下发；时钟的格数是叙事的节拍而非属性，照下发（id 与挂处不下发）。
       与 frontend/src/engineTypes.ts 逐字段镜像；新字段只做加法，旧客户端照读
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.application.bus import (
    ChooseOption,
    Command,
    NarrationDelta,
    ResumePlayer,
    SessionOpened,
    SpawnPlayer,
    SubmitText,
    TurnCompleted,
    TurnMessage,
    TurnResolved,
)
from app.errors import EngineError


class ProtocolError(EngineError):
    code = "BAD_FRAME"


class _Frame(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SpawnFrame(_Frame):
    type: Literal["spawn"]
    name: str
    location: str | None = None


class ResumeFrame(_Frame):
    type: Literal["resume"]
    player_id: str
    quiet: bool = False  # 断线重连：不复述此景、不调大模型，只回 session 与带选项和状态的终帧（叙事为空）


class ActFrame(_Frame):
    type: Literal["act"]
    text: str


class ChooseFrame(_Frame):
    type: Literal["choose"]
    option_id: str


CLIENT_FRAME: TypeAdapter[SpawnFrame | ResumeFrame | ActFrame | ChooseFrame] = TypeAdapter(
    Annotated[SpawnFrame | ResumeFrame | ActFrame | ChooseFrame, Field(discriminator="type")]
)


def to_command(frame: SpawnFrame | ResumeFrame | ActFrame | ChooseFrame, player_id: str | None) -> Command:
    match frame:
        case SpawnFrame(name=name, location=location):
            return SpawnPlayer(name=name, location=location)
        case ResumeFrame(player_id=pid, quiet=quiet):
            return ResumePlayer(player_id=pid, quiet=quiet)
    if player_id is None:
        raise ProtocolError("尚未入世：请先发送 spawn 或 resume 帧。")
    if isinstance(frame, ActFrame):
        return SubmitText(player_id=player_id, text=frame.text)
    return ChooseOption(player_id=player_id, option_id=frame.option_id)


def to_frame(message: TurnMessage) -> dict[str, Any]:
    match message:
        case SessionOpened():
            return {"type": "session", **message.model_dump(mode="json")}
        case TurnResolved():
            return {"type": "turn_resolved", **message.model_dump(mode="json")}
        case NarrationDelta():
            return {"type": "narration_delta", **message.model_dump(mode="json")}
        case TurnCompleted():
            body = message.model_dump(mode="json", exclude={"options"})
            body["options"] = [
                o.model_dump(mode="json", include={"id", "label", "category", "why", "risk"}, exclude_none=True)
                for o in message.options
            ]
            return {"type": "turn_completed", **body}
    raise TypeError(f"未知的回合消息：{type(message).__name__}")


def error_frame(code: str, message: str) -> dict[str, Any]:
    return {"type": "error", "code": code, "message": message}
