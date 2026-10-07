"""
[INPUT]: 依赖 pydantic 的 ValidationError，依赖 app.schemas 的 DirectorOutput，依赖 app.errors 的 DirectorError
[OUTPUT]: 对外提供 parse_director_output() —— 原始文本 → 经校验的 DirectorOutput
[POS]: director 的解析层，是大模型自由文本与强类型协议之间的唯一闸门；失败即抛 DirectorError 交由 pipeline 重试
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from pydantic import ValidationError

from app.errors import DirectorError
from app.schemas import DirectorOutput


def parse_director_output(raw: str) -> DirectorOutput:
    """
    截取首个 "{" 到末个 "}" —— 一刀同时剥掉 ```json 围栏与前后寒暄，
    其余一切交给 Pydantic 校验：类型、长度、存活必有选项。
    """
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise DirectorError("天机混沌：导演未给出 JSON 裁决")
    try:
        return DirectorOutput.model_validate_json(raw[start : end + 1])
    except ValidationError as exc:
        raise DirectorError(f"天机混沌：导演裁决不合法度（{exc.error_count()} 处错误）") from exc
