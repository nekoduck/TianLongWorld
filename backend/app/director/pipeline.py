"""
[INPUT]: 依赖 app.llm.base 的 LLMClient 协议，依赖 app.session 的 SessionStore / evolve，依赖 director 内 lethal / prompts / parser / lore
[OUTPUT]: 对外提供 Director 类 —— open() 开局、interact() 推演一回合
[POS]: director 的编排核心，串起 守卫 → 致死预判 → Prompt 组装 → 大模型 → 解析 → 生死封印 → 状态推进；被 app/api.py 调用
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import logging
import random

from app.director import lethal, prompts
from app.director.lore import OPENING_SEEDS
from app.director.parser import parse_director_output
from app.errors import DirectorError
from app.llm.base import LLMClient
from app.schemas import DIRECTOR_SCHEMA, DirectorOutput, InteractRequest, InteractResponse, NewSessionResponse
from app.session import SessionStore, evolve

logger = logging.getLogger(__name__)


class Director:
    def __init__(self, llm: LLMClient, store: SessionStore, *, attempts: int = 2, rng: random.Random | None = None):
        self._llm = llm
        self._store = store
        self._attempts = attempts
        self._rng = rng or random.Random()

    # ------------------------------------------------------------------
    #  开局：抽一颗种子，让导演铺陈第一幕
    # ------------------------------------------------------------------
    async def open(self) -> NewSessionResponse:
        seed = self._rng.choice(OPENING_SEEDS)
        out = await self._direct(prompts.build_opening(seed))
        session = self._store.create(evolve(seed.state, out), out.scene_description, tuple(out.present))
        return NewSessionResponse(session_id=session.id, **InteractResponse.of(session.state, out).model_dump())

    # ------------------------------------------------------------------
    #  回合：规则先裁生死，大模型后叙因果
    # ------------------------------------------------------------------
    async def interact(self, req: InteractRequest) -> InteractResponse:
        session = self._store.get_or_rehydrate(req.session_id, req.current_state)
        with session.acting():
            verdict = lethal.judge(req.action_text, session.state.player_state, session.presence())
            prompt = prompts.build_turn(session, req.action_type, req.action_text, verdict)
            out = await self._direct(prompt)
            if verdict.lethal:
                # 规则层的死刑不容大模型赦免：无论它写了什么，都封印为死亡
                out = out.model_copy(update={"game_over": True, "options": None})
            session.advance(req.action_text, out)
        return InteractResponse.of(session.state, out)

    async def _direct(self, user_prompt: str) -> DirectorOutput:
        """大模型输出有随机性：解析失败就重新采样，耗尽次数才认输。"""
        attempt = 1
        while True:
            raw = await self._llm.complete(prompts.SYSTEM_PROMPT, user_prompt, DIRECTOR_SCHEMA)
            try:
                return parse_director_output(raw)
            except DirectorError as exc:
                logger.warning("导演裁决解析失败（第 %d/%d 次）：%s | 原文：%.300s", attempt, self._attempts, exc, raw)
                if attempt >= self._attempts:
                    raise
                attempt += 1
