"""
[INPUT]: 依赖 app.session 的 Session 与 absorb / chronicle / still_secret（私密情报账与世界台账的记账规矩），
         依赖 app.lore 的 kin（人物别名）/ RELATIONS（关系网的静态边）/ WORLD_RULES（江湖常识），依赖 app.schemas 的 WorldEvent / WorldState / SecretDelta
[OUTPUT]: 对外提供 MemoryService 抽象仓储（query_relational_graph / query_semantic_events / commit_event / retire_secret）、
          InMemoryMemoryService 内存实现、MemoryFactory 工厂类型、in_memory() 工厂
[POS]: app 的记忆仓储层（Repository Pattern），GraphRAG 的接口地基：导演管线只认 MemoryService 这一抽象，从不碰台账的存储形状——
       今天是会话状态树上的两个列表加 lore 的静态表，明天换成图数据库与向量库，只需新写一个实现、在 main.py 换一个工厂。
       仓储按世界划定命名空间：接口方法不带会话参数，一个实例只服务一个会话，多租户串号在结构上不可能发生
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import re
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Mapping, Sequence
from functools import partial
from typing import Any

from app.lore import RELATIONS, WORLD_RULES, kin
from app.schemas import SecretDelta, WorldEvent, WorldState
from app.session import Session, absorb, chronicle, still_secret


# ============================================================
#  抽象仓储 —— 读走两条检索路径，写走一个入口
# ============================================================
class MemoryService(ABC):
    """
    导演的记忆仓储：
    - 图检索（GraphRAG 的 Graph 半边）：实体 → 它们之间、它们身上的关系网
    - 语义检索（Vector 半边）：动作文本 → 最相关的往事与江湖常识
    - 写入：情报与大事统一落账，is_secret 决定进玩家的 secrets 还是世界台账
    读出的每一行都是能直接摆上导演案头的一句话；同一条大事在两条检索路径上必须渲染为同一行文本，调用方据此去重。
    secrets 只进不出：它从不出现在检索结果里，也从不作检索键——导演从 <secrets> 段读它，NPC 无从得知。
    """

    @abstractmethod
    def query_relational_graph(self, entity_names: Sequence[str]) -> str:
        """实体关系网：一行一条，既有这些实体之间的关系，也有牵涉它们的大事；无命中返回空串。"""

    @abstractmethod
    def query_semantic_events(self, action_text: str, top_k: int = 3) -> list[str]:
        """与动作文本最相关的至多 top_k 条往事或江湖常识，按相关度降序。"""

    @abstractmethod
    def commit_event(self, event_data: Mapping[str, Any], is_secret: bool) -> None:
        """
        统一落账入口，event_data 形如 {"tags": [...], "event_desc": "..."}：
        is_secret=True 推入玩家的 secrets（tags 可省）；False 追加进世界台账——与玩家仍持有的秘密是同一件事的不入账（私密优先）。
        """

    @abstractmethod
    def retire_secret(self, secret: str) -> None:
        """秘密退场：当众揭穿、广为人知或失去意义时从 secrets 移出（名称照抄，唯一包含匹配）。"""


MemoryFactory = Callable[[Session], MemoryService]  # 每个会话（世界）一个仓储实例：命名空间在构造时划定


# ============================================================
#  内存实现 —— 存储即会话状态树（单一事实来源），检索是图与向量的朴素模拟
# ============================================================
_MIN_KEY = 2  # 单字键（"刀""庄"）匹配面太宽，只会把无关的关系与往事灌进上下文
_MIN_SIMILARITY = 0.2  # 未点名的条目要进语义检索，须与动作近乎同义：只撞上一个常见二元组（"天下"）的不算
_GRAPH_LIMIT = 12  # 关系网缺省行数；线上取值由 config.graph_limit 经 in_memory() 传入


class InMemoryMemoryService(MemoryService):
    """
    - 存储：会话的状态树（secrets 在 player_state，大事在 world_state），写穿透——前端拿到的整树与仓储永远是同一份
    - 关系图：节点是实体，边有两种——世界台账里的大事（一条大事连起它的全部 tags，是平行世界长出来的边）
      与 lore.RELATIONS（原著开篇的静态边）；实体与节点两侧都经 kin 展开别名，相互包含即命中
    - 语义：混合检索的两个梯队——点名命中（tag 或关键词原样出现在动作里，模拟关键词召回）在前、越近越先；
      未点名而字符二元组 Jaccard 相似度过线的近义复述（模拟向量召回）在后、越像越先
    - 检索是线性扫描，命中先在去重后的标签词汇上判定（台账再长，人名地名的词汇量也有限）；
      真上规模时换图数据库与向量库——这正是仓储接口存在的意义
    """

    def __init__(self, session: Session, *, graph_limit: int = _GRAPH_LIMIT) -> None:
        self._session = session
        self._graph_limit = graph_limit

    # ---- 读：关系图 ----
    def query_relational_graph(self, entity_names: Sequence[str]) -> str:
        keys = _aliases(entity_names)
        if not keys:
            return ""
        events = self._events()
        touched = {tag for tag in _vocabulary(events) if _touches(tag, keys)}
        # 大事在前：它们是平行世界的现状，导演无从由原著得知；原著关系在后，两端都命中的（这些实体之间的）先于一跳之外的
        recent = [event for event in events if not touched.isdisjoint(event.tags)][-self._graph_limit :]
        touching = [r for r in RELATIONS if _touches(r.a, keys) or _touches(r.b, keys)]
        between = [r for r in touching if _touches(r.a, keys) and _touches(r.b, keys)]
        beyond = [r for r in touching if r not in between]
        lines = [*map(_line, recent), *(f"【原著】{r.fact}" for r in (*between, *beyond))]
        return "\n".join(lines[: self._graph_limit])

    # ---- 读：语义 ----
    def query_semantic_events(self, action_text: str, top_k: int = 3) -> list[str]:
        if top_k <= 0:
            return []
        events = self._events()
        # 常识先入列、大事后入列：序号越大越近，同一梯队里近事优先
        corpus: list[tuple[Sequence[str], str, str | WorldEvent]] = [
            *((rule.keys, rule.text, f"【常识】{rule.text}") for rule in WORLD_RULES),
            *((event.tags, event.event_desc, event) for event in events),
        ]
        vocabulary = {key for rule in WORLD_RULES for key in rule.keys} | _vocabulary(events)
        named = {key for key in vocabulary if _named(key, action_text)}
        grams = _bigrams(action_text)
        called: list[int] = []
        similar: list[tuple[float, int]] = []
        for rank, (keys, text, _) in enumerate(corpus):
            if not named.isdisjoint(keys):
                called.append(rank)
            elif any(gram in text for gram in grams) and (score := _jaccard(grams, _bigrams(text))) >= _MIN_SIMILARITY:
                similar.append((score, rank))
        # 点名的越近越先：动作点到的地方，最近发生的事最可能左右眼前；未点名的越像越先
        ranked = [*sorted(called, reverse=True), *(rank for _, rank in sorted(similar, reverse=True))]
        return [_render(corpus[rank][2]) for rank in ranked[:top_k]]

    # ---- 写 ----
    def commit_event(self, event_data: Mapping[str, Any], is_secret: bool) -> None:
        if is_secret:
            learned = SecretDelta.model_validate({"add": [event_data.get("event_desc")]})  # 超长截断记日志，缺正文拒收
            self._keep_secrets(absorb(self._secrets(), learned))
            return
        event = WorldEvent.model_validate(event_data)
        if not still_secret(event, self._secrets()):
            self._keep_events(chronicle(self._events(), [event]))

    def retire_secret(self, secret: str) -> None:
        self._keep_secrets(absorb(self._secrets(), SecretDelta(remove=[secret])))

    # ---- 存储：会话状态树 ----
    def _secrets(self) -> list[str]:
        return self._session.state.player_state.secrets

    def _events(self) -> list[WorldEvent]:
        return self._session.state.world_state.major_events

    def _keep_secrets(self, secrets: list[str]) -> None:
        state = self._session.state
        player = state.player_state.model_copy(update={"secrets": secrets})
        self._session.state = state.model_copy(update={"player_state": player})

    def _keep_events(self, events: list[WorldEvent]) -> None:
        self._session.state = self._session.state.model_copy(update={"world_state": WorldState(major_events=events)})


def in_memory(*, graph_limit: int = _GRAPH_LIMIT) -> MemoryFactory:
    """内存仓储的工厂：组合根（main.py）只在这一处决定记忆存在哪里。"""
    return partial(InMemoryMemoryService, graph_limit=graph_limit)


# ============================================================
#  检索原语
# ============================================================
_WORD = re.compile(r"\w+")


def _aliases(names: Iterable[str]) -> tuple[str, ...]:
    """检索键：每个实体展开为全部称呼（「乔峰」即「萧峰」），去重保序，单字不作键。"""
    return tuple(dict.fromkeys(alias for name in names for alias in kin(name) if len(alias) >= _MIN_KEY))


def _hit(node: str, key: str) -> bool:
    """相互包含即命中："聚贤庄废墟"↔「聚贤庄」、"丐帮弟子"↔「丐帮」；双方都须至少 2 字。"""
    return len(node) >= _MIN_KEY and len(key) >= _MIN_KEY and (node in key or key in node)


def _touches(node: str, keys: Sequence[str]) -> bool:
    """节点（大事标签或关系端点）的任一称呼与任一检索键相互包含即命中：「萧峰」的边也认得"醉酒的乔峰"这样的修饰称呼。"""
    return any(_hit(alias, key) for alias in kin(node) for key in keys)


def _named(key: str, text: str) -> bool:
    """点名：键（含别名）原样出现在动作里。只做单向检索——长动作文本不能反过来"包含"一切短键。"""
    return any(alias in text for alias in kin(key) if len(alias) >= _MIN_KEY)


def _vocabulary(events: Iterable[WorldEvent]) -> set[str]:
    return {tag for event in events for tag in event.tags}


def _bigrams(text: str) -> set[str]:
    """字符二元组：中文没有空格分词，二元组是最朴素的"向量"；标点与空白断开词段，不跨段成组。"""
    return {run[i : i + 2] for run in _WORD.findall(text) for i in range(len(run) - 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _line(event: WorldEvent) -> str:
    """台账大事的案头形态：标签在前、原文在后；压平内部空白，一条大事永远只占一行，伪造不出第二行。"""
    return " ".join(f"【台账】{'、'.join(event.tags)}：{event.event_desc}".split())


def _render(item: str | WorldEvent) -> str:
    return item if isinstance(item, str) else _line(item)
