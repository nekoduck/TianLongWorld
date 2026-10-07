"""
[INPUT]: 依赖 fastapi 的 APIRouter / Depends / Request，依赖 app.director 的 Director，依赖 app.schemas 的请求/响应模型
[OUTPUT]: 对外提供 router —— POST /api/session、POST /api/interact、GET /api/health
[POS]: app 的路由层，只做协议校验与转发，零业务逻辑；Director 实例由 main.py 在 lifespan 内挂到 app.state 上注入
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.director import Director
from app.schemas import InteractRequest, InteractResponse, NewSessionRequest, NewSessionResponse

router = APIRouter(prefix="/api")


def get_director(request: Request) -> Director:
    director = request.app.state.director
    assert isinstance(director, Director)
    return director


Directing = Annotated[Director, Depends(get_director)]


@router.post("/session", response_model=NewSessionResponse)
async def new_session(director: Directing, req: NewSessionRequest | None = None) -> NewSessionResponse:
    """入世 / 投胎：world_id 缺省开辟新世界，携带则在该世界重新投胎（此身全新，世界大事延续）。"""
    return await director.open(req.world_id if req else None)


@router.post("/interact", response_model=InteractResponse)
async def interact(req: InteractRequest, director: Directing) -> InteractResponse:
    """出招：推演玩家的一个动作。"""
    return await director.interact(req)


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
