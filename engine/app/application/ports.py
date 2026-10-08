"""
[INPUT]: 依赖 abc 的 ABC / abstractmethod
[OUTPUT]: 对外提供 LLMClient 抽象（complete 一次性补全 + stream 流式补全）、JsonSchema 别名
[POS]: application 拥有的大模型端口：意图解析器、叙事渲染器、原著解析管道只认这个抽象，不感知厂商。
       大模型在引擎里只有三种无状态职责（抽取原著、解析意图、渲染文本），没有一种能写状态——端口上也就没有任何写方法
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

type JsonSchema = dict[str, Any]


class LLMClient(ABC):
    @abstractmethod
    async def complete(self, system: str, user: str, schema: JsonSchema | None = None) -> str:
        """纯文本进、纯文本出。schema 是契约：支持结构化输出的厂商据此约束采样，最终校验永远由调用方的 Pydantic 完成。"""

    async def stream(self, system: str, user: str) -> AsyncIterator[str]:
        """流式补全。默认把一次性补全当作单个分片吐出——不支持流式的实现无需改写即可替换（里氏替换）。"""
        yield await self.complete(system, user)
