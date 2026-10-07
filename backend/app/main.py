"""
[INPUT]: 依赖 fastapi 与 CORSMiddleware，依赖 app.config / app.api / app.director / app.store / app.llm.factory / app.errors
[OUTPUT]: 对外提供 create_app(settings, llm) 组合根与模块级 app（uvicorn app.main:app 的入口）
[POS]: app 的组合根：唯一一处把配置、大模型、事件库、导演与路由装配在一起的地方。
       事件库在 lifespan 内打开与关闭——导入模块不触碰磁盘，进程退出时连接被确定性释放；测试经 llm 参数注入剧本替身
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import router
from app.config import Settings, get_settings
from app.director import Director
from app.errors import GameError
from app.llm.base import LLMClient
from app.llm.factory import build_llm
from app.store import EventStore

_BACKEND = Path(__file__).resolve().parent.parent
_IN_MEMORY = ":memory:"


def _open_store(path: str) -> EventStore:
    """相对路径锚定在 backend/，与启动时的 cwd 无关；首次启动自动建目录。"""
    if path == _IN_MEMORY:
        return EventStore(path)
    file = Path(path) if Path(path).is_absolute() else _BACKEND / path
    file.parent.mkdir(parents=True, exist_ok=True)
    return EventStore(str(file))


async def _game_error_handler(_: Request, exc: Exception) -> JSONResponse:
    # detail 与 FastAPI 原生 422 同形，供玩家阅读；code 是机器可读语义，供前端分支（如 dead → 投胎界面）
    assert isinstance(exc, GameError)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message, "code": exc.code})


def create_app(settings: Settings | None = None, llm: LLMClient | None = None) -> FastAPI:
    config = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        store = _open_store(config.database_path)
        try:
            app.state.director = Director(
                llm or build_llm(config),
                store,
                window=config.history_turns,
                memory_limit=config.memory_limit,
            )
            yield
        finally:
            store.close()

    app = FastAPI(title="天龙八部：平行世界", version="0.2.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    app.add_exception_handler(GameError, _game_error_handler)
    app.include_router(router)
    return app


app = create_app()
