"""
[INPUT]: 依赖标准库 json，依赖 pydantic 的 ValidationError，依赖 app.schemas 的 DirectorOutput，依赖 app.errors 的 DirectorError
[OUTPUT]: 对外提供 parse_director_output() —— 原始文本 → 经校验的 DirectorOutput
[POS]: director 的解析闸门，是大模型输出与强类型契约之间的最后一道关：厂商层的严格 schema 是第一道防线，
       这里兜住残余漂移；失败时抛出带校验要点（hints）的 DirectorError，供编排器回灌重采样
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import json

from pydantic import ValidationError

from app.errors import DirectorError
from app.schemas import DirectorOutput

_DECODER = json.JSONDecoder()
_MAX_HINTS = 6


def parse_director_output(raw: str) -> DirectorOutput:
    """
    从首个 "{" 起按 JSON 语法解码出第一个完整对象——Markdown 围栏与前后寒暄被跳过，
    尾随文本里即便另有花括号也不会误判；其余交给 Pydantic：字段、类型、长度、禁止多余字段、存活必有选项。
    """
    start = raw.find("{")
    if start < 0:
        raise DirectorError("天机混沌：导演未给出 JSON 裁决", hints=("只输出一个 JSON 对象，不要任何其他文字",))
    try:
        payload, _ = _DECODER.raw_decode(raw, start)
    except json.JSONDecodeError as exc:
        raise DirectorError("天机混沌：导演裁决不是合法 JSON", hints=(f"JSON 语法错误：{exc.msg}",)) from exc
    try:
        return DirectorOutput.model_validate(payload)
    except ValidationError as exc:
        hints = tuple(
            f"{'.'.join(str(part) for part in error['loc']) or '根对象'}：{error['msg']}"
            for error in exc.errors()[:_MAX_HINTS]
        )
        raise DirectorError(f"天机混沌：导演裁决不合法度（{exc.error_count()} 处错误）", hints=hints) from exc
