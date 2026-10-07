"""
[INPUT]: 依赖 fastapi 与 CORSMiddleware，依赖 app.config / app.api / app.director / app.session / app.llm.factory / app.errors
[OUTPUT]: 对外提供 create_app() 组合根与模块级 app（uvicorn app.main:app 的入口）
[POS]: app 的组合根：唯一一处把配置、大模型、会话仓库、导演与路由装配在一起的地方；测试经 director 参数注入替身
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import router
from app.config import Settings, get_settings
from app.director import Director
from app.errors import GameError
from app.llm.factory import build_llm
from app.session import SessionStore


async def _game_error_handler(_: Request, exc: Exception) -> JSONResponse:
    # 与 FastAPI 原生 422 同形：前端只需读取 detail 一个字段
    assert isinstance(exc, GameError)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


def create_app(settings: Settings | None = None, director: Director | None = None) -> FastAPI:
    settings = settings or get_settings()
    director = director or Director(
        build_llm(settings),
        SessionStore(capacity=settings.session_capacity, history_turns=settings.history_turns),
        memory_limit=settings.memory_limit,
    )

    app = FastAPI(title="天龙八部：平行世界", version="0.1.0")
    app.state.director = director
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    app.add_exception_handler(GameError, _game_error_handler)
    app.include_router(router)
    return app


app = create_app()
