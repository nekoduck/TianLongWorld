"""
[INPUT]: 依赖 app.llm.base 的 LLMClient，依赖 app.store 的 EventStore，依赖 app.engine 的投影与裁决纯函数，
         依赖 director 内 lethal / memory / prompts / parser / fallback，依赖 app.lore 的 OPENING_SEEDS，依赖 app.errors 的错误谱系
[OUTPUT]: 对外提供 Director —— open(world_id) 开局 / 投胎，interact(req) 推演一回合
[POS]: director 的编排器，ESAA 的单一同步主循环：
         读事件 → 投影状态 → 规则判生死 → JIT 召回记忆 → 组装 Prompt → 大模型产出意图 → 解析校验（带错重采样）
         → 纯函数裁决为事件 → 原子追加 → 投影出响应
       它是唯一触碰 I/O（大模型、事件库、日志）的地方；engine 只算不写，大模型只提议不写
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import logging
import random
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from uuid import UUID, uuid4

from app.director import fallback, lethal, prompts
from app.director.memory import recall
from app.director.parser import parse_director_output
from app.engine import LifeView, apply, begin, decide_opening, decide_turn, game_state, project_life, project_world, snapshot_of
from app.errors import DirectorError, LLMError, NotFoundError, SessionBusyError, SessionDeadError
from app.llm.base import LLMClient
from app.lore import OPENING_SEEDS
from app.schemas import DIRECTOR_SCHEMA, DirectorOutput, InteractRequest, InteractResponse, NewSessionResponse
from app.store import EventStore

logger = logging.getLogger(__name__)

Check = Callable[[DirectorOutput], None]


# ============================================================
#  采纳前的语义检查 —— 契约之外、取决于本回合指令的硬约束；不通过即带错重采样
# ============================================================
def _any(_: DirectorOutput) -> None:
    return None


def _alive(out: DirectorOutput) -> None:
    if out.game_over:
        raise DirectorError("开局裁决判了玩家死亡", hints=("这是开局：game_over 必须为 false，并给出 A/B/C 三个选项",))


def _dead(out: DirectorOutput) -> None:
    if not out.game_over:
        raise DirectorError("必死回合未判死", hints=("指令为必死：game_over 必须为 true，options 必须为 null",))


class Director:
    def __init__(
        self,
        llm: LLMClient,
        store: EventStore,
        *,
        window: int,
        memory_limit: int,
        attempts: int = 2,
        rng: random.Random | None = None,
    ) -> None:
        if attempts < 1:
            raise ValueError("attempts 至少为 1")
        self._llm = llm
        self._store = store
        self._window = window
        self._memory_limit = memory_limit
        self._attempts = attempts
        self._rng = rng or random.Random()
        self._inflight: set[UUID] = set()  # 同一条命同一时刻只推演一招；跨进程由事件库的乐观并发兜底

    # ------------------------------------------------------------------
    #  开局 / 投胎：此身状态全新，世界大事延续
    # ------------------------------------------------------------------
    async def open(self, world_id: UUID | None) -> NewSessionResponse:
        if world_id is not None and not self._store.world_exists(world_id):
            raise NotFoundError("此方世界无从寻觅，请重新入世。")
        world_id = world_id or uuid4()
        world = project_world(self._store.load_world(world_id))
        seed = self._rng.choice(OPENING_SEEDS)
        memories = recall(
            world.major_events, location=seed.player.location, entities=seed.present, traits=(), limit=self._memory_limit
        )
        out = await self._attempt(prompts.build_opening(seed, memories), _alive)
        if out is None:
            logger.warning("开局裁决持续不合法度，退回种子原文")
            out = fallback.seed_opening(seed)

        began = decide_opening(world_id, seed, out)
        life_id = uuid4()
        self._store.append(life_id=life_id, world_id=world_id, expected_version=0, life=(began,))
        reply = InteractResponse.render(game_state(begin(life_id, began), world), began.scene, began.options, False)
        return NewSessionResponse(session_id=life_id, world_id=world_id, **dict(reply))

    # ------------------------------------------------------------------
    #  回合：规则先裁生死，大模型后叙因果，运行时落定事实
    # ------------------------------------------------------------------
    async def interact(self, req: InteractRequest) -> InteractResponse:
        # req.current_state 只是客户端回显：状态一律从事件日志投影，客户端无从篡改
        with self._acting(req.session_id):
            view = self._load(req.session_id)
            if view.dead:
                raise SessionDeadError("你已身死道消，此世再无回头路。")
            world = project_world(self._store.load_world(view.world_id))

            verdict = lethal.judge(req.action_text, view.player, view.local.entities)
            memories = recall(
                world.major_events,
                location=view.player.location,
                entities=view.local.entities,
                traits=view.player.social_traits,
                limit=self._memory_limit,
            )
            prompt = prompts.build_turn(view, memories, req.action_type, req.action_text, verdict)
            out = await self._resolve(prompt, view, verdict)
            if out is None:
                logger.warning("回合裁决持续不合法度，世界原地停顿：%s", req.session_id)
                return InteractResponse.render(game_state(view, world), fallback.STALLED_SCENE, view.options, False)

            decision = decide_turn(view, req.action_type, req.action_text, out, condemned=verdict.lethal)
            for note in decision.rejections:
                logger.info("驳回导演意图（%s）：%s", req.session_id, note)
            self._store.append(
                life_id=view.life_id,
                world_id=view.world_id,
                expected_version=view.version,
                life=(decision.turn,),
                world=decision.world_events,
            )
            after = apply(view, decision.turn, self._window)
            world = project_world(self._store.load_world(view.world_id))  # 同一世界的其他人世可能也刚改写了大事记

        turn = decision.turn
        return InteractResponse.render(game_state(after, world), turn.scene, turn.options, turn.died)

    async def _resolve(self, prompt: str, view: LifeView, verdict: lethal.Verdict) -> DirectorOutput | None:
        if verdict.killer is None:
            return await self._attempt(prompt, _any)
        # 必死回合不依赖大模型：失败、抗命、幻觉一律落到确定性处决
        try:
            out = await self._attempt(prompt, _dead)
        except LLMError:
            logger.warning("必死回合大模型不可用，执行确定性处决：%s", view.life_id)
            out = None
        return out or fallback.execution(snapshot_of(view.player), verdict.killer.name, verdict.killer.signature)

    # ------------------------------------------------------------------
    #  容错链：约束解码（厂商层）→ 宽容解析 → 带错重采样 → None（交给调用方的确定性兜底）
    # ------------------------------------------------------------------
    async def _attempt(self, prompt: str, check: Check) -> DirectorOutput | None:
        """
        返回采纳的裁决；大模型可达但输出持续不合法度时返回 None；每一次都是调用失败才抛 LLMError（502）。
        """
        user = prompt
        failure: LLMError | None = None
        hallucinated = False
        for attempt in range(1, self._attempts + 1):
            try:
                raw = await self._llm.complete(prompts.SYSTEM_PROMPT, user, DIRECTOR_SCHEMA)
            except LLMError as exc:
                logger.warning("大模型调用失败（第 %d/%d 次）：%s", attempt, self._attempts, exc)
                failure = exc
                continue
            try:
                out = parse_director_output(raw)
                check(out)
                return out
            except DirectorError as exc:
                logger.warning("导演裁决不合法度（第 %d/%d 次）：%s %s | 原文：%.300s", attempt, self._attempts, exc, exc.hints, raw)
                hallucinated = True
                user = prompts.with_feedback(prompt, exc.hints)
        if hallucinated or failure is None:
            return None
        raise failure

    # ------------------------------------------------------------------
    #  读模型
    # ------------------------------------------------------------------
    def _load(self, life_id: UUID) -> LifeView:
        events = self._store.load_life(life_id)
        if not events:
            raise NotFoundError("此局不存在或已散去，请重新入世。")
        return project_life(life_id, events, self._window)

    @contextmanager
    def _acting(self, life_id: UUID) -> Iterator[None]:
        if life_id in self._inflight:
            raise SessionBusyError("上一招尚未落定，稍安勿躁。")
        self._inflight.add(life_id)
        try:
            yield
        finally:
            self._inflight.discard(life_id)
