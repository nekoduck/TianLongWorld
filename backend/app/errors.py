"""
[INPUT]: 无外部依赖
[OUTPUT]: 对外提供 GameError 基类及 SessionDeadError / SessionBusyError / DirectorError / LLMError
[POS]: app 的统一错误谱系，领域层只抛这些异常，main.py 以单一 handler 映射为 HTTP {"detail": ...}
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""


class GameError(Exception):
    """所有可预期的游戏错误。status_code 决定 HTTP 状态，message 直接呈现给玩家。"""

    status_code: int = 500

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class SessionDeadError(GameError):
    """永久死亡：死者不能再行动。服务端守住这条线，前端锁死只是表象。"""

    status_code = 409


class SessionBusyError(GameError):
    """同一会话上一招尚未落定，拒绝并发推演，防止世界线分叉。"""

    status_code = 409


class DirectorError(GameError):
    """大模型输出无法解析为合法的导演裁决（重试耗尽后抛出）。"""

    status_code = 502


class LLMError(GameError):
    """大模型调用本身失败：网络、鉴权、限流、响应结构异常。"""

    status_code = 502
