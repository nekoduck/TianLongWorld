"""
[INPUT]: 依赖 app.llm.base 的 LLMClient 协议，依赖 app.memory_service 的 MemoryService / MemoryFactory / in_memory（记忆仓储抽象），
         依赖 app.session 的 Session / SessionStore / LocalEnvironment / evolve / observe / witnessed，
         依赖 director 内 lethal / perception / prompts / parser，依赖 app.lore 的 OPENING_SEEDS，
         依赖 app.schemas 的 DIRECTOR_SCHEMA / DirectorOutput / InteractRequest / InteractResponse / NewSessionResponse，依赖 app.errors 的 DirectorError
[OUTPUT]: 对外提供 Director 类 —— open() 开局、interact() 推演一回合
[POS]: director 的编排核心，串起 守卫 → 致死预判 → RAG 检索（关系图 + 语义）→ System Prompt 组装 → 大模型 → 解析 → 生死封印
       → 状态推进 → 记忆落账；只认 MemoryService 抽象，从不碰台账的存储形状；被 app/api.py 调用
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import logging
import random

from app.director import lethal, prompts
from app.director.parser import parse_director_output
from app.director.perception import surface
from app.errors import DirectorError
from app.llm.base import LLMClient
from app.lore import OPENING_SEEDS
from app.memory_service import MemoryFactory, MemoryService, in_memory
from app.schemas import DIRECTOR_SCHEMA, DirectorOutput, InteractRequest, InteractResponse, NewSessionResponse
from app.session import LocalEnvironment, Session, SessionStore, evolve, observe, witnessed

logger = logging.getLogger(__name__)


class Director:
    def __init__(
        self,
        llm: LLMClient,
        store: SessionStore,
        *,
        memory: MemoryFactory | None = None,
        semantic_top_k: int = 3,
        attempts: int = 2,
        rng: random.Random | None = None,
    ):
        self._llm = llm
        self._store = store
        self._memory = memory or in_memory()  # 每个会话一个记忆仓储：存在哪里由组合根决定
        self._top_k = semantic_top_k  # 每回合至多注入的语义相关往事与江湖常识条数
        self._attempts = attempts
        self._rng = rng or random.Random()

    # ------------------------------------------------------------------
    #  开局：抽一颗种子，让导演铺陈第一幕
    # ------------------------------------------------------------------
    async def open(self) -> NewSessionResponse:
        seed = self._rng.choice(OPENING_SEEDS)
        # 新世界的台账为空、也没有上一回合：两个参考模块都是（无）
        out = await self._direct(prompts.system_prompt(), prompts.build_opening(seed))
        # 种子点名的高手恒先登记在场；开局不是移动，按大模型写出的开局地点登记，措辞漂移（"无锡松鹤楼"→"松鹤楼"）也清不掉他们
        here = LocalEnvironment(location=out.next_state.location, present_npcs=seed.present)
        local = observe(here, here.location, out.local_delta)
        session = self._store.create(evolve(seed.state, out), out.scene_description, local, witnessed(out, local))
        _settle(self._memory(session), out)
        return NewSessionResponse(session_id=session.id, **InteractResponse.of(session.state, out).model_dump())

    # ------------------------------------------------------------------
    #  回合：规则先裁生死，检索备好案头，大模型后叙因果，仓储最后落账
    # ------------------------------------------------------------------
    async def interact(self, req: InteractRequest) -> InteractResponse:
        session = self._store.get_or_rehydrate(req.session_id, req.current_state)
        with session.acting():
            verdict = lethal.judge(req.action_text, session.state.player_state, session.presence())
            memory = self._memory(session)
            system = self._context(memory, session, req.action_text)
            out = await self._direct(system, prompts.build_turn(session, req.action_type, req.action_text, verdict))
            if verdict.lethal:
                # 规则层的死刑不容大模型赦免：无论它写了什么，都封印为死亡
                out = out.model_copy(update={"game_over": True, "options": None})
            session.advance(req.action_text, out)
            _settle(memory, out)
        return InteractResponse.of(session.state, out)

    def _context(self, memory: MemoryService, session: Session, action: str) -> str:
        """
        RAG 上下文注入管道，产出本回合的 System Prompt。全量台账止步于仓储，进 Prompt 的只有两条检索路径各自封顶的几行：
        - 关系图：上一回合沉淀的 involved_entities + 此刻在场的 NPC，外加所在地与玩家的公开身份——JIT 记忆的地点、身份两路不能丢
        - 语义：玩家这一招的表面行为（心里的盘算不作检索键）；关系网里已有的大事不重复注入，为此多取几条再去重
        secrets 从不作检索键：以秘密为钥匙召回历史，等于每回合提醒导演"玩家藏着什么"，正是上帝视角漂移的温床
        """
        player = session.state.player_state
        graph = memory.query_relational_graph(
            [*session.involved, *session.local.present_npcs, player.location, *player.social_traits]
        )
        known = set(graph.splitlines())
        related = memory.query_semantic_events(surface(action), top_k=self._top_k + len(known))
        return prompts.system_prompt(graph, [line for line in related if line not in known][: self._top_k])

    async def _direct(self, system: str, user: str) -> DirectorOutput:
        """大模型输出有随机性：解析失败就重新采样，耗尽次数才认输。"""
        attempt = 1
        while True:
            raw = await self._llm.complete(system, user, DIRECTOR_SCHEMA)
            try:
                return parse_director_output(raw)
            except DirectorError as exc:
                logger.warning("导演裁决解析失败（第 %d/%d 次）：%s | 原文：%.300s", attempt, self._attempts, exc, raw)
                if attempt >= self._attempts:
                    raise
                attempt += 1


def _settle(memory: MemoryService, out: DirectorOutput) -> None:
    """
    情报与大事统一经记忆仓储落账，顺序即语义：先让揭穿的秘密退场，再收新知，最后记公开大事——
    仓储据此判断一条"大事"是否仍是玩家独知的秘密（私密优先），而被当众揭穿的秘密不再挡住它的公开后果。
    """
    secrets = out.player_delta.secrets
    for secret in secrets.remove:
        memory.retire_secret(secret)
    for secret in secrets.add:
        memory.commit_event({"event_desc": secret}, is_secret=True)
    for event in out.next_state.major_events:
        memory.commit_event(event.model_dump(), is_secret=False)
