"""
[INPUT]: 依赖 application/ports 的 LLMClient / JsonSchema，依赖 app.errors 的 LLMError
[OUTPUT]: 对外提供 CallBudget（一个进程内各职责共用的调用次数保险丝）与 BudgetedLLM（给任一 LLMClient 套上保险丝的装饰器）
[POS]: llm 包的计费护栏：真实厂商按次收钱，跑空预付额度只需要一个失控的循环或一批没算过账的基准测试（2026-10 实测时真出过这事）。
       保险丝只数"发出去的请求"，熔断后抛不可重试的 LLMError——各调用方早已为它备好退路：地下城主交给规则、叙事降级为白描、
       意图解析把错误透传成错误帧（回合不写任何事件）、抽取即止。它不懂价格、不区分模型：要的是兜底，不是记账
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from collections.abc import AsyncIterator

from app.application.ports import JsonSchema, LLMClient
from app.errors import LLMError


class CallBudget:
    """limit 为 0 即不设上限。同一进程里的各个职责共用一份：省下来的是同一笔钱。"""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.spent = 0

    def spend(self, model: str) -> None:
        if self.limit and self.spent >= self.limit:
            raise LLMError(
                f"本进程的大模型调用已达上限 LLM_CALL_LIMIT={self.limit}（{model} 未发出）：重启进程或在 engine/.env 调高",
                retryable=False,
            )
        self.spent += 1


class BudgetedLLM(LLMClient):
    """装饰器：每次 complete / stream 先过保险丝，再交给真正的客户端。厂商内部的退避重试不另计——那些请求多半不计费。"""

    def __init__(self, inner: LLMClient, budget: CallBudget, model: str) -> None:
        self._inner = inner
        self._budget = budget
        self._model = model

    async def complete(self, system: str, user: str, schema: JsonSchema | None = None) -> str:
        self._budget.spend(self._model)
        return await self._inner.complete(system, user, schema)

    async def stream(self, system: str, user: str) -> AsyncIterator[str]:
        self._budget.spend(self._model)
        async for chunk in self._inner.stream(system, user):
            yield chunk
