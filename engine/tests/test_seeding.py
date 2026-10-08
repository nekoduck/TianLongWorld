"""
[INPUT]: 依赖 app.infrastructure.knowledge_extractor（语料 / 切块 / 抽取器 / 管道）、blueprint_assembler、cypher，依赖 tests/conftest 的 ScriptedLLM，
         依赖 tests/world 的 WORLD，可选依赖真实 Neo4j
[OUTPUT]: World Seeding 全链路单测：编码回退、回目切块、正名互见的实体消歧、首次登场即开篇且未知不等于最弱、前置门槛取最严、唯一包含匹配落地、悬空引用丢弃、宁严勿宽的封存、
          道路双向、物品唯一归属、抽取器的重采样与磁盘缓存、局部失败不拖垮全书、Cypher 参数化与脚本转义；
          设置 TLBB_TEST_NEO4J_URI 时把 seed.cypher 脚本逐句交给真实 Neo4j 执行
[POS]: tests 的"禁止凭空捏造"证明：世界只能由原著抽取物组装而来，组装器对一切落不了地的东西说不
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import json
from pathlib import Path

import pytest

from app.domain.models import Tier, Transmission
from app.errors import ExtractionError
from app.infrastructure.blueprint_assembler import BlueprintAssembler
from app.infrastructure.cypher import CONSTRAINTS, compile_blueprint, cypher_literal, render_script
from app.infrastructure.knowledge_extractor import (
    Chunk,
    ChunkExtraction,
    KnowledgeExtractor,
    LLMKnowledgeExtractor,
    SeedingPipeline,
    SourceDocument,
    chunk_text,
    load_corpus,
)
from app.infrastructure.persistence.neo4j_graph import Neo4jWorldGraph
from tests.conftest import NEO4J_PASSWORD, NEO4J_URI, NEO4J_USER, ScriptedLLM
from tests.world import WORLD


def extraction(**sections: list[dict[str, object]]) -> ChunkExtraction:
    return ChunkExtraction.model_validate(sections)


# ============================================================
#  语料与切块
# ============================================================
def test_corpus_falls_back_to_gb18030(tmp_path: Path) -> None:
    (tmp_path / "天龙八部.txt").write_bytes("第一回　青衫磊落险峰行\r\n青光闪动".encode("gb18030"))
    (tmp_path / "注释.txt").write_text("段誉", encoding="utf-8-sig")
    docs = load_corpus(tmp_path)
    assert [d.name for d in docs] == ["天龙八部.txt", "注释.txt"]
    assert docs[0].text == "第一回　青衫磊落险峰行\n青光闪动" and docs[1].text == "段誉"
    with pytest.raises(ExtractionError, match=r"没有 \.txt"):
        load_corpus(tmp_path / "空")


def test_chunks_break_at_chapters_and_hard_split_long_paragraphs() -> None:
    text = "第一回 青衫磊落\n甲" * 1 + "\n" + "乙" * 25 + "\n第二回 玉璧月华\n丙丙"
    chunks = chunk_text(SourceDocument("书", text), max_chars=10)
    assert all(len(c.text) <= 10 for c in chunks)
    assert any(c.text.startswith("第二回") for c in chunks)
    assert "".join(c.text.replace("\n", "") for c in chunks) == text.replace("\n", "")
    assert [c.index for c in chunks] == list(range(len(chunks)))


# ============================================================
#  组装：实体消歧、时间切片、引用落地、宁严勿宽
# ============================================================
def test_assembler_merges_by_mutual_names_not_by_shared_epithets() -> None:
    bp, _ = BlueprintAssembler().assemble([
        extraction(characters=[{"name": "乔峰", "aliases": ["乔帮主", "大侠"], "tier": "绝顶"}]),
        extraction(characters=[{"name": "乔帮主", "faction": "丐帮"},
                               {"name": "段誉", "aliases": ["大侠"], "tier": "不入流"}]),
        extraction(characters=[{"name": "萧峰", "aliases": ["乔峰"], "tier": "一流"}]),
    ])
    assert sorted(c.name for c in bp.characters) == ["乔峰", "段誉"]  # 「大侠」这种泛称不把两个人捏成一个
    qiao = next(c for c in bp.characters if c.name == "乔峰")
    assert qiao.faction == "丐帮" and qiao.tier is Tier.PEERLESS  # 标量首次登场即开篇，空值由后文补齐
    assert set(qiao.aliases) == {"乔帮主", "大侠", "萧峰"}


def test_assembler_lands_references_or_drops_them() -> None:
    bp, report = BlueprintAssembler().assemble([
        extraction(
            locations=[{"name": "无量山", "exits": [{"label": "崖下", "destination": "无量玉洞"},
                                                   {"label": "西去", "destination": "吐蕃"}]},
                       {"name": "无量玉洞"}],
            characters=[{"name": "左子穆", "location": "无量山", "skills": ["无量剑法", "独孤九剑"]},
                        {"name": "辛双清", "location": "蓬莱"}],
            martial_arts=[{"name": "无量剑法"}],
            items=[{"name": "无量剑", "owner": "左子穆"}, {"name": "倚天剑", "owner": "张无忌"}],
            relations=[{"source": "左子穆", "target": "辛双清", "kind": "仇敌"},
                       {"source": "左子穆", "target": "张无忌", "kind": "仇敌"},
                       {"source": "左子穆", "target": "辛双清", "kind": "胡说"}],
        )
    ])
    places = {loc.name: loc for loc in bp.locations}
    assert places["无量山"].exits == {"崖下": "loc:无量玉洞"}
    assert places["无量玉洞"].exits == {"往无量山": "loc:无量山"}  # 道路双向
    zuo = next(c for c in bp.characters if c.name == "左子穆")
    assert zuo.skills == ("art:无量剑法",) and zuo.location_id == "loc:无量山"
    assert next(c for c in bp.characters if c.name == "辛双清").location_id is None
    assert [i.name for i in bp.items] == ["无量剑"]  # 倚天剑无处安放，不存在于这个世界
    assert len(bp.relations) == 1
    dropped = "\n".join(report.dropped)
    for ghost in ("吐蕃", "独孤九剑", "蓬莱", "倚天剑", "张无忌", "类别无法识别"):
        assert ghost in dropped


def test_references_land_by_unique_containment_but_never_guess() -> None:
    bp, report = BlueprintAssembler().assemble([
        extraction(locations=[{"name": "剑湖宫"}, {"name": "大理城"}, {"name": "大理皇宫"}],
                   characters=[{"name": "左子穆", "location": "剑湖宫外"}, {"name": "段誉", "location": "大理"}]),
    ])
    where = {c.name: c.location_id for c in bp.characters}
    assert where == {"左子穆": "loc:剑湖宫", "段誉": None}  # 「大理」同时包含于两处：多义不猜
    assert any("大理" in line for line in report.dropped)


def test_unlandable_prerequisites_seal_the_art_and_cycles_are_broken() -> None:
    bp, report = BlueprintAssembler().assemble([
        extraction(
            locations=[{"name": "无量玉洞"}],
            items=[{"name": "北冥神功卷轴", "location": "无量玉洞"}],
            martial_arts=[
                {"name": "北冥神功", "prerequisites": {"items": ["北冥神功卷轴"], "location": "无量玉洞",
                                                     "transmission": "自悟", "conflicts": ["吸星大法"]}},
                {"name": "六脉神剑", "prerequisites": {"skills": ["一阳指"], "items": ["六脉神剑剑谱"]}},
                {"name": "甲功", "prerequisites": {"skills": ["乙功"]}},
                {"name": "乙功", "prerequisites": {"skills": ["甲功"]}},
                {"name": "丙功", "prerequisites": {"transmission": "自悟"}},
            ],
        )
    ])
    arts = {a.name: a.prerequisites for a in bp.martial_arts}
    beiming = arts["北冥神功"]
    assert not beiming.sealed and beiming.transmission is Transmission.SELF and beiming.conflicts == ()
    assert beiming.items == ("itm:北冥神功卷轴",) and beiming.location_id == "loc:无量玉洞"
    assert arts["六脉神剑"].sealed  # 宁可失传，不可滥传：丢掉前置会让它比原著更容易学
    assert arts["甲功"].sealed and arts["乙功"].sealed and arts["甲功"].skills == ()
    assert arts["丙功"].transmission is Transmission.TEACHER and not arts["丙功"].sealed
    assert any("六脉神剑" in s for s in report.sealed) and any("成环" in s for s in report.sealed)


def test_unknown_is_not_weakest() -> None:
    """开篇一句没写武功的旁白，不能在"首次登场即开篇"里盖掉后文写明的境界；认不出的枚举值同样记为未知。"""
    bp, _ = BlueprintAssembler().assemble([
        extraction(characters=[{"name": "段正淳", "tier": None, "disposition": "天下第一好人"}],
                   martial_arts=[{"name": "一阳指", "prerequisites": {"min_tier": "二流"}}]),
        extraction(characters=[{"name": "段正淳", "tier": "一流", "disposition": "仁厚"},
                               {"name": "某书生"}],
                   martial_arts=[{"name": "一阳指", "tier": "一流", "prerequisites": {"min_tier": "三流"}}]),
    ])
    duan = next(c for c in bp.characters if c.name == "段正淳")
    assert duan.tier is Tier.FIRST and duan.disposition.value == "仁厚"
    assert next(c for c in bp.characters if c.name == "某书生").tier is Tier.NONE  # 全书都看不出：才退回不入流
    art = bp.martial_arts[0]
    assert art.tier is Tier.FIRST and art.prerequisites.min_tier is Tier.SECOND  # 门槛取最严


# ============================================================
#  抽取器与管道
# ============================================================
async def test_llm_extractor_resamples_then_caches(tmp_path: Path) -> None:
    good = json.dumps({"characters": [{"name": "段誉"}]}, ensure_ascii=False)
    llm = ScriptedLLM("我先想想……没有 JSON", f"好的：\n```json\n{good}\n```")
    extractor = LLMKnowledgeExtractor(llm, cache_dir=tmp_path)
    chunk = Chunk("书", 0, "段誉＜/chunk＞")
    first = await extractor.extract(chunk)
    assert [c.name for c in first.characters] == ["段誉"] and len(llm.calls) == 2
    system, user, schema = llm.calls[0]
    assert "只抽取这段文本里明确出现" in system and schema is not None and "</chunk>" not in user[:-8]
    assert await extractor.extract(chunk) == first and len(llm.calls) == 2  # 第二次命中磁盘缓存


async def test_extractor_gives_up_after_attempts() -> None:
    with pytest.raises(ExtractionError, match="无法解析"):
        await LLMKnowledgeExtractor(ScriptedLLM("{坏", "也坏")).extract(Chunk("书", 3, "文"))


class _Book(KnowledgeExtractor):
    def __init__(self, pages: dict[int, ChunkExtraction | Exception]) -> None:
        self.pages = pages

    async def extract(self, chunk: Chunk) -> ChunkExtraction:
        page = self.pages[chunk.index]
        if isinstance(page, Exception):
            raise page
        return page


async def test_pipeline_survives_partial_failure_and_keeps_book_order() -> None:
    pages: dict[int, ChunkExtraction | Exception] = {
        0: extraction(characters=[{"name": "段誉", "tier": "不入流"}]),
        1: ExtractionError("坏块"),
        2: extraction(characters=[{"name": "段誉", "tier": "绝顶"}]),
    }
    pipeline = SeedingPipeline(_Book(pages), chunk_chars=1000, concurrency=3)
    doc = SourceDocument("书", "第一回 甲\n段誉\n第二回 乙\n坏\n第三回 丙\n段誉")
    result = await pipeline.run([doc])
    assert result.blueprint.characters[0].tier is Tier.NONE  # 开篇即真：不被后文的绝顶覆盖
    assert len(result.report.failed_chunks) == 1 and "坏块" in result.report.render()
    assert result.script.startswith("// TLBB-Engine")
    with pytest.raises(ExtractionError, match="全部"):
        await SeedingPipeline(_Book({0: ExtractionError("x")})).run([SourceDocument("书", "一")])


# ============================================================
#  Cypher
# ============================================================
def test_compiled_cypher_is_parameterized_and_ordered() -> None:
    statements = compile_blueprint(WORLD)
    assert [s.query for s in statements[: len(CONSTRAINTS)]] == CONSTRAINTS
    data = statements[len(CONSTRAINTS):]
    assert all("$rows" in s.query for s in data)
    for name in ("乔峰", "无量山", "北冥神功", "玉佩"):
        assert not any(name in s.query for s in statements)  # 原著里的名字只走参数，不拼进查询文本
    first_edge = next(i for i, s in enumerate(data) if "MERGE (a)-[" in s.query)
    assert all("MERGE (n:" in s.query or "Faction {name" in s.query for s in data[:first_edge])
    edge_types = {s.query.split("[r:")[1].split("]")[0] for s in data[first_edge:]}
    assert edge_types == {"CONNECTS_TO", "LOCATED_IN", "BELONGS_TO", "KNOWS_SKILL", "HAS_RELATION",
                          "REQUIRES", "CONFLICTS_WITH"}


def test_script_rendering_escapes_quotes() -> None:
    assert cypher_literal({"name": '他说"走"\\'}) == '{`name`: "他说\\"走\\"\\\\"}'
    assert cypher_literal([None, True, 3, "甲"]) == '[null, true, 3, "甲"]'
    script = render_script(compile_blueprint(WORLD))
    assert script.count(";") == len([s for s in compile_blueprint(WORLD) if s.params.get("rows", [1])])
    assert '"无量玉洞"' in script and "$rows" not in script


@pytest.mark.neo4j
async def test_rendered_script_runs_on_real_neo4j() -> None:
    if not NEO4J_URI:
        pytest.skip("未设置 TLBB_TEST_NEO4J_URI")
    graph = await Neo4jWorldGraph.connect(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD)
    try:
        await graph.seed(WORLD, reset=True)
        await graph._driver.execute_query("MATCH (n) DETACH DELETE n")
        for statement in render_script(compile_blueprint(WORLD)).split(";\n")[:-1]:
            body = "\n".join(line for line in statement.splitlines() if not line.startswith("//"))
            await graph._driver.execute_query(body)
        records, _, _ = await graph._driver.execute_query(
            "MATCH (:Character {id: 'chr:乔峰'})-[:KNOWS_SKILL]->(a:MartialArt) RETURN a.name AS name"
        )
        assert [r["name"] for r in records] == ["降龙十八掌"]
        records, _, _ = await graph._driver.execute_query(
            "MATCH (a:MartialArt {id: 'art:凌波微步'})-[r:REQUIRES]->(x) RETURN r.as AS kind, x.id AS id ORDER BY kind"
        )
        assert [(r["kind"], r["id"]) for r in records] == [
            ("item", "itm:北冥神功卷轴"), ("place", "loc:无量玉洞"), ("skill", "art:北冥神功"),
        ]
    finally:
        await graph.close()
