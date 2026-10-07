"""
[INPUT]: 依赖 fastapi 的 APIRouter / Depends / Request，依赖 app.director 的 Director，依赖 app.schemas 的请求/响应模型
[OUTPUT]: 对外提供 router —— POST /api/session、POST /api/interact、GET /api/health
[POS]: app 的路由层，只做协议校验与转发，零业务逻辑；Director 实例由 main.py 挂在 app.state 上注入。
       /api/interact 背后的 RAG 上下文管道（关系图 + 语义检索 → System Prompt 参考模块）住在 director/pipeline.py，路由不感知
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from fastapi import APIRouter, Depends, Request

from app.director import Director
from app.schemas import InteractRequest, InteractResponse, NewSessionResponse

router = APIRouter(prefix="/api")


def get_director(request: Request) -> Director:
    return request.app.state.director


@router.post("/session", response_model=NewSessionResponse)
async def new_session(director: Director = Depends(get_director)) -> NewSessionResponse:
    """投胎：开一局新游戏，返回 session_id 与第一幕。"""
    return await director.open()


@router.post("/interact", response_model=InteractResponse)
async def interact(req: InteractRequest, director: Director = Depends(get_director)) -> InteractResponse:
    """
    出招：推演玩家的一个动作。Director 内部依次是 守卫 → 致死预判 →
    RAG 检索（上一回合 involved_entities + 在场 NPC → 关系图；动作表面文本 → 语义）→ System Prompt 注入 →
    大模型 → 解析 → 生死封印 → 状态推进 → 记忆落账。
    """
    return await director.interact(req)


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
