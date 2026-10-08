"""
[INPUT]: 依赖 application/ports 的 LLMClient，依赖 domain/models 的本体枚举与 WorldBlueprint，
         依赖 infrastructure/blueprint_assembler 的 BlueprintAssembler / AssemblyReport，依赖 infrastructure/cypher 的 compile_blueprint / render_script，
         依赖 app.errors 的 ExtractionError / LLMError
[OUTPUT]: 对外提供 SourceDocument / load_corpus()（读取 data/source_text，UTF-8 → GB18030 回退）、Chunk / chunk_text()（按回目与段落切块）、
          抽取契约 ChunkExtraction（Raw* 名称级记录）、KnowledgeExtractor 抽象与 LLMKnowledgeExtractor（结构化输出 + 重采样 + 磁盘缓存）、
          SeedingResult 与 SeedingPipeline（语料 → 切块 → 并发抽取 → 组装蓝图 → 编译 Cypher）
[POS]: infrastructure 的原著解析管道（World Seeding 的前半程）：大模型在这里只做"读书抽取"这一件无状态的事，
       产出的是名称级的原始记录；实体消歧、引用落地、拓扑补全与"宁严勿宽"的封存都在 blueprint_assembler 中确定性地完成。
       任何一个块抽取失败只记入报告，不拖垮整本书；全部失败才视为管道失败
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

PROMPT_VERSION = "tlbb-extract-v1"  # 改动抽取提示词时递增，使磁盘缓存整体失效
_ENCODINGS = ("utf-8-sig", "gb18030")
_CHAPTER = re.compile(r"^\s*第[一二三四五六七八九十百零〇两\d]+[回章]")


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


def load_corpus(source_dir: Path) -> list[SourceDocument]:
    files = sorted(p for p in source_dir.glob("*.txt") if p.is_file())
    if not files:
        raise ExtractionError(f"{source_dir} 中没有 .txt 原著文本：请把《天龙八部》全文放入该目录")
    return [
        SourceDocument(p.name, _decode(p.read_bytes(), p.name).replace("\r\n", "\n").replace("\r", "\n"))
        for p in files
    ]


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
#  抽取契约 —— 名称级原始记录；枚举宽容（不认识的值退回缺省），免得一个形容词让整块重采样
# ============================================================
def _lenient[E: StrEnum](enum: type[E], fallback: E | None) -> Callable[[Any], E | None]:
    def coerce(value: Any) -> E | None:
        try:
            return enum(value)
        except ValueError:
            return fallback

    return coerce


type _Tier = Annotated[Tier, BeforeValidator(_lenient(Tier, Tier.NONE))]


class _Raw(BaseModel):
    model_config = ConfigDict(extra="ignore")


class RawExit(_Raw):
    label: str = Field(description="出口方位或路径，如「北上」「出城门」「下崖」")
    destination: str = Field(description="通往的地点正名")


class RawLocation(_Raw):
    name: str
    aliases: list[str] = []
    region: str = ""
    description: str = ""
    exits: list[RawExit] = []


class RawCharacter(_Raw):
    name: str
    aliases: list[str] = []
    faction: str = ""
    status: Annotated[CharacterStatus, BeforeValidator(_lenient(CharacterStatus, CharacterStatus.ALIVE))] = (
        CharacterStatus.ALIVE
    )
    tier: _Tier = Tier.NONE
    disposition: Annotated[Disposition, BeforeValidator(_lenient(Disposition, Disposition.NEUTRAL))] = (
        Disposition.NEUTRAL
    )
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
    tier: Annotated[Tier, BeforeValidator(_lenient(Tier, Tier.THIRD))] = Tier.THIRD
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
2. name 用原文中最正式的称呼；aliases 只列本段文本里出现过的其他称呼（绰号、尊称、旧名）。
3. 地点 exits：只在文本写明两地相通或有人从一地行至另一地时记录；label 写方位或路径（如「北上」「出城门」「下崖」），destination 写目的地正名。
4. 人物：location 写此人在本段所处之地；faction 写门派或阵营；status 只能是 健在 / 已故；
   tier 依本段描写判断武功境界，只能是 不入流 / 三流 / 二流 / 一流 / 绝顶，不会武功即 不入流；
   disposition 依其为人判断，只能是 仁厚 / 中庸 / 狠辣；skills 只列本段写明此人施展过或身负的武学。
5. 武学 prerequisites 是修习它的硬性条件，只记文本写明的：须先通晓的武学（skills）、须持有的秘籍图谱（items）、
   须在何地修习（location）、修习者须有的境界（min_tier）、与之相冲的武学（conflicts）；
   transmission：文本写明可凭典籍自行参悟的填 自悟，否则一律 师传。
6. 物品：owner 是物主，location 是它此刻静置之处；随身携带则只填 owner。
7. 人物关系 kind 只能是 亲族 / 师徒 / 同门 / 结义 / 主仆 / 情侣 / 仇敌。
8. 这一段若没有某类实体，对应数组留空。只输出 JSON，不要任何解释。

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


class LLMKnowledgeExtractor(KnowledgeExtractor):
    def __init__(self, llm: LLMClient, *, cache_dir: Path | None = None, attempts: int = 2) -> None:
        self._llm = llm
        self._cache_dir = cache_dir
        self._attempts = attempts

    def _cache_path(self, chunk: Chunk) -> Path | None:
        if self._cache_dir is None:
            return None
        digest = hashlib.sha256(f"{PROMPT_VERSION}\n{chunk.text}".encode()).hexdigest()[:32]
        return self._cache_dir / f"{digest}.json"

    async def extract(self, chunk: Chunk) -> ChunkExtraction:
        cached = self._cache_path(chunk)
        if cached is not None and cached.exists():
            return ChunkExtraction.model_validate_json(cached.read_text(encoding="utf-8"))
        user = f'<chunk source="{_escape(chunk.source)}" index="{chunk.index}">\n{_escape(chunk.text)}\n</chunk>'
        last_error: Exception | None = None
        for _ in range(self._attempts):
            raw = await self._llm.complete(EXTRACTION_SYSTEM, user, EXTRACTION_SCHEMA)
            try:
                result = ChunkExtraction.model_validate(_json_object(raw))
            except (ValueError, ValidationError) as exc:
                last_error = exc
                continue
            if cached is not None:
                cached.parent.mkdir(parents=True, exist_ok=True)
                cached.write_text(result.model_dump_json(), encoding="utf-8")
            return result
        raise ExtractionError(f"{chunk.source}#{chunk.index} 抽取结果无法解析：{last_error}")


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

        async def one(chunk: Chunk) -> ChunkExtraction | None:
            async with gate:
                try:
                    return await self._extractor.extract(chunk)
                except (ExtractionError, LLMError) as exc:
                    logger.warning("抽取失败 %s#%d：%s", chunk.source, chunk.index, exc)
                    failures.append(f"{chunk.source}#{chunk.index}：{exc}")
                    return None

        # gather 保序：组装器的"首次登场即开篇状态"依赖原著的先后
        results = await asyncio.gather(*(one(c) for c in chunks))
        extractions = [r for r in results if r is not None]
        if not extractions:
            raise ExtractionError(f"全部 {len(chunks)} 个文本块抽取失败，管道中止")
        blueprint, report = self._assembler.assemble(extractions)
        report.failed_chunks.extend(failures)
        return SeedingResult(blueprint, report, compile_blueprint(blueprint))
