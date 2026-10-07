"""
[INPUT]: 依赖 httpx2 的 AsyncClient，依赖 app.errors 的 LLMError
[OUTPUT]: 对外提供 post_json() —— 发出 JSON POST，把一切传输/状态码异常收敛为 LLMError
[POS]: llm 包的私有传输层，被 openai_compat.py 与 anthropic.py 共用，让厂商客户端只关心报文形状
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import logging
from typing import Any

import httpx2

from app.errors import LLMError

logger = logging.getLogger(__name__)


async def post_json(url: str, *, headers: dict[str, str], payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    # 每次调用新建连接：大模型延迟以秒计，连接池省下的毫秒不值得引入生命周期管理
    try:
        async with httpx2.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, headers=headers, json=payload)
    except httpx2.TimeoutException as exc:
        raise LLMError("天机迟滞：大模型响应超时") from exc
    except httpx2.HTTPError as exc:
        logger.error("大模型连接失败 %s：%s", url, exc)
        raise LLMError("天机断绝：无法连接大模型服务") from exc

    if resp.status_code >= 400:
        # 上游报文可能含账户信息：完整内容只进日志，玩家只看到状态码
        logger.error("大模型返回 HTTP %d：%.500s", resp.status_code, resp.text)
        raise LLMError(f"天机紊乱：大模型返回 HTTP {resp.status_code}")
    try:
        return resp.json()
    except ValueError as exc:
        raise LLMError("天机紊乱：大模型响应不是合法 JSON") from exc
