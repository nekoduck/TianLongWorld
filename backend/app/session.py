"""
[INPUT]: 依赖 app.schemas 的 GameState / PlayerSnapshot / PlayerState / WorldState / WorldEvent / TagDelta / SecretDelta / LocalDelta / DirectorOutput / LEDGERS / MAX_TAGS，
         依赖 app.lore 的 kin / is_grandmaster（在场者按身份认人、满员时高手优先），依赖 app.errors 的 SessionDeadError / SessionBusyError
[OUTPUT]: 对外提供 evolve（状态推进）、reconcile（四本标签账）、absorb（私密情报账）、chronicle（世界台账）、LocalEnvironment / observe（局部环境）、Turn、
          Session（含 acting 守卫、presence 在场判定素材、advance 推进）、SessionStore（LRU 内存仓库）
[POS]: app 的会话状态层，是世界状态的唯一权威；被 director/pipeline.py 读写，不感知 HTTP 与大模型。
       三种记账各守一条规矩：五本玩家账（含 secrets）遗漏不等于失去；世界台账只追加不删除；局部环境同图只认增减、换图强制清空
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import logging
from collections import OrderedDict, deque
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from app.errors import SessionBusyError, SessionDeadError
from app.lore import is_grandmaster, kin
from app.schemas import (
    LEDGERS,
    MAX_TAGS,
    DirectorOutput,
    GameState,
    LocalDelta,
    PlayerSnapshot,
    PlayerState,
    SecretDelta,
    TagDelta,
    WorldEvent,
    WorldState,
)

logger = logging.getLogger(__name__)

MAX_PRESENT = 12  # 局部环境在场者上限：满员时绝顶高手一律留下（致死预判读的正是他们），其余人里先请走最早到场的


# ============================================================
#  状态推进 —— 快照照单全收；五本账只认增减；世界台账只追加，且容不下仍是秘密的事
# ============================================================
_SECRET_OVERLAP = 6  # 判"同一件事"的最短包含长度：太短会把"马大元暴毙"这类公开事实误当成秘密吞掉


def evolve(state: GameState, out: DirectorOutput) -> GameState:
    before, delta = state.player_state, out.player_delta
    ledgers = {name: _BOOKKEEPERS.get(name, reconcile)(getattr(before, name), getattr(delta, name)) for name in LEDGERS}
    snapshot = PlayerSnapshot.model_validate(out.next_state.model_dump(exclude={"major_events"}))
    player = PlayerState(**snapshot.model_dump(), **ledgers)
    public = [event for event in out.next_state.major_events if not _still_secret(event, player.secrets)]
    world = WorldState(major_events=chronicle(state.world_state.major_events, public))
    return GameState(player_state=player, world_state=world)


def _still_secret(event: WorldEvent, secrets: Sequence[str]) -> bool:
    """
    同一件事不能既是秘密又是天下皆知：大模型把刚得知的内情同时写进 secrets 与 major_events 时，私密优先——
    泄露不可撤回，而公开的大事等秘密真被当众揭穿（secrets 里 remove 掉）时再记也不迟。
    只认相同或相互包含（短者不少于 6 字）：模糊重叠会把"马大元暴毙"这类公开事实误当秘密吞掉。
    """
    desc = event.event_desc
    for secret in secrets:
        short, long = sorted((desc, secret), key=len)
        if desc == secret or (len(short) >= _SECRET_OVERLAP and short in long):
            logger.info("世界大事与玩家仍持有的秘密是同一件事，不入台账：%s", desc)
            return True
    return False


def reconcile(tags: list[str], delta: TagDelta) -> list[str]:
    """
    标签守恒：只有被点名移除的才会消失，大模型的遗漏不等于失去。
    先减后加——"水囊"喝空写作 移除「水囊」+ 新增「空水囊」，先减才不会与新增之物撞名。
    """
    kept = _drop_named(tags, delta.remove)
    for name in delta.add:
        if name not in kept:
            kept.append(name)
    if len(kept) > MAX_TAGS:
        logger.warning("标签超过 %d 个，截去最后新增的：%s", MAX_TAGS, kept[MAX_TAGS:])
    return kept[:MAX_TAGS]


def absorb(secrets: list[str], delta: SecretDelta) -> list[str]:
    """
    私密情报账：移除同样只认点名（遗漏不等于遗忘），但新增与满员的规矩不同于行囊——
    - 同一情报换个说法再报：被已知情报包含的不再记；包含了已知情报的更详尽说法取而代之
    - 满员时请走最早的情报：新知比旧闻更可能左右眼前，行囊满了丢新物是常理，情报满了丢新知却是失忆
    """
    kept = _drop_named(secrets, delta.remove)
    for fact in delta.add:
        if any(fact in known for known in kept):
            continue
        kept = [known for known in kept if known not in fact] + [fact]
    if len(kept) > MAX_TAGS:
        logger.warning("私密情报超过 %d 条，请走最早的：%s", MAX_TAGS, kept[: len(kept) - MAX_TAGS])
    return kept[-MAX_TAGS:]


def _drop_named(held: list[str], names: Sequence[str]) -> list[str]:
    kept = list(held)
    for name in names:
        found = _match(kept, name)
        if found is None:
            logger.info("忽略未持有或指代不明的移除：%s ∉ %s", name, kept)
        else:
            kept.remove(found)
    return kept


# 账本 -> 记账规则：四本标签账走 reconcile，私密情报账走 absorb
_BOOKKEEPERS = {"secrets": absorb}


def chronicle(events: list[WorldEvent], fresh: Sequence[WorldEvent]) -> list[WorldEvent]:
    """
    世界台账：只追加，不改写、不合并、不删除，也不设上限——大模型只看得见按标签筛出的几条，台账再长也不撑爆上下文。
    大模型偶尔会把注入的历史原样抄回来：event_desc 已在台账里的一律忽略，只追加的台账容不下重复。
    """
    ledger = list(events)
    known = {event.event_desc for event in ledger}
    for event in fresh:
        if event.event_desc in known:
            logger.info("忽略复述的世界大事：%s", event.event_desc)
            continue
        known.add(event.event_desc)
        ledger.append(event)
    return ledger


def _match(tags: Sequence[str], name: str) -> str | None:
    """精确匹配优先；否则接受唯一的包含关系（「弯刀」↔「契丹弯刀」），多义时宁可不动。"""
    if name in tags:
        return name
    candidates = [tag for tag in tags if name in tag or tag in name]
    return candidates[0] if len(candidates) == 1 else None


# ============================================================
#  局部环境 —— 此刻的地点与在场的有名有姓者
# ============================================================
@dataclass(frozen=True)
class LocalEnvironment:
    location: str
    present_npcs: tuple[str, ...] = ()


def observe(local: LocalEnvironment, location: str, delta: LocalDelta) -> LocalEnvironment:
    """
    局部环境记账：
    - 同一地图（新地点包含原地点全称：原地不动，或深入子地点 "无锡松鹤楼" → "无锡松鹤楼二楼"）只认到场与离场，遗漏不等于离场
    - 其余一律视作切换地图，强制清空旧地点的在场者，只留大模型写出的新地点到场者——
      宁可让高手暂时离开名单（大模型仍受高手名录约束），也不让不在场的人凭旧名单处决玩家
    - 在场者按身份认人：「乔峰」与「萧峰」只登记一次，写「乔帮主」「丐帮帮主乔峰」离场也能对上
    """
    same_map = local.location in location
    base = local.present_npcs if same_map else ()
    gone = {held for name in delta.departed if (held := _whom(base, name)) is not None}
    kept = [npc for npc in base if npc not in gone]
    for name in delta.arrived:
        if not any(_same_person(npc, name) for npc in kept):
            kept.append(name)
    return LocalEnvironment(location=location, present_npcs=_cap(kept))


def _cap(present: list[str]) -> tuple[str, ...]:
    """满员时高手一个不少；其余人按到场先后只留最近的几位——同图里"遗漏不等于离场"会让早到的路人越积越多。"""
    if len(present) <= MAX_PRESENT:
        return tuple(present)
    masters = [npc for npc in present if is_grandmaster(npc)][:MAX_PRESENT]
    others = [npc for npc in present if not is_grandmaster(npc)]
    recent = others[len(others) - (MAX_PRESENT - len(masters)) :] if len(masters) < MAX_PRESENT else []
    return tuple(npc for npc in present if npc in masters or npc in recent)


def _whom(present: Sequence[str], name: str) -> str | None:
    """离场认人：先按名字（精确或唯一包含），再按身份，最后看修饰称呼里是否含某人的任一别名（"丐帮帮主乔峰" → 萧峰）。"""
    return (
        _match(present, name)
        or next((npc for npc in present if _same_person(npc, name)), None)
        or next((npc for npc in present if any(len(alias) >= 2 and alias in name for alias in kin(npc))), None)
    )


def _same_person(a: str, b: str) -> bool:
    # 到场只认同名或同一身份：包含关系在这里会把「看客1」与「看客10」误认作一人
    return bool(set(kin(a)) & set(kin(b)))


# ============================================================
#  会话
# ============================================================
@dataclass(frozen=True)
class Turn:
    """一回合的叙事记忆。四个状态字段装不下"黑衣人正盯着你"，涌现叙事靠这段短记忆维持连贯。"""

    action: str  # 空串表示开局
    scene: str


@dataclass
class Session:
    id: UUID
    state: GameState
    history: deque[Turn]  # 滑动窗口：deque 的 maxlen 即窗口长度，更早的回合自动截断
    local: LocalEnvironment = field(default_factory=lambda: LocalEnvironment(location=""))
    dead: bool = False
    busy: bool = False

    def presence(self) -> str:
        """致死预判的"在场"素材：优先用局部环境的结构化名单；名单为空（冷启动）时退回上一幕原文。"""
        if self.local.present_npcs:
            return "、".join(self.local.present_npcs)
        return self.history[-1].scene if self.history else ""

    @contextmanager
    def acting(self) -> Iterator[None]:
        """回合守卫：死者不得行动，上一招未落定不得出下一招。"""
        if self.dead:
            raise SessionDeadError("此身已死，江湖再无你的传说。请重新投胎。")
        if self.busy:
            raise SessionBusyError("上一招尚未落定，莫要心急。")
        self.busy = True
        try:
            yield
        finally:
            self.busy = False

    def advance(self, action: str, out: DirectorOutput) -> None:
        self.state = evolve(self.state, out)
        self.local = observe(self.local, out.next_state.location, out.local_delta)
        self.history.append(Turn(action, out.scene_description))
        self.dead = out.game_over


class SessionStore:
    """纯内存会话仓库。OrderedDict 实现 LRU：超出容量时淘汰最久未活动的会话。"""

    def __init__(self, capacity: int = 1000, history_turns: int = 4) -> None:
        self._sessions: OrderedDict[UUID, Session] = OrderedDict()
        self._capacity = capacity
        self._history_turns = history_turns

    def create(self, state: GameState, opening_scene: str, local: LocalEnvironment | None = None) -> Session:
        here = local or LocalEnvironment(location=state.player_state.location)
        return self._put(uuid4(), state, (Turn("", opening_scene),), here)

    def get_or_rehydrate(self, session_id: UUID, client_state: GameState) -> Session:
        """服务端会话优先；丢失时以客户端快照冷启动（开发期热重载、进程重启后无缝续玩；局部环境与短期记忆随之清空）。"""
        session = self._sessions.get(session_id)
        if session is None:
            local = LocalEnvironment(location=client_state.player_state.location)
            return self._put(session_id, client_state, (), local)
        self._sessions.move_to_end(session_id)
        return session

    def _put(self, session_id: UUID, state: GameState, turns: tuple[Turn, ...], local: LocalEnvironment) -> Session:
        session = Session(session_id, state, deque(turns, maxlen=self._history_turns), local)
        self._sessions[session_id] = session
        while len(self._sessions) > self._capacity:
            self._sessions.popitem(last=False)
        return session
