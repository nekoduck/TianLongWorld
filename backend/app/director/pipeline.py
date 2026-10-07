"""
[INPUT]: 依赖 app.llm.base 的 LLMClient 协议，依赖 app.session 的 SessionStore / LocalEnvironment / evolve / observe，
         依赖 director 内 lethal / memory / prompts / parser，依赖 app.lore 的 OPENING_SEEDS
[OUTPUT]: 对外提供 Director 类 —— open() 开局、interact() 推演一回合
[POS]: director 的编排核心，串起 守卫 → 致死预判 → 记忆过滤（JIT）→ Prompt 组装 → 大模型 → 解析 → 生死封印 → 状态推进；被 app/api.py 调用
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import logging
import random

from app.director import lethal, prompts
from app.director.memory import recall
from app.lore import OPENING_SEEDS
from app.director.parser import parse_director_output
from app.errors import DirectorError
from app.llm.base import LLMClient
from app.schemas import DIRECTOR_SCHEMA, DirectorOutput, InteractRequest, InteractResponse, NewSessionResponse
from app.session import LocalEnvironment, SessionStore, evolve, observe

logger = logging.getLogger(__name__)


class Director:
    def __init__(
        self,
        llm: LLMClient,
        store: SessionStore,
        *,
        memory_limit: int = 8,
        attempts: int = 2,
        rng: random.Random | None = None,
    ):
        self._llm = llm
        self._store = store
        self._memory_limit = memory_limit  # 每回合至多注入的相关世界大事条数
        self._attempts = attempts
        self._rng = rng or random.Random()

    # ------------------------------------------------------------------
    #  开局：抽一颗种子，让导演铺陈第一幕
    # ------------------------------------------------------------------
    async def open(self) -> NewSessionResponse:
        seed = self._rng.choice(OPENING_SEEDS)
        out = await self._direct(prompts.build_opening(seed))
        # 种子点名的高手恒先登记在场；大模型只补写其余到场者
        here = LocalEnvironment(location=seed.state.player_state.location, present_npcs=seed.present)
        local = observe(here, out.next_state.location, out.local_delta)
        session = self._store.create(evolve(seed.state, out), out.scene_description, local)
        return NewSessionResponse(session_id=session.id, **InteractResponse.of(session.state, out).model_dump())

    # ------------------------------------------------------------------
    #  回合：规则先裁生死，大模型后叙因果
    # ------------------------------------------------------------------
    async def interact(self, req: InteractRequest) -> InteractResponse:
        session = self._store.get_or_rehydrate(req.session_id, req.current_state)
        with session.acting():
            player = session.state.player_state
            verdict = lethal.judge(req.action_text, player, session.presence())
            # 记忆拦截：全量世界台账止步于此，只有与此时此地此人相关的几条进入 Prompt
            memories = recall(
                session.state.world_state.major_events,
                location=player.location,
                present_npcs=session.local.present_npcs,
                traits=player.social_traits,
                limit=self._memory_limit,
            )
            prompt = prompts.build_turn(session, memories, req.action_type, req.action_text, verdict)
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
