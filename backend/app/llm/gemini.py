"""
[INPUT]: 依赖 pydantic 的 Field，依赖 llm/_http.py 的 post_json / VendorEnvelope / parse_envelope，
         依赖 llm/base.py 的 JsonSchema，依赖 app.errors 的 LLMError
[OUTPUT]: 对外提供 GeminiClient —— 实现 LLMClient 协议
[POS]: llm 包的 Gemini 原生 generateContent 客户端（默认推荐）：以 responseJsonSchema 在 API 层强制导演契约
       （Gemini 直收 Pydantic 原生 schema，无需 schema.py 整形），以 thinkingLevel 换取回合延迟；
       响应经 Pydantic 封套收窄，思考片段、截断、拦截与一切非 STOP 结束在此处各自点名，不让它们伪装成解析失败
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import logging
from typing import Any

from pydantic import Field

from app.errors import LLMError
from app.llm._http import VendorEnvelope, parse_envelope, post_json
from app.llm.base import JsonSchema

logger = logging.getLogger(__name__)

_COMPLETE = (None, "STOP")  # 只有正常结束的正文才交给解析闸门


# ============================================================
#  厂商响应封套 —— 只声明读取的字段，usageMetadata / thoughtSignature 等一律忽略
# ============================================================
class _Part(VendorEnvelope):
    text: str = ""  # functionCall / inlineData 等非文本片段没有 text
    thought: bool = False  # 思考片段：推理草稿，不属于裁决正文


class _Content(VendorEnvelope):
    parts: tuple[_Part, ...] = ()


class _Candidate(VendorEnvelope):
    content: _Content = _Content()  # 被安全策略截停或只产出思考的候选可能没有 content / parts
    finish_reason: str | None = Field(default=None, alias="finishReason")


class _PromptFeedback(VendorEnvelope):
    block_reason: str | None = Field(default=None, alias="blockReason")


class _GenerateContentResponse(VendorEnvelope):
    candidates: tuple[_Candidate, ...] = ()
    prompt_feedback: _PromptFeedback = Field(default=_PromptFeedback(), alias="promptFeedback")


class GeminiClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        temperature: float | None,
        timeout: float,
        max_tokens: int,
        thinking_level: str | None,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/models/{model}:generateContent"
        self._headers = {"x-goog-api-key": api_key}  # 走请求头而非 ?key=，密钥不会出现在任何 URL 日志里
        self._temperature = temperature
        self._timeout = timeout
        self._max_tokens = max_tokens
        self._thinking_level = thinking_level

    async def complete(self, system: str, user: str, schema: JsonSchema) -> str:
        config: dict[str, Any] = {
            # 结构化输出：MIME 锁定 JSON + schema 约束采样，从源头杜绝 Markdown 外壳与寒暄
            "responseMimeType": "application/json",
            "responseJsonSchema": schema,
            "maxOutputTokens": self._max_tokens,
        }
        if self._temperature is not None:
            config["temperature"] = self._temperature
        if self._thinking_level:
            config["thinkingConfig"] = {"thinkingLevel": self._thinking_level}

        payload: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": config,
        }
        data = await post_json(self._url, headers=self._headers, payload=payload, timeout=self._timeout)
        return _extract_text(parse_envelope(_GenerateContentResponse, data))


def _extract_text(resp: _GenerateContentResponse) -> str:
    if not resp.candidates:
        reason = resp.prompt_feedback.block_reason or "无候选"
        raise LLMError(f"天机遮蔽：大模型拒绝作答（{reason}）")
    candidate = resp.candidates[0]
    if candidate.finish_reason == "MAX_TOKENS":
        # 思考 token 同样计入 maxOutputTokens：截断的 JSON 注定通不过解析闸门，在此点名真因
        logger.warning("Gemini 输出触顶 maxOutputTokens：请调高 LLM_MAX_TOKENS 或调低 LLM_THINKING_LEVEL")
        raise LLMError("天机中断：大模型输出被截断（finishReason=MAX_TOKENS）")
    if candidate.finish_reason not in _COMPLETE:
        # SAFETY / RECITATION / PROHIBITED_CONTENT……：残缺正文不是幻觉，不该被当作格式错误重采样后悄悄停顿
        raise LLMError(f"天机遮蔽：大模型非正常结束（finishReason={candidate.finish_reason}）")
    text = "".join(part.text for part in candidate.content.parts if not part.thought)
    if not text:
        raise LLMError(f"天机遮蔽：大模型未返回正文（finishReason={candidate.finish_reason}）")
    return text
