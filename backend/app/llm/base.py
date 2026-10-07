"""
[INPUT]: 依赖 typing 的 Protocol
[OUTPUT]: 对外提供 LLMClient 协议 —— complete(system, user) -> str
[POS]: llm 包的抽象边界：director 只依赖此协议，不感知具体厂商；所有客户端（含 Mock）都是它的实现
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from typing import Protocol


class LLMClient(Protocol):
    """纯文本进、纯文本出。结构化解析是 director 的职责，不是客户端的。"""

    async def complete(self, system: str, user: str) -> str: ...
