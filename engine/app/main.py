"""
[INPUT]: 依赖 fastapi 的 FastAPI，依赖 app.config 的 Settings / get_settings，依赖 app.container 的 Container / build_container，
         依赖 presentation/websocket 的 router
[OUTPUT]: 对外提供 create_app(settings, container_factory) 应用工厂、模块级 app（供 uvicorn 加载）
[POS]: 引擎的进程入口：lifespan 里调用组合根装配一次、关闭时释放全部后端连接；挂载 WebSocket /ws/play 与 GET /health。
       测试经 container_factory 注入替身容器，与生产走同一条装配路径
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from app.config import Settings, get_settings
from app.container import Container, build_container
from app.presentation.websocket import router as ws_router

type ContainerFactory = Callable[[Settings], Awaitable[Container]]


def create_app(settings: Settings | None = None, *, container_factory: ContainerFactory | None = None) -> FastAPI:
    config = settings or get_settings()
    factory = container_factory or build_container

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        container = await factory(config)
        app.state.container = container
        try:
            yield
        finally:
            await container.aclose()

    app = FastAPI(title="TLBB-Engine", version="0.1.0", lifespan=lifespan)
    app.include_router(ws_router)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        container: Container = app.state.container
        return {"status": "ok", "seeded": await container.seeder.is_seeded()}

    return app


app = create_app()
