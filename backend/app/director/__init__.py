"""
[INPUT]: 依赖 director/pipeline.py 的 Director
[OUTPUT]: 对外提供 Director（包的唯一公开入口）
[POS]: director 包门面，外部只需 from app.director import Director
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from app.director.pipeline import Director

__all__ = ["Director"]
