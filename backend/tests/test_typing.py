"""
[INPUT]: 依赖 mypy.api 的 run，读取 backend/mypy.ini（strict + pydantic 插件，覆盖 app 与 tests）
[OUTPUT]: test_codebase_is_strictly_typed —— 类型闸门
[POS]: tests 的静态契约守卫："100% 类型注解"不靠自觉而靠闸门——任何未注解的函数、隐式 Any、
       与冻结模型不符的构造都会让 pytest 变红，和行为用例一起拦在合入之前
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from pathlib import Path

from mypy import api

CONFIG = Path(__file__).resolve().parent.parent / "mypy.ini"


def test_codebase_is_strictly_typed() -> None:
    stdout, stderr, status = api.run(["--config-file", str(CONFIG)])
    assert status == 0, stdout + stderr
