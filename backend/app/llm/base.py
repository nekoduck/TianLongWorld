"""
[INPUT]: 依赖 typing 的 Any / Protocol
[OUTPUT]: 对外提供 LLMClient 协议 —— complete(system, user, schema) -> str，schema 必填；JsonSchema 类型别名
[POS]: llm 包的抽象边界：director 只依赖此协议，不感知具体厂商；所有客户端（含 Mock）都是它的实现。
       schema 必填是刻意的：结构化输出是每次调用的硬约束而非可选提示，漏传在类型检查期就会暴露
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from typing import Any, Protocol

# JSON Schema 天然是任意嵌套的 JSON：别名只在契约的内外传递，形状由 Pydantic 生成、由 schema.py 整形
JsonSchema = dict[str, Any]


class LLMClient(Protocol):
    """
    纯文本进、纯文本出，schema 是必须兑现的输出契约，两道防线各司其职：

    - API 层（第一道）：每个真实客户端都必须在 API 层强制执行该 schema —— Gemini 的 responseJsonSchema、
      OpenAI 的 json_schema 严格模式、Anthropic 的 output_config.format —— 从采样层面杜绝格式漂移、Markdown 外壳与自然语言填充；
      端点能力不足时（如只支持 json_object 的兼容端点）至少保证输出是合法 JSON。
    - 解析闸门（第二道）：返回值仍只是文本，最终由 director 的解析闸门以 Pydantic 校验。
      API 层约束会裁掉长度等校验型关键字，语义规则（如存活必有选项）也只有 Pydantic 能守住，所以闸门永不省略。

    任何调用失败（传输、截断、拒答、响应形状异常）都必须收敛为 app.errors.LLMError，不得泄漏厂商异常。
    """

    async def complete(self, system: str, user: str, schema: JsonSchema) -> str: ...
