"""
[INPUT]: 依赖 app.config 的 Settings，依赖 infrastructure/knowledge_extractor 的 load_corpus / LLMKnowledgeExtractor / CachedExtractor / SeedingPipeline，
         依赖 infrastructure/cypher 的 compile_blueprint / render_script，依赖 infrastructure/persistence/neo4j_graph 的 Neo4jWorldGraph，
         依赖 infrastructure/llm/factory 的 build_llm（抽取职责），依赖 domain/models 的 WorldBlueprint
[OUTPUT]: 对外提供 命令行入口 main()：`python -m app.seed extract [--max-chunks N] [--apply] [--reset] [--allow-partial]`、
          `python -m app.seed assemble [--prompt-version V] [--max-chunks N]`、`python -m app.seed apply [--reset]` 与 `python -m app.seed script`
[POS]: World Seeding 的操作面：extract 读 data/source_text 的原著，经大模型抽取、确定性组装，写出 data/world/ 下的
       blueprint.json（中间表示）、seed.cypher（可交给 cypher-shell 审阅或导入）与 report.txt（丢弃 / 封存明细）——有失败块时只写报告、
       不覆盖已有蓝图（缓存保住已抽的块，排障后重跑即续抽）；
       assemble 只读缓存零费用重新组装（组装器改了规则、或要复现入库的蓝图时用）；
       apply 把 blueprint.json 写进 Neo4j。抽取与写图分离：重新播种无需重新读书，蓝图可以人工审阅后再落图
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import argparse
import asyncio
import logging
import sys

from app.config import LLMRole, Settings, get_settings
from app.domain.models import WorldBlueprint
from app.infrastructure.cypher import compile_blueprint, render_script
from app.infrastructure.knowledge_extractor import (
    PROMPT_VERSION,
    CachedExtractor,
    KnowledgeExtractor,
    LLMKnowledgeExtractor,
    SeedingPipeline,
    load_corpus,
)
from app.infrastructure.llm.factory import build_llm
from app.infrastructure.persistence.neo4j_graph import Neo4jWorldGraph


async def extract(settings: Settings, *, max_chunks: int | None, allow_partial: bool = False) -> WorldBlueprint:
    llm = build_llm(settings, LLMRole.EXTRACTION)
    if llm is None:
        raise SystemExit("原著解析需要真实大模型：请在 engine/.env 配置 LLM_PROVIDER / LLM_API_KEY / LLM_EXTRACTION_MODEL")
    model, thinking = settings.llm_profile(LLMRole.EXTRACTION)
    print(f"抽取模型：{settings.llm_provider} / {model}（思考档位 {thinking or '模型默认'}，提示词 {PROMPT_VERSION}）")
    extractor = LLMKnowledgeExtractor(llm, cache_dir=settings.world_dir / "cache")
    return await _seed(settings, extractor, max_chunks=max_chunks, allow_partial=allow_partial)


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
    settings.world_dir.mkdir(parents=True, exist_ok=True)
    (settings.world_dir / "report.txt").write_text(result.report.render(), encoding="utf-8")
    if result.report.failed_chunks and not allow_partial:
        raise SystemExit(
            f"{len(result.report.failed_chunks)} 个文本块抽取失败（明细见 report.txt），蓝图未写出、旧蓝图保持原样；"
            "排除故障后重跑 extract 即从缓存续抽，或加 --allow-partial 写出残缺蓝图"
        )
    settings.blueprint_path.write_text(result.blueprint.model_dump_json(indent=2), encoding="utf-8")
    (settings.world_dir / "seed.cypher").write_text(result.script, encoding="utf-8")
    bp = result.blueprint
    print(
        f"蓝图已写出：地点 {len(bp.locations)}、人物 {len(bp.characters)}、武学 {len(bp.martial_arts)}、"
        f"物品 {len(bp.items)}、关系 {len(bp.relations)}；丢弃 {len(result.report.dropped)}、"
        f"封存 {len(result.report.sealed)}、失败块 {len(result.report.failed_chunks)}（明细见 report.txt）"
    )
    return bp


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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.seed", description="《天龙八部》原著播种")
    sub = parser.add_subparsers(dest="command", required=True)
    ex = sub.add_parser("extract", help="读原著 → 蓝图 + Cypher 脚本")
    ex.add_argument("--max-chunks", type=int, default=None, help="只抽取前 N 个文本块（试跑、或只取开篇作为世界的时间切片）")
    ex.add_argument("--apply", action="store_true", help="抽取完成后立即写入 Neo4j")
    ex.add_argument("--reset", action="store_true", help="写图前清空旧的正典与全部平行世界")
    ex.add_argument("--allow-partial", action="store_true", help="有文本块抽取失败时仍写出蓝图（默认拒绝，以免残缺蓝图覆盖完整蓝图）")
    ap = sub.add_parser("apply", help="把 data/world/blueprint.json 写入 Neo4j")
    ap.add_argument("--reset", action="store_true", help="写图前清空旧的正典与全部平行世界")
    asm = sub.add_parser("assemble", help="只读缓存零费用重新组装蓝图（不调用大模型）")
    asm.add_argument("--prompt-version", default=PROMPT_VERSION, help=f"读取哪一版提示词的抽取缓存（默认当前 {PROMPT_VERSION}）")
    asm.add_argument("--max-chunks", type=int, default=None, help="只组装前 N 个文本块")
    sub.add_parser("script", help="由 blueprint.json 重新生成 seed.cypher")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    settings = get_settings()

    async def run() -> None:
        if args.command == "extract":
            blueprint = await extract(settings, max_chunks=args.max_chunks, allow_partial=args.allow_partial)
            if args.apply:
                await apply(settings, blueprint, reset=args.reset)
            return
        if args.command == "assemble":
            await assemble(settings, version=args.prompt_version, max_chunks=args.max_chunks)
            return
        if not settings.blueprint_path.exists():
            raise SystemExit(f"找不到 {settings.blueprint_path}：请先运行 extract")
        blueprint = WorldBlueprint.model_validate_json(settings.blueprint_path.read_text(encoding="utf-8"))
        if args.command == "script":
            (settings.world_dir / "seed.cypher").write_text(render_script(compile_blueprint(blueprint)), "utf-8")
            print(f"已写出 {settings.world_dir / 'seed.cypher'}")
        else:
            await apply(settings, blueprint, reset=args.reset)

    asyncio.run(run())


if __name__ == "__main__":
    main(sys.argv[1:])
