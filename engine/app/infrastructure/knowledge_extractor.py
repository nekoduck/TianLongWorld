"""
[INPUT]: 依赖 application/ports 的 LLMClient，依赖 domain/models 的本体枚举与 WorldBlueprint，
         依赖 infrastructure/blueprint_assembler 的 BlueprintAssembler / AssemblyReport，依赖 infrastructure/cypher 的 compile_blueprint / render_script，
         依赖 app.errors 的 ExtractionError / LLMError
[OUTPUT]: 对外提供 SourceDocument / load_corpus()（读取 data/source_text，UTF-8 → GB18030 回退，经 clean_text 只留正文）、clean_text()（去水印、去序跋）、
          Chunk / chunk_text()（按回目与段落切块）、
          抽取契约 ChunkExtraction（Raw* 名称级记录，地点带 parent 上级）、KnowledgeExtractor 抽象与 LLMKnowledgeExtractor（结构化输出 + 退避重试 +
          照抄原文 ≥VERBATIM_CHARS 字的描述在写缓存前清空 + 按提示词版本分目录的磁盘缓存）、CachedExtractor（只读缓存、零费用重组装）、cache_path()、
          parse_extraction() / store_extraction()（任何抽取器的产出入缓存的唯一入口：截取 JSON → 契约校验 → 防抄清洗 → 写入）、
          SeedingResult 与 SeedingPipeline（语料 → 切块 → 并发抽取 → 组装蓝图 → 编译 Cypher）
[POS]: infrastructure 的原著解析管道（World Seeding 的前半程）：大模型在这里只做"读书抽取"这一件无状态的事，
       产出的是名称级的原始记录；实体消歧、引用落地、拓扑补全与"宁严勿宽"的封存都在 blueprint_assembler 中确定性地完成。
       任何一个块抽取失败只记入报告，不拖垮整本书；全部失败才视为管道失败。
       缓存是抽取器之间的交换契约：大模型、子代理、人工都可以当抽取器，产出经 store_extraction 同一道闸门入缓存，组装器一视同仁
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

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError

from app.application.ports import LLMClient
from app.domain.models import CharacterStatus, Disposition, RelationKind, Tier, Transmission, WorldBlueprint
from app.errors import ExtractionError, LLMError
from app.infrastructure.blueprint_assembler import AssemblyReport, BlueprintAssembler
from app.infrastructure.cypher import CypherStatement, compile_blueprint, render_script

logger = logging.getLogger(__name__)

PROMPT_VERSION = "tlbb-extract-v5"  # 改动抽取提示词时递增，使磁盘缓存整体失效
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
    name: str
    aliases: list[str] = []
    faction: str = ""
    status: Annotated[CharacterStatus | None, BeforeValidator(_lenient(CharacterStatus, None))] = None
    tier: _MaybeTier = None
    disposition: Annotated[Disposition | None, BeforeValidator(_lenient(Disposition, None))] = None
    location: str | None = None
    skills: list[str] = []
    description: str = ""


class RawPrerequisites(_Raw):
    skills: list[str] = []
    items: list[str] = []
    location: str | None = None
    min_tier: _Tier = Tier.NONE
    conflicts: list[str] = []
    transmission: Annotated[Transmission, BeforeValidator(_lenient(Transmission, Transmission.TEACHER))] = (
        Transmission.TEACHER
    )


class RawMartialArt(_Raw):
    name: str
    aliases: list[str] = []
    faction: str = ""
    kind: str = ""
    tier: _MaybeTier = None
    description: str = ""
    prerequisites: RawPrerequisites = RawPrerequisites()


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


class ChunkExtraction(_Raw):
    locations: list[RawLocation] = []
    characters: list[RawCharacter] = []
    martial_arts: list[RawMartialArt] = []
    items: list[RawItem] = []
    relations: list[RawRelation] = []


EXTRACTION_SCHEMA = ChunkExtraction.model_json_schema()

EXTRACTION_SYSTEM = f"""你是《天龙八部》原著知识抽取器。你读一段原著文本，只抽取这段文本里明确出现的地点、人物、武学、物品与人物关系，输出 JSON。

铁律：
1. 只抽取本段文本写到的东西。文本没写的不补，原著后文的情节不提前写进来，你的常识不是原文。
2. 名字必须能脱离本段唯一认出所指：
   - 人物 name 填姓名。只知称谓不知姓名的（「爹爹」「妈妈」「老大」「夫人」「帮主」「那少女」）不要抽取此人；
     aliases 只收能唯一指向此人的专称（字号、绰号、名号、封号，如「保定帝」「无恶不作」），亲属称谓、排行、身份泛称、外貌描写一律不收。
   - 武学只抽有专名的（如「一阳指」「凌波微步」），「轻功」「内功」「剑法」「掌法」这类泛称不抽；同一门武功只用一个名字。
3. 地点 name 必须是独立可认的地名（「无量山」「剑湖宫」「大理城」「镇南王府」）。厅堂房间、谷中山中的某处，写成「上级地名·处所」
   （「剑湖宫·练武厅」「镇南王府·书房」），parent 填上级地名，上级地点也要列入 locations；「院子」「卧室」「山溪」「树林」这类泛称不得单独作 name。
   exits 只在文本写明两地相通或有人从一地行至另一地时记录；label 写简短的方位或路径（不超过 8 字，如「北上」「出城门」「下崖」），destination 写目的地 name。
4. 时间切片：人物与物品的状态一律以它在本段首次出现时为准，本段后来才发生的变化（学会了什么、走到了哪、东西落到谁手里）不写。
5. 人物：location 写此人首次出现时所在之地；faction 写门派或阵营；status 只能是 健在 / 已故；
   tier 依描写判断武功境界，只能是 不入流 / 三流 / 二流 / 一流 / 绝顶，明写不会武功才填 不入流；
   disposition 依其为人判断，只能是 仁厚 / 中庸 / 狠辣；
   status / tier / disposition 本段看不出来就填 null，绝不要猜——不知道不等于最弱；
   skills 只列此人施展过、或本段开始前就已身负的武学，本段中才学会的不列。
6. 武学 tier 同样依描写判断，看不出填 null。prerequisites 是修习它的硬性条件，只记文本写明的：须先通晓的武学（skills）、须持有的秘籍图谱（items）、
   须在何地修习（location）、修习者须有的境界（min_tier）、与之相冲的武学（conflicts）；
   transmission：文本写明可凭典籍自行参悟的填 自悟，且 items 必须写明所凭的秘籍图谱；否则一律 师传。
7. 物品：owner 是物主，location 是它首次出现时静置之处；随身携带则只填 owner。
8. 人物关系 kind 只能是 亲族 / 师徒 / 同门 / 结义 / 主仆 / 情侣 / 仇敌；取文本中最突出的一种——同门反目、彼此为敌者记 仇敌。
9. 块内自洽：人物与物品的 location、出口的 destination、武学前置里的地点，都必须是本段 locations 数组里列出的某个地点的 name 或别名；
   人物 skills 与武学前置里的武学、前置里的典籍，也必须分别出现在本段 martial_arts / items 数组里。不要写数组里没有的名字。
10. 所有 description 与 note 用你自己的话概括，不超过 40 字，不得照抄原文的句子。
11. 这一段若没有某类实体，对应数组留空。只输出 JSON，不要任何解释。

输出必须符合此 JSON Schema：
{json.dumps(EXTRACTION_SCHEMA, ensure_ascii=False)}"""


def _escape(text: str) -> str:
    return text.replace("<", "＜").replace(">", "＞")  # 原文里的尖括号不能闭合我们的标签


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
    """输出不合契约、或厂商一时失灵（限流 / 超时 / 5xx）都重来，退避翻倍；整本书几百次调用，偶发失败不该靠人重跑。"""

    def __init__(
        self, llm: LLMClient, *, cache_dir: Path | None = None, attempts: int = 3, backoff: float = 5.0
    ) -> None:
        self._llm = llm
        self._cache_dir = cache_dir
        self._attempts = attempts
        self._backoff = backoff

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
                result = parse_extraction(await self._llm.complete(EXTRACTION_SYSTEM, user, EXTRACTION_SCHEMA))
            except (LLMError, ValueError, ValidationError) as exc:
                logger.warning("%s#%d 第 %d 次抽取失败：%s", chunk.source, chunk.index, attempt + 1, exc)
                last_error = exc
                if isinstance(exc, LLMError) and not exc.retryable:
                    break  # 欠费、鉴权失败：重试只会再失败一次
                continue
            _sanitize_and_save(result, chunk, cached)
            return result
        raise ExtractionError(f"{chunk.source}#{chunk.index} 抽取失败：{last_error}")


def chunk_message(chunk: Chunk) -> str:
    """交给抽取器的那条用户消息：块文本转义后包进 <chunk> 标签。"""
    return f'<chunk source="{_escape(chunk.source)}" index="{chunk.index}">\n{_escape(chunk.text)}\n</chunk>'


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
    for relation in result.relations:
        if relation.note and _copies(relation.note, flat):
            relation.note, blanked = "", blanked + 1
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
