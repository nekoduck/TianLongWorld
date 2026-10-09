"""
[INPUT]: 依赖 app.config 的 Settings，依赖 infrastructure/knowledge_extractor 的 load_corpus / LLMKnowledgeExtractor / CachedExtractor / SeedingPipeline，
         依赖 infrastructure/graph_linter 的 GraphHealer / LLMPlacementOracle / lint / heal_brief / ingest_placements / HEALER_SYSTEM，
         依赖 infrastructure/canon_audit 的 Library / audit_export / ingest_audit / canonize_audit / unbacked_audit / one_line 与 lore_gate 的 lore_export / ingest_lore / canonize_lore，
         依赖 infrastructure/geo_gate 的 geo_export / ingest_geography / canonize_geography / load_geography，
         依赖 infrastructure/cypher 的 compile_blueprint / render_script，依赖 infrastructure/persistence/neo4j_graph 的 Neo4jWorldGraph，
         依赖 infrastructure/llm/factory 的 build_llm 与 budget 的 CallBudget（抽取职责，自愈同用，只在 --use-llm 时装配），依赖 domain/models 的 WorldBlueprint
[OUTPUT]: 对外提供 命令行入口 main()：`python -m app.seed extract --use-llm [--max-chunks N] [--apply] [--reset] [--allow-partial]`、
          `python -m app.seed assemble [--prompt-version V] [--max-chunks N]`、`python -m app.seed export --out DIR [--max-chunks N] [--all]`、
          `python -m app.seed ingest --index I --file F [--source S]`、
          `python -m app.seed heal [--apply] [--reset] [--retry-null] [--export DIR | --ingest FILE --by NAME | --use-llm]`、
          `python -m app.seed audit [--export DIR [--batch N] [--all] | --ingest FILE --by NAME] [--evidence-version V]`、
          `python -m app.seed lore [--export DIR [--from LOCATION] [--hops N] [--batch N] | --ingest FILE --by NAME] [--evidence-version V]`、
          `python -m app.seed geo [--export DIR [--from LOCATION] [--batch-pairs N] [--all] | --ingest FILE --by NAME] [--evidence-version V]`、
          `python -m app.seed apply [--reset]` 与 `python -m app.seed script`
[POS]: World Seeding 的操作面：extract 读 data/source_text 的原著，经大模型抽取、确定性组装，写出 data/world/ 下的
       blueprint.json（中间表示）、seed.cypher（可交给 cypher-shell 审阅或导入）与 report.txt（丢弃 / 封存 / 孤儿 / 时间线 / 自愈 / 审计 / 掌故 / 地理明细）——
       有失败块时只写报告、不覆盖已有蓝图（缓存保住已抽的块，排障后重跑即续抽）；
       assemble 只读缓存零费用重新组装（组装器改了规则、或要复现入库的蓝图时用）；
       extract 与 assemble 组装之后依次自动套用 healing.json 的安放、audit.json 的 T=0 审计、lore.json 的人设、见闻与人群、geography.json 的道路与可见性注记（零费用、确定性），
       新的推断与撰写只由 heal / audit / lore / geo 经 --export / --ingest 交给子代理——audit、lore 与 geo 根本没有大模型这条路；
       heal 为蓝图里下落不明的孤儿物品推断安放：配了真实大模型就问它，离线时只套缓存，export / ingest 让子代理或人工作答（自愈重建蓝图后照缓存补回审计与掌故）；
       audit 出 T=0 审计的分批题面、收作答过闸后写回蓝图（与播种同一个新鲜度判据，只豁免这一批刚重答的人物）；
       lore 以一地为中心（默认剑湖宫·练武厅走两跳）出人设、见闻与人群的题面、收作答过闸后写回（各命令的回显都点出人群数）；
       geo 从剑湖宫·练武厅起按广度优先把出口按无向的一对分批出道路题面（缺省每批 40 对，一对两向同批，--all 连缓存里两向已答的也出），另出一批全部地点的可见性题面，
       收作答过闸后入 geography.json 并写回；三者不带参数即按缓存重新套用，只换 report.txt 里自己那几节（每条折成一行）；
       套用顺序恒为 自愈 → 审计 → 掌故 → 地理（audit / lore 重建蓝图时同样依次补回后面几段）；
       蓝图带着审计痕迹而审计缓存用不上（缺失、损坏、口径或指纹不符）时，audit 与 heal 拒绝并指路 assemble——免得把审过的描述当原描述；
       有审计缓存时套用顺带读证据库，撞上后文的 T=0 描述在 [审计] 里标 ⚠；
       写回蓝图而不是直接改图——蓝图是正典，Neo4j 只是它的投影（--apply 时经同一个 seeder MERGE 进去）；
       export / ingest 让大模型之外的抽取器接手：export 导出抽取铁律与待抽的块，ingest 把外部抽取器的产出经同一道闸门写入缓存；
       apply 把 blueprint.json 写进 Neo4j。抽取与写图分离：重新播种无需重新读书，蓝图可以人工审阅后再落图。
       花钱的路要显式走：本项目的抽取与自愈由 Claude 子代理经 export / ingest 完成（2026-10 实测跑空过两次 Gemini 预付额度），
       extract 不带 --use-llm 即拒绝并指路，heal 不带 --use-llm 只套缓存；带了也有 LLM_CALL_LIMIT 保险丝兜底
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import argparse
import asyncio
import logging
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from app.config import LLMRole, Settings, get_settings
from app.domain.models import WorldBlueprint
from app.errors import ExtractionError
from app.infrastructure.canon_audit import (
    EVIDENCE_VERSION,
    Library,
    audit_export,
    canonize_audit,
    ingest_audit,
    load_audit,
    one_line,
    unbacked_audit,
)
from app.infrastructure.cypher import compile_blueprint, render_script
from app.infrastructure.geo_gate import (
    DEFAULT_BATCH_PAIRS,
    canonize_geography,
    geo_export,
    ingest_geography,
    load_geography,
)
from app.infrastructure.geo_gate import DEFAULT_FROM as GEO_FROM
from app.infrastructure.graph_linter import (
    HEALER_SYSTEM,
    GraphHealer,
    LLMPlacementOracle,
    PlacementOracle,
    heal_brief,
    ingest_placements,
    lint,
)
from app.infrastructure.knowledge_extractor import (
    EXTRACTION_SYSTEM,
    PROMPT_VERSION,
    CachedExtractor,
    Chunk,
    KnowledgeExtractor,
    LLMKnowledgeExtractor,
    SeedingPipeline,
    cache_path,
    chunk_message,
    chunk_text,
    load_corpus,
    naming_reference,
    store_extraction,
)
from app.infrastructure.llm.budget import CallBudget
from app.infrastructure.llm.factory import build_llm
from app.infrastructure.lore_gate import DEFAULT_FROM, DEFAULT_HOPS, canonize_lore, ingest_lore, load_lore, lore_export
from app.infrastructure.persistence.neo4j_graph import Neo4jWorldGraph


def _healing_cache(settings: Settings) -> Path:
    return settings.world_dir / "healing.json"


def _audit_cache(settings: Settings) -> Path:
    return settings.world_dir / "audit.json"


def _lore_cache(settings: Settings) -> Path:
    return settings.world_dir / "lore.json"


def _geography_cache(settings: Settings) -> Path:
    return settings.world_dir / "geography.json"


def _library(settings: Settings, version: str = EVIDENCE_VERSION) -> Library:
    return Library.load(settings.source_text_dir, settings.world_dir / "cache", version, settings.extraction_chunk_chars)


def _canonize(settings: Settings, blueprint: WorldBlueprint) -> tuple[WorldBlueprint, dict[str, list[str]]]:
    """
    自愈之后依次套用审计、掌故与地理缓存（零费用、确定性、从不抛错）：掌故依赖审计定下的 era 与物性，所以在后；地理只认地点与出口，排在最后。
    有审计缓存时另读证据库，把撞上后文的 T=0 描述标 ⚠。返回（蓝图, 报告分节 {审计, 掌故, 地理}）。
    """
    cache = _audit_cache(settings)
    audited, audit = canonize_audit(blueprint, cache, lib=_library(settings) if cache.exists() else None)
    lored, lore = canonize_lore(audited, _lore_cache(settings))
    noted, geo = canonize_geography(lored, _geography_cache(settings))
    return noted, {"审计": audit, "掌故": lore, "地理": geo}


def _refuse_unbacked(settings: Settings, blueprint: WorldBlueprint) -> None:
    """已审的蓝图却没有可用的审计缓存：再审计或再套用都会把审过的描述当原描述、旧结论撤不掉——拒绝并指路 assemble。"""
    if reason := unbacked_audit(blueprint, _audit_cache(settings)):
        raise SystemExit(reason)


def _write_blueprint(settings: Settings, blueprint: WorldBlueprint) -> None:
    settings.blueprint_path.write_text(blueprint.model_dump_json(indent=2), encoding="utf-8")
    (settings.world_dir / "seed.cypher").write_text(render_script(compile_blueprint(blueprint)), encoding="utf-8")


def _load_blueprint(settings: Settings) -> WorldBlueprint:
    if not settings.blueprint_path.exists():
        raise SystemExit(f"找不到 {settings.blueprint_path}：请先运行 extract 或 assemble")
    return WorldBlueprint.model_validate_json(settings.blueprint_path.read_text(encoding="utf-8"))


# ============================================================
#  抽取与组装
# ============================================================
OFFLINE_EXTRACTION = """原著抽取由 Claude 子代理完成，不调用付费大模型：
  python -m app.seed export --out DIR                 # 导出抽取铁律、跨块命名参考与待抽的块
  （子代理照 EXTRACTION_SYSTEM.txt 逐块作答，每块一个 JSON 文件）
  python -m app.seed ingest --index I --file F        # 逐块经同一道闸门入缓存
  python -m app.seed assemble                         # 零费用组装蓝图
确需调用 engine/.env 配置的大模型（会产生费用），加 --use-llm"""


async def extract(
    settings: Settings, *, max_chunks: int | None, allow_partial: bool = False, use_llm: bool = False
) -> WorldBlueprint:
    if not use_llm:
        raise SystemExit(OFFLINE_EXTRACTION)
    llm = build_llm(settings, LLMRole.EXTRACTION, CallBudget(settings.llm_call_limit))
    if llm is None:
        raise SystemExit("原著解析需要真实大模型：请在 engine/.env 配置 LLM_PROVIDER / LLM_API_KEY / LLM_EXTRACTION_MODEL")
    model, thinking = settings.llm_profile(LLMRole.EXTRACTION)
    print(f"抽取模型：{settings.llm_provider} / {model}（思考档位 {thinking or '模型默认'}，提示词 {PROMPT_VERSION}）")
    naming = _naming(settings)
    print(f"跨块命名参考：{'取自现有 blueprint.json' if naming else '无（尚无蓝图）'}")
    extractor = LLMKnowledgeExtractor(llm, cache_dir=settings.world_dir / "cache", naming=naming)
    return await _seed(settings, extractor, max_chunks=max_chunks, allow_partial=allow_partial)


def _naming(settings: Settings) -> str:
    """上一版蓝图在就以它的写法作跨块命名参考；没有就不给（首次播种）。读不了也不给——重抽正是为了替换坏掉或过时的蓝图，不能被它拦住。"""
    if not settings.blueprint_path.exists():
        return ""
    try:
        return naming_reference(WorldBlueprint.model_validate_json(settings.blueprint_path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:  # ValueError 涵盖 pydantic 校验错误与解码错误
        print(f"现有蓝图读不了，本次不给跨块命名参考：{exc}")
        return ""


async def assemble(settings: Settings, *, version: str, max_chunks: int | None) -> WorldBlueprint:
    print(f"只读缓存重新组装：提示词 {version}，不调用大模型")
    return await _seed(settings, CachedExtractor(settings.world_dir / "cache", version), max_chunks=max_chunks)


async def _seed(
    settings: Settings, extractor: KnowledgeExtractor, *, max_chunks: int | None, allow_partial: bool = False
) -> WorldBlueprint:
    pipeline = SeedingPipeline(
        extractor, chunk_chars=settings.extraction_chunk_chars, concurrency=settings.extraction_concurrency
    )
    documents = load_corpus(settings.source_text_dir)
    print(f"读取 {len(documents)} 部原著，共 {len(pipeline.chunks(documents, max_chunks))} 个文本块，开始抽取……")
    result = await pipeline.run(documents, max_chunks=max_chunks)
    healing = await GraphHealer(None, _healing_cache(settings)).heal(result.blueprint)  # 只套缓存：零费用、确定性
    bp, sections = _canonize(settings, healing.blueprint)
    report = result.report
    report.healed.extend([*healing.healed, *healing.unresolved])
    settings.world_dir.mkdir(parents=True, exist_ok=True)
    (settings.world_dir / "report.txt").write_text(_with_sections(report.render(), sections), encoding="utf-8")
    if report.failed_chunks and not allow_partial:
        raise SystemExit(
            f"{len(report.failed_chunks)} 个文本块抽取失败（明细见 report.txt），蓝图未写出、旧蓝图保持原样；"
            "排除故障后重跑 extract 即从缓存续抽，或加 --allow-partial 写出残缺蓝图"
        )
    _write_blueprint(settings, bp)
    print(
        f"蓝图已写出：地点 {len(bp.locations)}、人物 {len(bp.characters)}、武学 {len(bp.martial_arts)}、"
        f"物品 {len(bp.items)}、关系 {len(bp.relations)}、人设 {len(bp.personas)}、见闻 {len(bp.facts)}、人群 {len(bp.swarms)}、"
        f"道路注记 {len(bp.passages)}、可见性注记 {len(bp.sights)}；"
        f"丢弃 {len(report.dropped)}、封存 {len(report.sealed)}、"
        f"孤儿 {len(report.orphans)}（自愈 {len(healing.placements)}）、时间线隔离 {len(report.timeline)}、"
        f"失败块 {len(report.failed_chunks)}（明细见 report.txt）"
    )
    if remaining := lint(bp):
        print(f"仍有 {len(remaining)} 件孤儿下落不明：python -m app.seed heal 推断安放（或 heal --export / --ingest 交给子代理、人工）")
    return bp


def _chunks(settings: Settings, max_chunks: int | None = None) -> list[Chunk]:
    chunks = [c for d in load_corpus(settings.source_text_dir) for c in chunk_text(d, settings.extraction_chunk_chars)]
    return chunks[:max_chunks] if max_chunks else chunks


def export(settings: Settings, out: Path, *, max_chunks: int | None, everything: bool) -> list[Chunk]:
    """导出抽取铁律（含 JSON Schema）与待抽的块（默认只导出当前提示词版本尚未缓存的块）。"""
    cache = settings.world_dir / "cache"
    pending = [c for c in _chunks(settings, max_chunks) if everything or not cache_path(cache, PROMPT_VERSION, c).exists()]
    out.mkdir(parents=True, exist_ok=True)
    (out / "EXTRACTION_SYSTEM.txt").write_text(EXTRACTION_SYSTEM, encoding="utf-8")
    if naming := _naming(settings):  # 大模型之外的抽取器拿到与生产抽取器同一份命名参考
        (out / "NAMING_REFERENCE.txt").write_text(naming, encoding="utf-8")
    for c in pending:
        (out / f"chunk-{c.index:03d}.txt").write_text(chunk_message(c), encoding="utf-8")
    print(f"已导出 {len(pending)} 个待抽块与抽取铁律（{PROMPT_VERSION}）到 {out}：{[c.index for c in pending]}")
    return pending


def ingest(settings: Settings, index: int, file: Path, *, source: str | None) -> None:
    chunks = [c for c in _chunks(settings) if c.index == index and source in (None, c.source)]
    if len(chunks) != 1:
        raise SystemExit(f"无法唯一确定第 {index} 块（{len(chunks)} 个候选）：多部原著时请用 --source 指明")
    try:
        result, blanked = store_extraction(settings.world_dir / "cache", chunks[0], file.read_text(encoding="utf-8"))
    except ExtractionError as exc:
        raise SystemExit(str(exc)) from exc
    sizes = {k: len(getattr(result, k)) for k in ("locations", "characters", "martial_arts", "items", "relations", "events")}
    print(f"第 {index} 块已入缓存（{PROMPT_VERSION}）：{sizes}；照抄原文而被清空的描述 {blanked} 条")


# ============================================================
#  图谱自愈 —— 作用于正典（蓝图），再经 seeder 投影进 Neo4j
# ============================================================
async def heal(
    settings: Settings, *, export_dir: Path | None = None, ingest_file: Path | None = None, by: str | None = None,
    retry_null: bool = False, use_llm: bool = False,
) -> WorldBlueprint:
    blueprint = _load_blueprint(settings)
    cache = _healing_cache(settings)
    if export_dir is None:  # 自愈之后要重新套用审计缓存
        _refuse_unbacked(settings, blueprint)
    if export_dir is not None:
        blueprint = (await GraphHealer(None, cache).heal(blueprint)).blueprint  # 缓存已能安放的不再出题
        orphans = lint(blueprint)
        export_dir.mkdir(parents=True, exist_ok=True)
        (export_dir / "HEALER_SYSTEM.txt").write_text(HEALER_SYSTEM, encoding="utf-8")
        (export_dir / "orphans.txt").write_text(heal_brief(blueprint, *orphans), encoding="utf-8")
        print(f"已导出自愈铁律与 {len(orphans)} 件孤儿的题面到 {export_dir}：{[o.item.name for o in orphans]}")
        return blueprint
    oracle: PlacementOracle | None = None
    if ingest_file is not None:
        try:
            accepted = ingest_placements(blueprint, ingest_file.read_text(encoding="utf-8"), cache, by or "")
        except ExtractionError as exc:
            raise SystemExit(str(exc)) from exc
        print(f"已入自愈缓存 {len(accepted)} 条（推断者 {by}）：{[p.item for p in accepted]}")
    elif use_llm and (llm := build_llm(settings, LLMRole.EXTRACTION, CallBudget(settings.llm_call_limit))) is not None:
        model, _ = settings.llm_profile(LLMRole.EXTRACTION)
        oracle = LLMPlacementOracle(llm, model_name=f"{settings.llm_provider}/{model}")
        print(f"自愈模型：{settings.llm_provider} / {model}（只问缓存里还没有答案的孤儿）")
    else:
        print("只套用自愈缓存（自愈由 Claude 子代理作答，不调用付费大模型）：尚无答案的孤儿用 heal --export DIR 交给子代理，"
              "再 heal --ingest FILE --by NAME 入缓存；确需调用 engine/.env 配置的大模型（会产生费用）加 --use-llm")
    result = await GraphHealer(oracle, cache, retry_null=retry_null).heal(blueprint)
    if isinstance(oracle, LLMPlacementOracle) and oracle.halted is not None:
        print(f"自愈模型不可用（{oracle.halted}）：尚无答案的孤儿这次没有问成，改用 heal --export / --ingest 交给子代理")
    healed, sections = _canonize(settings, result.blueprint)  # 安放改了所在：审计、掌故与地理按缓存重新核验套用（指纹不符即作废）
    _write_blueprint(settings, healed)
    _rewrite_sections(settings, {"自愈": [*result.healed, *result.unresolved], **sections})
    print(f"自愈完成：生效的安放 {len(result.placements)} 条，仍下落不明 {len(lint(healed))} 件；已写回蓝图与 seed.cypher")
    for line in [*result.healed, *result.unresolved]:
        print(f"  {line}")
    return healed


# ============================================================
#  报告分节 —— 组装之后由操作面填入的四节（自愈 / 审计 / 掌故 / 地理），依序排在「抽取失败」之前
# ============================================================
_SECTIONS = ("自愈", "审计", "掌故", "地理")


def _with_sections(text: str, sections: Mapping[str, Sequence[str]]) -> str:
    """把报告里给出的分节换成新内容，没给出的分节与其余各节原样保留。"""
    lines = [ln for ln in text.splitlines() if ln != "（无异常）"]
    managed = {t: [ln for ln in lines if ln.startswith(f"[{t}] ")] for t in _SECTIONS}
    managed |= {t: [f"[{t}] {one_line(line)}" for line in new] for t, new in sections.items()}  # 一条一行：换行拆不开分节
    rest = [ln for ln in lines if not any(ln.startswith(f"[{t}] ") for t in _SECTIONS)]
    tail = next((i for i, ln in enumerate(rest) if ln.startswith("[抽取失败] ")), len(rest))
    return "\n".join([*rest[:tail], *(ln for t in _SECTIONS for ln in managed[t]), *rest[tail:]]) or "（无异常）"


def _rewrite_sections(settings: Settings, sections: Mapping[str, Sequence[str]]) -> None:
    """heal / audit / lore / geo 不重新组装：只换 report.txt 里自己那几节。"""
    path = settings.world_dir / "report.txt"
    if path.exists():
        path.write_text(_with_sections(path.read_text(encoding="utf-8"), sections), encoding="utf-8")


# ============================================================
#  T=0 审计、掌故与地理 —— 只有 export / ingest：撰写归 Claude 子代理，这里没有大模型这条路
# ============================================================
def audit(
    settings: Settings, *, export_dir: Path | None = None, ingest_file: Path | None = None, by: str | None = None,
    batch: int = 40, everything: bool = False, version: str = EVIDENCE_VERSION,
) -> WorldBlueprint:
    blueprint = _load_blueprint(settings)
    cache = _audit_cache(settings)
    _refuse_unbacked(settings, blueprint)
    keep: set[str] = set()
    lib: Library | None = None
    try:
        book, notes = load_audit(cache, blueprint)
        if export_dir is not None:
            lib = _library(settings, version)
            files = audit_export(blueprint, lib, book, batch=batch, everything=everything)
            _write_files(export_dir, files)
            print(f"已导出 {len(files)} 份审计题面到 {export_dir}（缓存里已有结论 {len(book)} 条，--all 连它们一起出题）：{sorted(files)}")
            for note in notes:
                print(f"  {note}")
            if not lib.chunks:
                print("  本地没有原著：题面列不出原文块与后文事件，arrives_with 过不了闸门")
            return blueprint
        if ingest_file is not None:
            lib = _library(settings, version)
            fresh, notes = ingest_audit(blueprint, ingest_file.read_text(encoding="utf-8"), cache, by or "", lib)
            book, _ = load_audit(cache, blueprint)
            keep = {v.id for v in fresh.characters}
            print(f"已入审计缓存 {len(fresh)} 条（作答者 {by}）；缓存共 {len(book)} 条")
            for note in notes:
                print(f"  {note}")
    except ExtractionError as exc:
        raise SystemExit(str(exc)) from exc
    if ingest_file is not None:  # 与播种同一个新鲜度判据，只豁免这一批刚重答的人物（蓝图里还是上一版结论的样子）
        bp, audited = canonize_audit(blueprint, cache, keep=keep, lib=lib)
        bp, lored = canonize_lore(bp, _lore_cache(settings))
        bp, noted = canonize_geography(bp, _geography_cache(settings))
        sections = {"审计": audited, "掌故": lored, "地理": noted}
    else:
        bp, sections = _canonize(settings, blueprint)
    _write_blueprint(settings, bp)
    _rewrite_sections(settings, sections)
    print(f"审计已套用并写回蓝图与 seed.cypher；人设 {len(bp.personas)}、见闻 {len(bp.facts)}、人群 {len(bp.swarms)}（掌故依赖审计：改了 era 或物性即整体作废）")
    for line in [*sections["审计"], *sections["掌故"]]:
        print(f"  {line}")
    return bp


def lore(
    settings: Settings, *, export_dir: Path | None = None, ingest_file: Path | None = None, by: str | None = None,
    start: str = DEFAULT_FROM, hops: int = DEFAULT_HOPS, batch: int = 15, version: str = EVIDENCE_VERSION,
) -> WorldBlueprint:
    blueprint = _load_blueprint(settings)
    cache = _lore_cache(settings)
    try:
        if export_dir is not None:
            book, notes = load_lore(cache, blueprint)
            lib = _library(settings, version)
            try:
                files, area = lore_export(blueprint, lib, book, start=start, hops=hops, batch=batch)
            except ValueError as exc:
                raise SystemExit(str(exc)) from exc
            _write_files(export_dir, files)
            print(f"已导出 {len(files)} 份掌故题面到 {export_dir}：以「{start}」为中心沿 CONNECTS_TO 走 {hops} 跳，"
                  f"{len(area.places)} 地 {len(area.people)} 人：{[c.true_name for c in area.people]}")
            for note in notes:
                print(f"  {note}")
            if not lib.chunks:
                print("  本地没有原著：题面列不出原文块，出处过不了闸门")
            return blueprint
        if ingest_file is not None:
            fresh, notes = ingest_lore(blueprint, ingest_file.read_text(encoding="utf-8"), cache, by or "", _library(settings, version))
            print(f"已入掌故缓存：人设 {len(fresh.personas)}、见闻 {len(fresh.facts)}、人群 {len(fresh.swarms)}（作答者 {by}）")
            for note in notes:
                print(f"  {note}")
    except ExtractionError as exc:
        raise SystemExit(str(exc)) from exc
    bp, lored = canonize_lore(blueprint, cache)
    bp, noted = canonize_geography(bp, _geography_cache(settings))  # 地理排在掌故之后：同样按缓存补回
    _write_blueprint(settings, bp)
    _rewrite_sections(settings, {"掌故": lored, "地理": noted})
    print(f"掌故已套用并写回蓝图与 seed.cypher：人设 {len(bp.personas)}、见闻 {len(bp.facts)}、人群 {len(bp.swarms)}")
    for line in lored:
        print(f"  {line}")
    return bp


def geo(
    settings: Settings, *, export_dir: Path | None = None, ingest_file: Path | None = None, by: str | None = None,
    start: str = GEO_FROM, batch: int = DEFAULT_BATCH_PAIRS, everything: bool = False, version: str = EVIDENCE_VERSION,
) -> WorldBlueprint:
    """地理注记：export 出分批题面（道路按无向的一对、可见性全部地点），ingest 过闸后入 geography.json 并写回；不带参数即按缓存重新套用。"""
    blueprint = _load_blueprint(settings)
    cache = _geography_cache(settings)
    try:
        if export_dir is not None:
            book, notes = load_geography(cache, blueprint)
            lib = _library(settings, version)
            try:
                files, todo = geo_export(blueprint, lib, book, start=start, batch=batch, everything=everything)
            except ValueError as exc:
                raise SystemExit(str(exc)) from exc
            _write_files(export_dir, files)
            print(f"已导出 {len(files)} 份地理题面到 {export_dir}：从「{start}」起按广度优先，道路 {len(todo)} 对分 {len(files) - 1} 批"
                  f"（缓存里已有道路注记 {len(book.passages)} 条，--all 连两向已答的一起出题）、可见性 {len(blueprint.locations)} 地一批：{sorted(files)}")
            for note in notes:
                print(f"  {note}")
            if not lib.chunks:
                print("  本地没有原著：题面列不出原文块，出处过不了闸门")
            return blueprint
        if ingest_file is not None:
            fresh, notes = ingest_geography(blueprint, ingest_file.read_text(encoding="utf-8"), cache, by or "", _library(settings, version))
            print(f"已入地理缓存：道路注记 {len(fresh.passages)}、可见性注记 {len(fresh.sights)}（作答者 {by}）")
            for note in notes:
                print(f"  {note}")
    except ExtractionError as exc:
        raise SystemExit(str(exc)) from exc
    bp, noted = canonize_geography(blueprint, cache)
    _write_blueprint(settings, bp)
    _rewrite_sections(settings, {"地理": noted})
    print(f"地理已套用并写回蓝图与 seed.cypher：道路注记 {len(bp.passages)}、可见性注记 {len(bp.sights)}")
    for line in noted[:1]:  # 逐条明细在 report.txt 的 [地理] 分节
        print(f"  {line}")
    return bp


def _write_files(out: Path, files: Mapping[str, str]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (out / name).write_text(text, encoding="utf-8")


async def apply(settings: Settings, blueprint: WorldBlueprint, *, reset: bool) -> None:
    if settings.graph_backend != "neo4j":
        print("GRAPH_BACKEND=memory：服务启动时会自动加载 blueprint.json，无需写图。")
        return
    graph = await Neo4jWorldGraph.connect(
        settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password, database=settings.neo4j_database
    )
    try:
        await graph.seed(blueprint, reset=reset)
    finally:
        await graph.close()
    print(f"已写入 Neo4j：{len(compile_blueprint(blueprint))} 条语句{'（已清空旧纪元）' if reset else ''}")


# ============================================================
#  命令行
# ============================================================
def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.seed", description="《天龙八部》原著播种")
    sub = parser.add_subparsers(dest="command", required=True)
    ex = sub.add_parser("extract", help="读原著 → 蓝图 + Cypher 脚本")
    ex.add_argument("--max-chunks", type=int, default=None, help="只抽取前 N 个文本块（试跑、或只取开篇作为世界的时间切片）")
    ex.add_argument("--apply", action="store_true", help="抽取完成后立即写入 Neo4j")
    ex.add_argument("--reset", action="store_true", help="写图前清空旧的正典与全部平行世界")
    ex.add_argument("--allow-partial", action="store_true", help="有文本块抽取失败时仍写出蓝图（默认拒绝，以免残缺蓝图覆盖完整蓝图）")
    ex.add_argument("--use-llm", action="store_true", help="调用 engine/.env 配置的大模型抽取（会产生费用；本项目由 Claude 子代理经 export / ingest 抽取）")
    ap = sub.add_parser("apply", help="把 data/world/blueprint.json 写入 Neo4j")
    ap.add_argument("--reset", action="store_true", help="写图前清空旧的正典与全部平行世界")
    asm = sub.add_parser("assemble", help="只读缓存零费用重新组装蓝图（不调用大模型）")
    asm.add_argument("--prompt-version", default=PROMPT_VERSION, help=f"读取哪一版提示词的抽取缓存（默认当前 {PROMPT_VERSION}）")
    asm.add_argument("--max-chunks", type=int, default=None, help="只组装前 N 个文本块")
    exp = sub.add_parser("export", help="导出抽取铁律与待抽的块，交给大模型之外的抽取器")
    exp.add_argument("--out", type=Path, required=True, help="导出目录")
    exp.add_argument("--max-chunks", type=int, default=None, help="只看前 N 个文本块")
    exp.add_argument("--all", action="store_true", help="连已缓存的块也导出")
    ing = sub.add_parser("ingest", help="把外部抽取器对某一块的产出校验、清洗后写入当前版本缓存")
    ing.add_argument("--index", type=int, required=True, help="块序号")
    ing.add_argument("--file", type=Path, required=True, help="抽取结果 JSON 文件（容忍围栏与寒暄）")
    ing.add_argument("--source", default=None, help="多部原著时指明块所属的文件名")
    hl = sub.add_parser(
        "heal", help="图谱自愈：为被武学引用却下落不明的物品据原著常识推断安放，写回蓝图（默认只套缓存，新推断经 --export / --ingest 交给子代理）"
    )
    hl.add_argument("--apply", action="store_true", help="自愈后把蓝图写入 Neo4j（推断的 LOCATED_IN / BELONGS_TO 边带 provenance=推断）")
    hl.add_argument("--reset", action="store_true", help="写图前清空旧的正典与全部平行世界")
    who = hl.add_mutually_exclusive_group()
    who.add_argument("--export", type=Path, default=None, metavar="DIR", help="导出自愈铁律与全部孤儿的题面，交给子代理或人工作答")
    who.add_argument("--ingest", type=Path, default=None, metavar="FILE", help="外部自愈者的作答（JSON 对象或数组，容忍围栏）校验后入自愈缓存，再按缓存自愈")
    who.add_argument("--use-llm", action="store_true", help="调用 engine/.env 配置的大模型推断（会产生费用；本项目由 Claude 子代理经 --export / --ingest 作答）")
    hl.add_argument("--by", default=None, metavar="NAME", help="--ingest 的推断者署名，如 claude-subagent 或人名")
    hl.add_argument("--retry-null", action="store_true", help="重新询问缓存里已被判为无从推断的孤儿（默认沿用判词，不重复付费）")
    au = sub.add_parser("audit", help="T=0 审计：关系结于何时、描述拆出后文剧情、物性与后来才到场者（经 --export / --ingest 交给子代理；不带参数即按缓存重新套用）")
    aud = au.add_mutually_exclusive_group()
    aud.add_argument("--export", type=Path, default=None, metavar="DIR", help="导出分批题面（缓存里已有结论的条目不再出题）")
    aud.add_argument("--ingest", type=Path, default=None, metavar="FILE", help="作答（JSON 数组，容忍围栏）过闸后入 audit.json，并写回蓝图")
    au.add_argument("--by", default=None, metavar="NAME", help="--ingest 的作答者署名，如 claude-subagent")
    au.add_argument("--batch", type=int, default=40, help="每份题面至多几条（关系、人物、物品各自分批）")
    au.add_argument("--all", action="store_true", help="连缓存里已有结论的条目也出题")
    au.add_argument("--evidence-version", default=EVIDENCE_VERSION, help=f"后文事件与切片取自哪一版抽取缓存（默认 {EVIDENCE_VERSION}）")
    lo = sub.add_parser("lore", help="人设、见闻与人群：以一地为中心出题、作答过闸后入 lore.json（经 --export / --ingest 交给子代理；不带参数即按缓存重新套用）")
    lor = lo.add_mutually_exclusive_group()
    lor.add_argument("--export", type=Path, default=None, metavar="DIR", help="导出以 --from 为中心的题面")
    lor.add_argument("--ingest", type=Path, default=None, metavar="FILE", help="作答（JSON 数组，容忍围栏）过闸后入 lore.json，并写回蓝图")
    lo.add_argument("--by", default=None, metavar="NAME", help="--ingest 的作答者署名，如 claude-subagent")
    lo.add_argument("--from", dest="start", default=DEFAULT_FROM, metavar="LOCATION", help=f"出发地点的正名（默认 {DEFAULT_FROM}）")
    lo.add_argument("--hops", type=int, default=DEFAULT_HOPS, help=f"沿 CONNECTS_TO 走几跳（默认 {DEFAULT_HOPS}）")
    lo.add_argument("--batch", type=int, default=15, help="每份题面至多几人")
    lo.add_argument("--evidence-version", default=EVIDENCE_VERSION, help=f"后文事件与切片取自哪一版抽取缓存（默认 {EVIDENCE_VERSION}）")
    gg = sub.add_parser("geo", help="地理注记：出路的方位、交通方式与耗时，地标与名胜（经 --export / --ingest 交给子代理；不带参数即按缓存重新套用）")
    geg = gg.add_mutually_exclusive_group()
    geg.add_argument("--export", type=Path, default=None, metavar="DIR", help="导出分批题面（道路按无向的一对，另出一批可见性）")
    geg.add_argument("--ingest", type=Path, default=None, metavar="FILE", help="作答（JSON 数组，容忍围栏）过闸后入 geography.json，并写回蓝图")
    gg.add_argument("--by", default=None, metavar="NAME", help="--ingest 的作答者署名，如 claude-subagent")
    gg.add_argument("--from", dest="start", default=GEO_FROM, metavar="LOCATION", help=f"广度优先的起点（默认 {GEO_FROM}）")
    gg.add_argument("--batch-pairs", type=int, default=DEFAULT_BATCH_PAIRS, help=f"每份道路题面至多几对（默认 {DEFAULT_BATCH_PAIRS}，一对两向同批）")
    gg.add_argument("--all", action="store_true", help="连缓存里两向都已答的一对也出题")
    gg.add_argument("--evidence-version", default=EVIDENCE_VERSION, help=f"原文块取自哪一版抽取缓存（默认 {EVIDENCE_VERSION}）")
    sub.add_parser("script", help="由 blueprint.json 重新生成 seed.cypher")
    args = parser.parse_args(argv)
    if args.command in ("heal", "audit", "lore", "geo") and args.ingest is not None and not args.by:
        parser.error(f"{args.command} --ingest 须以 --by NAME 署名作答者")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    logging.getLogger("neo4j").setLevel(logging.WARNING)  # 约束已存在之类的服务器通知不是进度
    settings = get_settings()

    if args.command == "export":
        export(settings, args.out, max_chunks=args.max_chunks, everything=args.all)
        return
    if args.command == "ingest":
        ingest(settings, args.index, args.file, source=args.source)
        return
    if args.command == "audit":
        audit(settings, export_dir=args.export, ingest_file=args.ingest, by=args.by, batch=args.batch,
              everything=args.all, version=args.evidence_version)
        return
    if args.command == "lore":
        lore(settings, export_dir=args.export, ingest_file=args.ingest, by=args.by, start=args.start, hops=args.hops,
             batch=args.batch, version=args.evidence_version)
        return
    if args.command == "geo":
        geo(settings, export_dir=args.export, ingest_file=args.ingest, by=args.by, start=args.start, batch=args.batch_pairs,
            everything=args.all, version=args.evidence_version)
        return

    async def run() -> None:
        if args.command == "extract":
            blueprint = await extract(
                settings, max_chunks=args.max_chunks, allow_partial=args.allow_partial, use_llm=args.use_llm
            )
            if args.apply:
                await apply(settings, blueprint, reset=args.reset)
            return
        if args.command == "assemble":
            await assemble(settings, version=args.prompt_version, max_chunks=args.max_chunks)
            return
        if args.command == "heal":
            healed = await heal(settings, export_dir=args.export, ingest_file=args.ingest, by=args.by,
                                retry_null=args.retry_null, use_llm=args.use_llm)
            if args.apply and args.export is None:
                await apply(settings, healed, reset=args.reset)
            return
        blueprint = _load_blueprint(settings)
        if args.command == "script":
            (settings.world_dir / "seed.cypher").write_text(render_script(compile_blueprint(blueprint)), "utf-8")
            print(f"已写出 {settings.world_dir / 'seed.cypher'}")
        else:
            await apply(settings, blueprint, reset=args.reset)

    asyncio.run(run())


if __name__ == "__main__":
    main(sys.argv[1:])
