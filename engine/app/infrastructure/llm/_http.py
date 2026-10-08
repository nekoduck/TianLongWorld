"""
[INPUT]: 依赖 httpx2 的 AsyncClient（post / stream），依赖 app.errors 的 LLMError
[OUTPUT]: 对外提供 post_json()（一次性 JSON POST）、stream_sse()（Server-Sent Events 逐条解析为 dict）
[POS]: llm 包的私有传输层，被三家厂商客户端共用：超时 / 连接失败 / 4xx5xx / 非 JSON 全部收敛为 LLMError，
       上游报文只进日志不进玩家视野（可能含账户信息）；厂商客户端因此只关心报文形状
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import json
import logging
from collections.abc import AsyncIterator
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
        logger.error("大模型返回 HTTP %d：%.500s", resp.status_code, resp.text)
        raise LLMError(f"天机紊乱：大模型返回 HTTP {resp.status_code}")
    try:
        data: dict[str, Any] = resp.json()
    except ValueError as exc:
        raise LLMError("天机紊乱：大模型响应不是合法 JSON") from exc
    return data


async def stream_sse(
    url: str, *, headers: dict[str, str], payload: dict[str, Any], timeout: float
) -> AsyncIterator[dict[str, Any]]:
    """逐条吐出 SSE 的 data 负载；[DONE] 哨兵与心跳注释行被吞掉，坏行只记日志不打断流。"""
    try:
        async with (
            httpx2.AsyncClient(timeout=timeout) as client,
            client.stream("POST", url, headers=headers, json=payload) as resp,
        ):
            if resp.status_code >= 400:
                body = await resp.aread()
                logger.error("大模型流式返回 HTTP %d：%.500s", resp.status_code, body.decode(errors="replace"))
                raise LLMError(f"天机紊乱：大模型返回 HTTP {resp.status_code}")
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    yield json.loads(data)
                except ValueError:
                    logger.warning("丢弃无法解析的 SSE 行：%.200s", data)
    except httpx2.TimeoutException as exc:
        raise LLMError("天机迟滞：大模型响应超时") from exc
    except httpx2.HTTPError as exc:
        logger.error("大模型流式连接失败 %s：%s", url, exc)
        raise LLMError("天机断绝：无法连接大模型服务") from exc
