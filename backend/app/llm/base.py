"""
[INPUT]: 依赖 typing 的 Protocol
[OUTPUT]: 对外提供 LLMClient 协议 —— complete(system, user, schema) -> str、JsonSchema 类型
[POS]: llm 包的抽象边界：director 只依赖此协议，不感知具体厂商；所有客户端（含 Mock）都是它的实现
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from typing import Any, Protocol

JsonSchema = dict[str, Any]


class LLMClient(Protocol):
    """
    纯文本进、纯文本出。schema 是契约提示：支持结构化输出的客户端据此约束采样，不支持的忽略即可——
    无论哪种，最终校验都由 director 的解析闸门完成。
    """

    async def complete(self, system: str, user: str, schema: JsonSchema | None = None) -> str: ...
