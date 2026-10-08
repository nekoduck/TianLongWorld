"""
[INPUT]: 依赖 fastapi 的 APIRouter / WebSocket / WebSocketDisconnect，依赖 pydantic 的 ValidationError，
         依赖 presentation/protocol 的帧解析与编码，依赖 application/bus 的 CommandBus / SessionOpened，依赖 app.errors 的 EngineError
[OUTPUT]: 对外提供 router（WebSocket /ws/play）
[POS]: presentation 的唯一入口：一条连接即一位玩家的会话。[Receive] 收帧 → 译为命令 → 总线分派 → 把回合消息流逐帧推回（叙事逐片流式）。
       零业务逻辑；一帧出错只回 error 帧，连接不断——玩家可以立刻换个说法再来
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app.application.bus import CommandBus, SessionOpened
from app.errors import EngineError
from app.presentation.protocol import CLIENT_FRAME, ProtocolError, error_frame, to_command, to_frame

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws/play")
async def play(ws: WebSocket) -> None:
    await ws.accept()
    bus: CommandBus = ws.app.state.container.bus
    player_id: str | None = None
    while True:
        try:
            raw = await ws.receive_json()
        except WebSocketDisconnect:
            return
        except ValueError:
            await ws.send_json(error_frame(ProtocolError.code, "帧不是合法的 JSON。"))
            continue
        try:
            command = to_command(CLIENT_FRAME.validate_python(raw), player_id)
            async for message in bus.dispatch(command):
                if isinstance(message, SessionOpened):
                    player_id = message.player_id
                await ws.send_json(to_frame(message))
        except WebSocketDisconnect:
            return
        except ValidationError as exc:
            await ws.send_json(error_frame(ProtocolError.code, f"帧格式不合协议：{exc.errors()[0]['msg']}"))
        except EngineError as exc:
            await ws.send_json(error_frame(exc.code, exc.message))
        except Exception:  # 未知错误不能杀死连接；真相在事件流里，下一帧照常可玩
            logger.exception("处理帧失败：%s", raw)
            await ws.send_json(error_frame("INTERNAL", "天机紊乱，请稍后再试。"))
