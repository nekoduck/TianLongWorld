"""
[INPUT]: 依赖 application/ports 的 LLMClient，依赖 domain/models 的本体枚举与 WorldBlueprint，
         依赖 infrastructure/blueprint_assembler 的 BlueprintAssembler / AssemblyReport / CanonEventKind，依赖 infrastructure/cypher 的 compile_blueprint / render_script，
         依赖 app.errors 的 ExtractionError / LLMError
[OUTPUT]: 对外提供 SourceDocument / load_corpus()（读取 data/source_text，UTF-8 → GB18030 回退，经 clean_text 只留正文）、clean_text()（去水印、去序跋）、
          Chunk / chunk_text()（按回目与段落切块）、
          抽取契约 ChunkExtraction（Raw* 名称级记录：地点带 parent 上级；人物三名分立 name / titles / aliases + name_is_title；
          武学两道门 RawAcquisition / RawPractice，旧缓存的 prerequisites 读入即升级；RawCanonEvent 记 T=0 之后的状态变化）、
          T0_ANCHOR 时间锚点、EXTRACTION_SYSTEM 抽取铁律 v7、naming_reference()（上一版蓝图 → 跨块命名参考）、escape_markup()（标签内插值转义）、
          KnowledgeExtractor 抽象与 LLMKnowledgeExtractor（结构化输出 + 退避重试 +
          照抄原文 ≥VERBATIM_CHARS 字的描述在写缓存前清空 + 按提示词版本分目录的磁盘缓存）、CachedExtractor（只读缓存、零费用重组装）、cache_path()、
          parse_extraction() / store_extraction()（任何抽取器的产出入缓存的唯一入口：截取 JSON → 契约校验 → 防抄清洗 → 写入）、
          SeedingResult 与 SeedingPipeline（语料 → 切块 → 并发抽取 → 组装蓝图 → 编译 Cypher）
[POS]: infrastructure 的原著解析管道（World Seeding 的前半程）：大模型在这里只做"读书抽取"这一件无状态的事，
       产出的是名称级的原始记录；实体消歧、引用落地、拓扑补全与"宁严勿宽"的封存都在 blueprint_assembler 中确定性地完成。
       时间锚点 T=0 是契约的第一原则：状态字段一律写开篇那一刻，开篇之后发生的事写进 events——事件是证据而非状态，
       组装器凭它否决被时间线污染的开篇状态（段誉不会开篇就身负北冥神功）。
       v7 据真实 Gemini 实测改了四处：本名只要出现过一次就作 name（flash 曾让「南海鳄神」篡位、把「岳老三」塞进别名）、
       T=0 所在不在本段即填 null 而不补造地点、门槛与获取地点只记原文写明的、events 也记转述与"已身负"的证据（flash 召回曾只有 1/9）；
       生产抽取器还拿到与 Claude 抽取员同一份跨块命名参考——本名能否跨块合并，主要靠它。
       任何一个块抽取失败只记入报告，不拖垮整本书；全部失败才视为管道失败。
       缓存是抽取器之间的交换契约：大模型、子代理、人工都可以当抽取器，产出经 store_extraction 同一道闸门入缓存，组装器一视同仁；
       契约升级不废旧缓存——v4 / v5 记录读入时自动升级为新形状，零费用复现旧蓝图
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import asyncio
import hashlib
import json
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError, model_validator

from app.application.ports import LLMClient
from app.domain.models import CharacterStatus, Disposition, RelationKind, Tier, Transmission, WorldBlueprint
from app.errors import ExtractionError, LLMError
from app.infrastructure.blueprint_assembler import AssemblyReport, BlueprintAssembler, CanonEventKind
from app.infrastructure.cypher import CypherStatement, compile_blueprint, render_script

logger = logging.getLogger(__name__)

PROMPT_VERSION = "tlbb-extract-v7"  # 改动抽取提示词时递增，使磁盘缓存整体失效
_ENCODINGS = ("utf-8-sig", "gb18030")
_NUMERAL = "一二三四五六七八九十百零〇两"
# 回目两种写法：「第一回 青衫磊落险峰行」与新修版的「一 青衫磊落险峰行」（标题是诗句，可含空格，不含句读）
_CHAPTER = re.compile(
    rf"^\s*(?:第[{_NUMERAL}\d]+[回章节](?:[ \u3000]+[^。，、！？：；“”「」]{{0,30}})?"
    rf"|[{_NUMERAL}]{{1,4}}[ \u3000]+[^。，、！？：；“”「」]{{2,30}})\s*$"
)
_END = re.compile(r"^\s*(?:（全书完）|\(全书完\)|全书完|后记|附录)[ \u3000]?\S{0,12}\s*$")
_CJK = re.compile(r"[\u4e00-\u9fff]")
_WATERMARK = re.compile(r"[★☆](?:[Ａ-Ｚａ-ｚ０-９A-Za-z0-9][★☆])+")  # 「★Ｄ★Ｏ★Ｓ★Ｐ★Ｙ★」式水印，偶尔黏在正文行尾


# ============================================================
#  语料
# ============================================================
@dataclass(frozen=True, slots=True)
class SourceDocument:
    name: str
    text: str


def _decode(raw: bytes, name: str) -> str:
    for encoding in _ENCODINGS:  # 中文小说的 TXT 多为 GBK 系，UTF-8 失败即回退 GB18030（GBK 的超集）
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ExtractionError(f"无法识别 {name} 的编码（已尝试 {' / '.join(_ENCODINGS)}）")


def clean_text(text: str) -> str:
    """
    语料清洗：先剥掉黏在正文里的水印，不含汉字的行（分隔线、整行水印）一律去掉；有回目时只留正文——首回之前的序言、释名，
    「全书完」之后的后记、附录，写的是作者与读者，不是这个世界，抽进来只会让「夜叉」「乾达婆」成了江湖人物。
    """
    raw = _WATERMARK.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    lines = [line.rstrip() for line in raw.split("\n") if _CJK.search(line)]
    start = next((i for i, line in enumerate(lines) if _CHAPTER.match(line)), None)
    if start is None:
        return "\n".join(lines)
    body = lines[start:]
    end = next((i for i, line in enumerate(body) if _END.match(line)), len(body))
    return "\n".join(body[:end])


def load_corpus(source_dir: Path) -> list[SourceDocument]:
    files = sorted(p for p in source_dir.glob("*.txt") if p.is_file())
    if not files:
        raise ExtractionError(f"{source_dir} 中没有 .txt 原著文本：请把《天龙八部》全文放入该目录")
    return [SourceDocument(p.name, clean_text(_decode(p.read_bytes(), p.name))) for p in files]


# ============================================================
#  切块 —— 回目是硬边界，段落是软边界，超长段落硬切
# ============================================================
@dataclass(frozen=True, slots=True)
class Chunk:
    source: str
    index: int
    text: str


def chunk_text(doc: SourceDocument, max_chars: int) -> list[Chunk]:
    blocks: list[str] = []
    current: list[str] = []
    size = 0

    def flush() -> None:
        nonlocal current, size
        if current:
            blocks.append("\n".join(current))
        current, size = [], 0

    for line in (ln.strip() for ln in doc.text.split("\n")):
        if not line:
            continue
        if _CHAPTER.match(line):
            flush()
        while len(line) > max_chars:  # 超长段落：先清空当前块，再按上限硬切
            flush()
            blocks.append(line[:max_chars])
            line = line[max_chars:]
        if size + len(line) + 1 > max_chars:
            flush()
        current.append(line)
        size += len(line) + 1
    flush()
    return [Chunk(doc.name, i, block) for i, block in enumerate(blocks)]


# ============================================================
#  抽取契约 —— 名称级原始记录；枚举宽容（不认识的值退回缺省），免得一个形容词让整块重采样。
#  境界 / 性情 / 生死"看不出"即为 None：未知 ≠ 最弱——否则开篇一句没写武功的旁白，会在"首次登场即开篇"的组装里
#  盖掉后文写明的「一流」
# ============================================================
def _lenient[E: StrEnum](enum: type[E], fallback: E | None) -> Callable[[Any], E | None]:
    def coerce(value: Any) -> E | None:
        try:
            return enum(value)
        except ValueError:
            return fallback

    return coerce


type _Tier = Annotated[Tier, BeforeValidator(_lenient(Tier, Tier.NONE))]
type _MaybeTier = Annotated[Tier | None, BeforeValidator(_lenient(Tier, None))]


class _Raw(BaseModel):
    model_config = ConfigDict(extra="ignore")


class RawExit(_Raw):
    label: str = Field(description="出口方位或路径，如「北上」「出城门」「下崖」")
    destination: str = Field(description="通往的地点正名")


class RawLocation(_Raw):
    name: str
    aliases: list[str] = []
    region: str = ""
    parent: str | None = Field(default=None, description="上级地点的 name：厅堂房间所在的宅院宫观、谷中某处所在的山谷")
    description: str = ""
    exits: list[RawExit] = []


class RawCharacter(_Raw):
    name: str = Field(description="本名：姓名或法号；本段只以称号出现、不知本名时填最常用的称号，并令 name_is_title 为 true")
    name_is_title: bool = Field(default=False, description="name 填的是称号而非本名时为 true")
    titles: list[str] = Field(default=[], description="江湖称号、绰号、名号，如「恶贯满盈」「南海鳄神」")
    aliases: list[str] = Field(default=[], description="化名、旧称、封号、字号，如「延庆太子」「保定帝」")
    faction: str = ""
    status: Annotated[CharacterStatus | None, BeforeValidator(_lenient(CharacterStatus, None))] = None
    tier: _MaybeTier = None
    disposition: Annotated[Disposition | None, BeforeValidator(_lenient(Disposition, None))] = None
    location: str | None = None
    skills: list[str] = []
    description: str = ""


class RawAcquisition(_Raw):
    """获取要求：得其门径的条件。"""

    transmission: Annotated[Transmission, BeforeValidator(_lenient(Transmission, Transmission.TEACHER))] = Field(
        default=Transmission.TEACHER, description="师传 / 自悟；自悟须在 items 写明所凭典籍"
    )
    items: list[str] = Field(default=[], description="自悟所凭、或入门须持之物（秘籍、图谱、信物）的 name")
    location: str | None = Field(default=None, description="须身处何地方得门径（典籍所藏、传功之所）")


class RawPractice(_Raw):
    """修炼要求：根基够不够。"""

    skills: list[str] = Field(default=[], description="须先练出根基的武学 name")
    min_tier: _Tier = Field(default=Tier.NONE, description="修炼者须有的境界")
    conflicts: list[str] = Field(default=[], description="与之相冲的武学 name")


_ACQUISITION_KEYS = ("transmission", "items", "location")
_PRACTICE_KEYS = ("skills", "min_tier", "conflicts")


class RawMartialArt(_Raw):
    name: str
    aliases: list[str] = []
    faction: str = ""
    kind: str = ""
    tier: _MaybeTier = None
    description: str = ""
    acquisition: RawAcquisition = RawAcquisition()
    practice: RawPractice = RawPractice()

    @model_validator(mode="before")
    @classmethod
    def _upgrade_prerequisites(cls, data: Any) -> Any:
        """v4 / v5 缓存只有一个 prerequisites：按字段拆进两道门——旧缓存零费用复现旧蓝图的承诺不因契约升级而作废。"""
        if not isinstance(data, dict) or not isinstance(old := data.get("prerequisites"), dict):
            return data
        upgraded = {k: v for k, v in data.items() if k != "prerequisites"}
        upgraded.setdefault("acquisition", {k: old[k] for k in _ACQUISITION_KEYS if k in old})
        upgraded.setdefault("practice", {k: old[k] for k in _PRACTICE_KEYS if k in old})
        return upgraded


class RawItem(_Raw):
    name: str
    aliases: list[str] = []
    kind: str = ""
    description: str = ""
    owner: str | None = None
    location: str | None = None


class RawRelation(_Raw):
    source: str
    target: str
    kind: Annotated[RelationKind | None, BeforeValidator(_lenient(RelationKind, None))] = None  # 认不出的关系不猜
    note: str = ""


# 事件是证据而不是状态：组装器只拿它否决被时间线污染的开篇状态，它本身不进蓝图。
# 认不出的种类即 None，组装时丢弃——宁可漏掉一条证据，也不让一个形容词否决开篇状态。
# （类文档串会成为 JSON Schema 的 description 交给抽取器，所以只写给抽取器看的话）
class RawCanonEvent(_Raw):
    """开篇（T=0）之后改变了人物武学、物品归属或生死的一件事。"""

    subject: str = Field(description="人物 name")
    kind: Annotated[CanonEventKind | None, BeforeValidator(_lenient(CanonEventKind, None))] = Field(
        default=None, description="只能是 习得武学 / 得到物品 / 身故"
    )
    object: str | None = Field(default=None, description="习得的武学或得到的物品的 name；身故可空")
    note: str = ""


class ChunkExtraction(_Raw):
    locations: list[RawLocation] = []
    characters: list[RawCharacter] = []
    martial_arts: list[RawMartialArt] = []
    items: list[RawItem] = []
    relations: list[RawRelation] = []
    events: list[RawCanonEvent] = []


EXTRACTION_SCHEMA = ChunkExtraction.model_json_schema()

# 世界的初始图谱定格于此：抽取器与自愈代理（graph_linter）共用同一个锚点，状态字段才有同一个"此刻"
T0_ANCHOR = "T=0：原著开篇，段誉刚离家出走之时——他正在无量山剑湖宫旁观东西二宗比剑。"

EXTRACTION_SYSTEM = f"""你是《天龙八部》原著知识抽取器。你读一段原著文本，只抽取这段文本里明确出现的地点、人物、武学、物品与人物关系，以及开篇之后改变了状态的事件，输出 JSON。

时间锚点（凌驾于下列一切铁律之上）：
{T0_ANCHOR}
世界的初始图谱定格在 T=0。人物的 location / skills / status 与物品的 owner / location 一律写 T=0 时的状态：
- 在 T=0 尚未发生的事（例：段誉跌入无量山崖下石洞、得到北冥神功卷轴、学会北冥神功与凌波微步）绝对不允许写进这些状态字段，而是写进 events 数组；
- T=0 之前就已身负的武功（开篇前就是高手，或本段写明早年练成）照常写进 skills；
- T=0 时尚未登场的人物，location 写其本段首次出现之处；物品同理，写首次出现时的静置之处或原主。

铁律：
1. 只抽取本段文本写到的东西。文本没写的不补，原著后文的情节不提前写进来，你的常识不是原文。
2. 名字必须能脱离本段唯一认出所指：
   - 人物三名分立：name 填本名（姓名或法号——此人真正叫什么）；titles 填江湖称号、绰号、名号（「恶贯满盈」「南海鳄神」「无恶不作」）；
     aliases 填化名、旧称、封号、字号（「延庆太子」「保定帝」）。
     本名在本段只要出现过一次，name 就填本名、name_is_title 为 false，称号一律进 titles——不论称号用得多频繁
     （例：通篇称「南海鳄神」、只有一两处叫「岳老三」，name 填「岳老三」，他自封的排行「岳老二」进 aliases）。
     本段从未出现本名、只以称号出现的：name 填最常用的那个称号，并令 name_is_title 为 true，titles 里不再重复它。
     只知亲属称谓、排行、身份泛称的（「爹爹」「妈妈」「老大」「夫人」「帮主」「那少女」）不要抽取此人；titles / aliases 同样不收亲属称谓、排行、泛称、外貌描写。
   - 武学只抽有专名的（如「一阳指」「凌波微步」），「轻功」「内功」「剑法」「掌法」这类泛称不抽；同一门武功只用一个名字。
3. 地点 name 必须是独立可认的地名（「无量山」「剑湖宫」「大理城」「镇南王府」）。厅堂房间、谷中山中的某处，写成「上级地名·处所」
   （「剑湖宫·练武厅」「镇南王府·书房」），parent 填上级地名，上级地点也要列入 locations；「院子」「卧室」「山溪」「树林」这类泛称不得单独作 name。
   exits 只在文本写明两地相通或有人从一地行至另一地时记录；label 写简短的方位或路径（不超过 8 字，如「北上」「出城门」「下崖」），destination 写目的地 name。
4. 人物：location 写此人 T=0 时所在之地（T=0 时尚未登场的，写本段首次出现之处）；T=0 时已登场、而其 T=0 所在本段没有写到的，
   location 填 null，不要为凑块内自洽把本段没写到的地点补进 locations；faction 写门派或阵营；
   status 只能是 健在 / 已故，写 T=0 时的生死——T=0 之后才死的仍是 健在，死写进 events；
   tier 依描写判断武功境界，只能是 不入流 / 三流 / 二流 / 一流 / 绝顶，明写不会武功才填 不入流；
   disposition 依其为人判断，只能是 仁厚 / 中庸 / 狠辣；
   status / tier / disposition 本段看不出来就填 null，绝不要猜——不知道不等于最弱；
   skills 只列此人 T=0 时已身负的武学；T=0 之后才学会的不列，写进 events。
5. 武学 tier 同样依描写判断，看不出填 null。武学有两道门，只记文本写明的：
   - acquisition 是获取要求（得其门径）：transmission 填 师传 / 自悟——文本写明可凭典籍自行参悟的填 自悟，且 items 必须写明所凭的秘籍图谱，否则一律 师传；
     items 写自悟所凭、或入门须持之物；location 只在文本写明须亲至某处方得门径时填（典籍可随身带走的不填）。
   - practice 是修炼要求：skills 写须先练出根基的武学，min_tier 只在文本写明修炼须有某等功力时填（没写就填 不入流），conflicts 写与之相冲的武学。
6. 物品：owner 是 T=0 时的物主，location 是 T=0 时静置之处；随身携带则只填 owner。T=0 之后才易手的，易手写进 events。
7. 人物关系 kind 只能是 亲族 / 师徒 / 同门 / 结义 / 主仆 / 情侣 / 仇敌；取文本中最突出的一种——同门反目、彼此为敌者记 仇敌。
8. events 记 T=0 之后改变了上述状态的事，它是证据而非状态：kind 只能是 习得武学 / 得到物品 / 身故；subject 写人物 name，
   object 写习得的武学或得到的物品的 name（身故可留空）；note 用自己的话概括这件事。以下三种都要记：
   - 本段发生的（开始修习、只练成一部分也算 习得武学）；
   - 本段转述的（「某某已给人害了」记 身故）；
   - 本段显示某人已身负 / 持有 T=0 时尚未拥有的武学或物品（例：段誉在本段施展凌波微步、以北冥神功吸人内力——即使习得发生在前文，也记一条 习得武学）。
   事件里的人物即使在本段只被提及，也要列入 characters（location 可为 null）。
9. 块内自洽：人物与物品的 location、出口的 destination、武学获取要求里的地点，都必须是本段 locations 数组里列出的某个地点的 name 或别名；
   人物 skills、武学修炼要求里的武学、获取要求里的典籍，也必须分别出现在本段 martial_arts / items 数组里；
   events 的 subject 必须出现在本段 characters 里，object 必须出现在本段 martial_arts / items 里。不要写数组里没有的名字。
10. 所有 description 与 note 用你自己的话概括，不超过 40 字，不得照抄原文的句子。
11. 这一段若没有某类实体，对应数组留空。只输出 JSON，不要任何解释。

输出必须符合此 JSON Schema：
{json.dumps(EXTRACTION_SCHEMA, ensure_ascii=False)}"""


def escape_markup(text: str) -> str:
    """插进提示词标签里的一切文本都过这一道：原文与名字里的尖括号不能闭合我们的标签（自愈代理同用）。"""
    return text.replace("<", "＜").replace(">", "＞")


# ============================================================
#  抽取器
# ============================================================
class KnowledgeExtractor(ABC):
    @abstractmethod
    async def extract(self, chunk: Chunk) -> ChunkExtraction: ...


def cache_path(cache_dir: Path, version: str, chunk: Chunk) -> Path:
    """缓存键 = 提示词版本 + 块文本：版本分目录存放，旧版记录留作溯源，换提示词不会读到旧口径的记录。"""
    digest = hashlib.sha256(f"{version}\n{chunk.text}".encode()).hexdigest()[:32]
    return cache_dir / version / f"{digest}.json"


class CachedExtractor(KnowledgeExtractor):
    """
    只读缓存的抽取器：组装器改了规则，凭已抽的记录零费用重新组装，不调大模型；缺块即失败。
    早于防抄清洗写下的旧版记录，读入时补做清洗并回写。
    """

    def __init__(self, cache_dir: Path, version: str = PROMPT_VERSION) -> None:
        self._cache_dir = cache_dir
        self._version = version

    async def extract(self, chunk: Chunk) -> ChunkExtraction:
        path = cache_path(self._cache_dir, self._version, chunk)
        if not path.exists():
            raise ExtractionError(f"缓存中没有 {chunk.source}#{chunk.index}（{self._version}）")
        result = ChunkExtraction.model_validate_json(path.read_text(encoding="utf-8"))
        if _paraphrase_only(result, chunk.text):
            path.write_text(result.model_dump_json(), encoding="utf-8")
        return result


class LLMKnowledgeExtractor(KnowledgeExtractor):
    """
    输出不合契约、或厂商一时失灵（限流 / 超时 / 5xx）都重来，退避翻倍；整本书几百次调用，偶发失败不该靠人重跑。
    naming 是跨块命名参考（naming_reference 由上一版蓝图生成），附在抽取铁律之后：它只是写法上的提示，不进缓存键。
    """

    def __init__(
        self, llm: LLMClient, *, cache_dir: Path | None = None, attempts: int = 3, backoff: float = 5.0, naming: str = ""
    ) -> None:
        self._llm = llm
        self._cache_dir = cache_dir
        self._attempts = attempts
        self._backoff = backoff
        self._system = f"{EXTRACTION_SYSTEM}\n\n{naming}" if naming else EXTRACTION_SYSTEM

    def _cache_path(self, chunk: Chunk) -> Path | None:
        return cache_path(self._cache_dir, PROMPT_VERSION, chunk) if self._cache_dir is not None else None

    async def extract(self, chunk: Chunk) -> ChunkExtraction:
        cached = self._cache_path(chunk)
        if cached is not None and cached.exists():
            return ChunkExtraction.model_validate_json(cached.read_text(encoding="utf-8"))
        user = chunk_message(chunk)
        last_error: Exception | None = None
        for attempt in range(self._attempts):
            if attempt:
                await asyncio.sleep(self._backoff * 2 ** (attempt - 1))
            try:
                result = parse_extraction(await self._llm.complete(self._system, user, EXTRACTION_SCHEMA))
            except (LLMError, ValueError, ValidationError) as exc:
                logger.warning("%s#%d 第 %d 次抽取失败：%s", chunk.source, chunk.index, attempt + 1, exc)
                last_error = exc
                if isinstance(exc, LLMError) and not exc.retryable:
                    break  # 欠费、鉴权失败：重试只会再失败一次
                continue
            _sanitize_and_save(result, chunk, cached)
            return result
        raise ExtractionError(f"{chunk.source}#{chunk.index} 抽取失败：{last_error}")


def naming_reference(bp: WorldBlueprint) -> str:
    """
    跨块命名参考：上一版蓝图里已定案的写法（地点与上级、人物的本名｜称号｜别名、武学、物品、门派）。
    逐块抽取看不到别的块：只叫「延庆太子」的一块与只叫「恶贯满盈」的一块各说各话，有了它才合得到同一个本名之下。
    实测这是 Claude 抽取员在本名上胜过裸跑 Gemini 的主因——生产抽取器也该拿到同一份。
    """
    parent = {target: loc.name for loc in bp.locations for label, target in loc.exits.items() if label.startswith("入")}
    places = [f"- {loc.name}" + (f"  ← {parent[loc.id]}" if loc.id in parent else "") for loc in bp.locations]
    people = [f"- {c.true_name} ｜ {'、'.join(c.titles) or '—'} ｜ {'、'.join(c.aliases) or '—'}" for c in bp.characters]
    factions = sorted({c.faction for c in bp.characters if c.faction} | {a.faction for a in bp.martial_arts if a.faction})
    return "\n".join([
        "跨块命名参考：本段写到的若正是下列地方 / 人物 / 武功 / 物品，沿用这里的写法，便于跨块合并；本段没写到的一概不要加，",
        "本段写法与此不同而确属另一处 / 另一人的，照本段写。",
        "地点（name ← parent）：", *places,
        "人物（本名 ｜ 称号 ｜ 别名）：", *people,
        "武学：" + "、".join(a.name for a in bp.martial_arts),
        "物品：" + "、".join(i.name for i in bp.items),
        "门派：" + "、".join(factions),
    ])


def chunk_message(chunk: Chunk) -> str:
    """交给抽取器的那条用户消息：块文本转义后包进 <chunk> 标签。"""
    return f'<chunk source="{escape_markup(chunk.source)}" index="{chunk.index}">\n{escape_markup(chunk.text)}\n</chunk>'


def parse_extraction(raw: str) -> ChunkExtraction:
    """抽取器原始输出 → 契约对象。不合契约抛 ValueError / ValidationError。"""
    return ChunkExtraction.model_validate(_json_object(raw))


def _sanitize_and_save(result: ChunkExtraction, chunk: Chunk, path: Path | None) -> int:
    if blanked := _paraphrase_only(result, chunk.text):
        logger.info("%s#%d 有 %d 条描述照抄原文，已清空", chunk.source, chunk.index, blanked)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(result.model_dump_json(), encoding="utf-8")
    return blanked


def store_extraction(cache_dir: Path, chunk: Chunk, raw: str, version: str = PROMPT_VERSION) -> tuple[ChunkExtraction, int]:
    """
    外部抽取器（子代理、人工、别家模型的离线批处理）的产出入缓存：与 LLMKnowledgeExtractor 走同一道闸门——
    契约校验、防抄清洗、写入该版本缓存。返回（记录, 被清空的描述条数）；不合契约抛 ExtractionError。
    """
    try:
        result = parse_extraction(raw)
    except (ValueError, ValidationError) as exc:
        raise ExtractionError(f"{chunk.source}#{chunk.index} 的抽取结果不合契约：{exc}") from exc
    return result, _sanitize_and_save(result, chunk, cache_path(cache_dir, version, chunk))


VERBATIM_CHARS = 16


def _copies(text: str, source: str, n: int = VERBATIM_CHARS) -> bool:
    flat = text.replace("\u3000", "").replace(" ", "")
    return any(flat[i : i + n] in source for i in range(len(flat) - n + 1))


def _paraphrase_only(result: ChunkExtraction, source: str) -> int:
    """
    抽取记录里只许有转述，不许有原文：与本块原文共享 ≥16 字连续片段的描述一律清空。
    清洗发生在写缓存之前——缓存与蓝图都会入库，原著的句子一句也不该跟着进去。
    """
    flat = source.replace("\n", "").replace("\u3000", "").replace(" ", "")
    records: list[Any] = [*result.locations, *result.characters, *result.martial_arts, *result.items]
    blanked = 0
    for record in records:
        if record.description and _copies(record.description, flat):
            record.description, blanked = "", blanked + 1
    noted_records: list[RawRelation | RawCanonEvent] = [*result.relations, *result.events]
    for noted in noted_records:
        if noted.note and _copies(noted.note, flat):
            noted.note, blanked = "", blanked + 1
    return blanked


def _json_object(raw: str) -> Any:
    """容忍围栏与寒暄：截取第一个 { 到最后一个 } 之间的内容。"""
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("输出中没有 JSON 对象")
    return json.loads(raw[start : end + 1])


# ============================================================
#  管道
# ============================================================
@dataclass(frozen=True, slots=True)
class SeedingResult:
    blueprint: WorldBlueprint
    report: AssemblyReport
    statements: list[CypherStatement]

    @property
    def script(self) -> str:
        return render_script(self.statements)


class SeedingPipeline:
    def __init__(
        self,
        extractor: KnowledgeExtractor,
        *,
        assembler: BlueprintAssembler | None = None,
        chunk_chars: int = 6000,
        concurrency: int = 4,
    ) -> None:
        self._extractor = extractor
        self._assembler = assembler or BlueprintAssembler()
        self._chunk_chars = chunk_chars
        self._concurrency = concurrency

    def chunks(self, documents: Sequence[SourceDocument], max_chunks: int | None = None) -> list[Chunk]:
        chunks = [c for doc in documents for c in chunk_text(doc, self._chunk_chars)]
        return chunks[:max_chunks] if max_chunks else chunks

    async def run(self, documents: Sequence[SourceDocument], *, max_chunks: int | None = None) -> SeedingResult:
        chunks = self.chunks(documents, max_chunks)
        gate = asyncio.Semaphore(self._concurrency)
        failures: list[str] = []

        done = 0

        async def one(chunk: Chunk) -> ChunkExtraction | None:
            nonlocal done
            async with gate:
                try:
                    return await self._extractor.extract(chunk)
                except (ExtractionError, LLMError) as exc:
                    logger.warning("抽取失败 %s#%d：%s", chunk.source, chunk.index, exc)
                    failures.append(f"{chunk.source}#{chunk.index}：{exc}")
                    return None
                finally:
                    done += 1
                    logger.info("抽取进度 %d/%d（%s#%d）", done, len(chunks), chunk.source, chunk.index)

        # gather 保序：组装器的"首次登场即开篇状态"依赖原著的先后
        results = await asyncio.gather(*(one(c) for c in chunks))
        extractions = [r for r in results if r is not None]
        if not extractions:
            raise ExtractionError(f"全部 {len(chunks)} 个文本块抽取失败，管道中止")
        blueprint, report = self._assembler.assemble(extractions)
        report.failed_chunks.extend(failures)
        return SeedingResult(blueprint, report, compile_blueprint(blueprint))
