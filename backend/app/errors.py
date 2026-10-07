"""
[INPUT]: 无外部依赖
[OUTPUT]: 对外提供 GameError 基类及 SessionDeadError / SessionBusyError / NotFoundError / DirectorError / LLMError
[POS]: app 的统一错误谱系，领域层只抛这些异常，main.py 以单一 handler 映射为 HTTP {"detail": ..., "code": ...}；
       code 是给前端的机器可读语义（如 dead 让前端直接进入投胎界面），detail 直接呈现给玩家
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""


class GameError(Exception):
    """所有可预期的游戏错误。status_code 决定 HTTP 状态，code 供前端分支，message 直接呈现给玩家。"""

    status_code: int = 500
    code: str = "error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class SessionDeadError(GameError):
    """永久死亡：死者不能再行动。死亡写在事件日志里，服务端守住这条线，前端锁死只是表象。"""

    status_code = 409
    code = "dead"


class SessionBusyError(GameError):
    """同一条命上一招尚未落定（或并发追加冲突），拒绝并发推演，防止世界线分叉。"""

    status_code = 409
    code = "busy"


class NotFoundError(GameError):
    """会话或世界不存在：服务端只认事件日志，从不凭客户端快照凭空建档。"""

    status_code = 404
    code = "not_found"


class DirectorError(GameError):
    """大模型输出无法解析为合法的导演裁决（重试耗尽后由编排器转为确定性兜底，正常不会外泄）。"""

    status_code = 502
    code = "director"

    def __init__(self, message: str, hints: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.hints = hints  # 校验失败的要点，重采样时回灌给大模型，避免盲目重试


class LLMError(GameError):
    """大模型调用本身失败：网络、鉴权、限流、拒答、输出被截断、响应结构异常。"""

    status_code = 502
    code = "llm_unavailable"
