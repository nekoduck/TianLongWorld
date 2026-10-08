"""
[INPUT]: 无外部依赖
[OUTPUT]: 对外提供 EngineError 基类及 UnknownPlayerError / PlayerDeadError / ConcurrencyError / OptionExpiredError /
          WorldNotSeededError / ProjectionError / LLMError / ExtractionError
[POS]: 引擎的共享内核——全部分层共用的唯一错误谱系；领域层只抛这些异常，presentation 以 code 字段映射为协议错误帧
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""


class EngineError(Exception):
    """一切可预期的引擎错误。code 是机器可读的稳定标识，message 直接呈现给玩家。"""

    code: str = "ENGINE_ERROR"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class UnknownPlayerError(EngineError):
    """事件流里没有这位玩家：未投胎，或 player_id 伪造。"""

    code = "UNKNOWN_PLAYER"


class PlayerDeadError(EngineError):
    """永久死亡：死者的事件流只读，任何命令在落入裁决之前即被拒绝。"""

    code = "PLAYER_DEAD"


class ConcurrencyError(EngineError):
    """乐观并发冲突：expected_version 与流的当前版本不符——同一世界线不允许分叉。"""

    code = "CONCURRENCY_CONFLICT"


class OptionExpiredError(EngineError):
    """所选选项不在当前快照推导出的合法选项之中（过期或伪造）。"""

    code = "OPTION_EXPIRED"


class WorldNotSeededError(EngineError):
    """图谱里没有原著本体：尚未执行 World Seeding，引擎拒绝凭空开局。"""

    code = "WORLD_NOT_SEEDED"


class ProjectionError(EngineError):
    """投影落后或损坏且无法自愈。事件流仍是真相，重放即可重建。"""

    code = "PROJECTION_ERROR"


class LLMError(EngineError):
    """大模型调用失败：网络、鉴权、限流、拒答、响应结构异常。retryable 为 False 的（鉴权、欠费、请求非法）重试也无济于事。"""

    code = "LLM_ERROR"

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class ExtractionError(EngineError):
    """原著解析管道失败：语料缺失、编码无法识别、抽取结果无法组装为合法蓝图。"""

    code = "EXTRACTION_ERROR"
