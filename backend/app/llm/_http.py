"""
[INPUT]: 依赖 httpx2 的 AsyncClient，依赖 pydantic 的 BaseModel / ConfigDict / ValidationError，依赖 app.errors 的 LLMError
[OUTPUT]: 对外提供 post_json() —— 发出 JSON POST，返回未经校验的上游 JSON（object）；
          VendorEnvelope 厂商响应封套基类；parse_envelope() —— 把上游 JSON 收窄为封套模型
[POS]: llm 包的私有传输层，被 gemini.py / openai_compat.py / anthropic.py 共用。两道关口：
       post_json 收敛传输与状态码异常，parse_envelope 收敛报文形状异常 —— 一切上游失败都变成 LLMError，
       厂商 JSON 不以 Any 流入业务代码，厂商客户端只关心报文语义
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import logging
from typing import Any, TypeVar

import httpx2
from pydantic import BaseModel, ConfigDict, ValidationError

from app.errors import LLMError

logger = logging.getLogger(__name__)


class VendorEnvelope(BaseModel):
    """
    厂商响应封套基类：只声明客户端真正读取的字段。
    冻结保证不可变；extra="ignore" 而非 forbid —— 厂商会不断追加字段（usage、id、签名……），
    契约的严格性属于导演输出，不属于我们无权约束的厂商报文。
    """

    model_config = ConfigDict(frozen=True, extra="ignore")


EnvelopeT = TypeVar("EnvelopeT", bound=VendorEnvelope)


async def post_json(url: str, *, headers: dict[str, str], payload: dict[str, Any], timeout: float) -> object:
    """返回值是 object 而非 dict：上游 JSON 在经过封套校验之前，形状一概不可信。"""
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
        data: object = resp.json()
    except ValueError as exc:
        raise LLMError("天机紊乱：大模型响应不是合法 JSON") from exc
    return data


def parse_envelope(model: type[EnvelopeT], data: object) -> EnvelopeT:
    """把上游 JSON 收窄为厂商封套；形状不符（缺字段、类型错、根本不是对象）一律收敛为 LLMError。"""
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        logger.error("大模型响应形状不符 %s：%.500s", model.__name__, exc)
        raise LLMError("天机紊乱：大模型响应结构异常") from exc
