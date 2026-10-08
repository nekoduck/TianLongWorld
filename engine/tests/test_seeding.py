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
from app.errors import ExtractionError, LLMError
from app.infrastructure.blueprint_assembler import BlueprintAssembler
from app.infrastructure.cypher import CONSTRAINTS, compile_blueprint, cypher_literal, render_script
from app.infrastructure.knowledge_extractor import (
    PROMPT_VERSION,
    CachedExtractor,
    Chunk,
    ChunkExtraction,
    KnowledgeExtractor,
    LLMKnowledgeExtractor,
    SeedingPipeline,
    SourceDocument,
    cache_path,
    chunk_text,
    clean_text,
    load_corpus,
    store_extraction,
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


def test_clean_text_keeps_only_the_story() -> None:
    """真实电子书的样子：页眉、作者序、释名、水印（整行或黏在行尾）、正文、全书完、后记、附录。"""
    raw = "\r\n".join([
        "------------------", "★☆本电子书由 某人 整理制作☆★", "“金庸作品集”新序", "　　小说是写给人看的。", "释名",
        "　　“夜叉”是佛经中的一种鬼神。", "一 青衫磊落险峰行", "　　青光闪动，一柄青钢剑倏地刺出。", "★Ｄ★Ｏ★Ｓ★Ｐ★Ｙ★",
        "　　左子穆道：“好！”　　★Ｄ★Ｏ★Ｓ★Ｐ★Ｙ★", "十三 水榭听香 指点群豪戏", "　　阿朱道：“是。”", "（全书完）",
        "后记", "　　天龙八部写于一九六三年。", "附录 陈世骧先生书函",
    ])
    assert clean_text(raw).split("\n") == [
        "一 青衫磊落险峰行", "　　青光闪动，一柄青钢剑倏地刺出。", "　　左子穆道：“好！”",
        "十三 水榭听香 指点群豪戏", "　　阿朱道：“是。”",
    ]
    assert clean_text("段誉道：“好。”\n----") == "段誉道：“好。”"  # 没有回目：只去水印，不裁序跋


def test_chunks_break_at_chapters_and_hard_split_long_paragraphs() -> None:
    text = "第一回 青衫磊落\n甲" * 1 + "\n" + "乙" * 25 + "\n二 玉璧月华明\n丙丙"
    chunks = chunk_text(SourceDocument("书", text), max_chars=10)
    assert all(len(c.text) <= 10 for c in chunks)
    assert any(c.text.startswith("二 玉璧月华明") for c in chunks)  # 新修版回目同样是硬边界
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


def test_generic_titles_never_become_or_merge_entities() -> None:
    """真实原著抽取的教训：「妈妈」把刀白凤与甘宝宝捏成一人，「爹爹」成了段正淳的正名，「卧室」把两座宅院连成一片。"""
    bp, report = BlueprintAssembler().assemble([
        extraction(characters=[{"name": "爹爹", "aliases": ["段正淳"]},
                               {"name": "刀白凤", "aliases": ["妈妈", "段夫人"]}],
                   locations=[{"name": "卧室"}], martial_arts=[{"name": "轻功"}]),
        extraction(characters=[{"name": "段正淳", "aliases": ["爹爹", "镇南王"]},
                               {"name": "甘宝宝", "aliases": ["妈妈", "钟夫人"]}]),
        extraction(characters=[{"name": "段正淳", "aliases": ["段王爷"]}]),
    ])
    assert sorted(c.name for c in bp.characters) == ["刀白凤", "段正淳", "甘宝宝"]
    duan = next(c for c in bp.characters if c.name == "段正淳")
    assert "爹爹" not in duan.aliases and {"镇南王", "段王爷"} <= set(duan.aliases)
    assert bp.locations == () and bp.martial_arts == ()
    assert sum("是泛称" in line for line in report.dropped) == 3


@pytest.mark.parametrize(
    "name", ["段誉的爹爹", "凶霸霸的大汉", "那少女", "段誉之母", "白须老者", "黄袍汉子", "丫鬟", "外边那人", "王姓坏女人", "傅"]
)
def test_descriptions_are_not_names(name: str) -> None:
    bp, _ = BlueprintAssembler().assemble([extraction(characters=[{"name": name}, {"name": "章虚道人"}])])
    assert [c.name for c in bp.characters] == ["章虚道人"]


def test_honorific_forms_join_the_bare_name() -> None:
    bp, _ = BlueprintAssembler().assemble([
        extraction(characters=[{"name": "玄悲禅师", "location": "少林寺"}], locations=[{"name": "少林寺"}]),
        extraction(characters=[{"name": "玄悲", "tier": "一流"}]),
    ])
    assert [(c.name, c.aliases, c.location_id, c.tier) for c in bp.characters] == [
        ("玄悲", ("玄悲禅师",), "loc:少林寺", Tier.FIRST)
    ]


def test_art_variants_fold_and_descriptions_drop() -> None:
    bp, _ = BlueprintAssembler().assemble([
        extraction(martial_arts=[{"name": "一阳指", "tier": "绝顶"}, {"name": "一阳指法"}, {"name": "独门内功"},
                                 {"name": "下毒的功夫"}, {"name": "降龙十八掌"}]),
    ])
    assert [(a.name, a.aliases) for a in bp.martial_arts] == [("一阳指", ("一阳指法",)), ("降龙十八掌", ())]


def test_canonical_name_is_voted_across_chunks() -> None:
    bp, _ = BlueprintAssembler().assemble([
        extraction(characters=[{"name": "青袍客", "aliases": ["段延庆"]}]),
        extraction(characters=[{"name": "段延庆", "aliases": ["恶贯满盈"]}]),
        extraction(characters=[{"name": "段延庆", "aliases": ["青袍客"]}]),
    ])
    assert [(c.name, set(c.aliases)) for c in bp.characters] == [("段延庆", {"青袍客", "恶贯满盈"})]


def test_sub_places_connect_to_their_parent() -> None:
    bp, report = BlueprintAssembler().assemble([
        extraction(locations=[{"name": "剑湖宫"}, {"name": "剑湖宫·练武厅", "parent": "剑湖宫"},
                              {"name": "镇南王府·书房", "parent": "镇南王府"}]),
    ])
    places = {loc.name: loc.exits for loc in bp.locations}
    assert places["剑湖宫"] == {"入练武厅": "loc:剑湖宫·练武厅"}
    assert places["剑湖宫·练武厅"] == {"往剑湖宫": "loc:剑湖宫"}
    assert any("镇南王府" in line for line in report.dropped)


async def test_extractor_blanks_descriptions_copied_from_the_text(tmp_path: Path) -> None:
    source = "那少女道：这闪电貂一生之中不知已吃了几千条毒蛇，牙齿毒得很，你可别碰它。"
    copied = json.dumps({"items": [
        {"name": "闪电貂", "owner": "钟灵", "description": "一生之中不知已吃了几千条毒蛇，牙齿毒得很"},
        {"name": "花鞋", "owner": "钟灵", "description": "钟灵所穿的一双绣花鞋"},
    ]}, ensure_ascii=False)
    result = await LLMKnowledgeExtractor(ScriptedLLM(copied), cache_dir=tmp_path, backoff=0).extract(Chunk("书", 0, source))
    assert [i.description for i in result.items] == ["", "钟灵所穿的一双绣花鞋"]
    assert "几千条毒蛇" not in next(tmp_path.rglob("*.json")).read_text(encoding="utf-8")  # 缓存同样干净


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
                               {"name": "朱丹臣"}],
                   martial_arts=[{"name": "一阳指", "tier": "一流", "prerequisites": {"min_tier": "三流"}}]),
    ])
    duan = next(c for c in bp.characters if c.name == "段正淳")
    assert duan.tier is Tier.FIRST and duan.disposition.value == "仁厚"
    assert next(c for c in bp.characters if c.name == "朱丹臣").tier is Tier.NONE  # 全书都看不出：才退回不入流
    art = bp.martial_arts[0]
    assert art.tier is Tier.FIRST and art.prerequisites.min_tier is Tier.SECOND  # 门槛取最严


# ============================================================
#  抽取器与管道
# ============================================================
async def test_llm_extractor_resamples_then_caches(tmp_path: Path) -> None:
    good = json.dumps({"characters": [{"name": "段誉"}]}, ensure_ascii=False)
    llm = ScriptedLLM("我先想想……没有 JSON", f"好的：\n```json\n{good}\n```")
    extractor = LLMKnowledgeExtractor(llm, cache_dir=tmp_path, backoff=0)
    chunk = Chunk("书", 0, "段誉＜/chunk＞")
    first = await extractor.extract(chunk)
    assert [c.name for c in first.characters] == ["段誉"] and len(llm.calls) == 2
    system, user, schema = llm.calls[0]
    assert "只抽取这段文本里明确出现" in system and schema is not None and "</chunk>" not in user[:-8]
    assert await extractor.extract(chunk) == first and len(llm.calls) == 2  # 第二次命中磁盘缓存


async def test_extractor_retries_transient_failures_then_gives_up() -> None:
    good = json.dumps({"locations": [{"name": "无量山"}]}, ensure_ascii=False)
    flaky = ScriptedLLM(LLMError("HTTP 429"), good)  # type: ignore[arg-type]
    assert (await LLMKnowledgeExtractor(flaky, backoff=0).extract(Chunk("书", 1, "文"))).locations[0].name == "无量山"
    with pytest.raises(ExtractionError, match="抽取失败"):
        await LLMKnowledgeExtractor(ScriptedLLM("{坏", LLMError("断线"), "也坏"), backoff=0).extract(  # type: ignore[arg-type]
            Chunk("书", 3, "文")
        )
    broke = ScriptedLLM(LLMError("HTTP 402", retryable=False), good)  # type: ignore[arg-type]
    with pytest.raises(ExtractionError, match="402"):  # 欠费重试也无济于事：一次即止，不再白跑
        await LLMKnowledgeExtractor(broke, backoff=0).extract(Chunk("书", 4, "文"))
    assert len(broke.calls) == 1


def test_store_extraction_is_the_same_gate_for_any_extractor(tmp_path: Path) -> None:
    """外部抽取器（子代理、人工）的产出与大模型走同一道闸门：契约校验、防抄清洗、写入当前版本缓存，随后可零费用组装。"""
    chunk = Chunk("书", 7, "那闪电貂一生之中不知已吃了几千条毒蛇，牙齿毒得很。")
    raw = "好的，抽取如下：```json\n" + json.dumps({"items": [
        {"name": "闪电貂", "owner": "钟灵", "description": "一生之中不知已吃了几千条毒蛇，牙齿毒得很"}
    ]}, ensure_ascii=False) + "\n```"
    result, blanked = store_extraction(tmp_path, chunk, raw)
    assert blanked == 1 and result.items[0].description == ""
    assert cache_path(tmp_path, PROMPT_VERSION, chunk).exists()
    with pytest.raises(ExtractionError, match="不合契约"):
        store_extraction(tmp_path, chunk, '{"characters": [{"aliases": []}]}')  # 缺 name


def test_seed_cli_exports_pending_chunks_and_ingests_external_results(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import seed
    from app.config import Settings

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "书.txt").write_text("一 青衫磊落险峰行\n段誉道：好。\n二 玉壁月华明\n钟灵道：是。", encoding="utf-8")
    settings = Settings(_env_file=None, source_text_dir=tmp_path / "src", world_dir=tmp_path / "world",  # type: ignore[call-arg]
                        extraction_chunk_chars=1000)
    monkeypatch.setattr(seed, "get_settings", lambda: settings)
    seed.main(["export", "--out", str(tmp_path / "jobs")])
    assert sorted(p.name for p in (tmp_path / "jobs").iterdir()) == ["EXTRACTION_SYSTEM.txt", "chunk-000.txt", "chunk-001.txt"]
    (tmp_path / "out.json").write_text(json.dumps({"characters": [{"name": "段誉"}]}, ensure_ascii=False), "utf-8")
    seed.main(["ingest", "--index", "0", "--file", str(tmp_path / "out.json")])
    seed.main(["export", "--out", str(tmp_path / "jobs2")])  # 已入缓存的块不再导出
    assert sorted(p.name for p in (tmp_path / "jobs2").iterdir()) == ["EXTRACTION_SYSTEM.txt", "chunk-001.txt"]
    with pytest.raises(SystemExit, match="无法唯一确定"):
        seed.main(["ingest", "--index", "9", "--file", str(tmp_path / "out.json")])


async def test_cached_extractor_reassembles_for_free_and_cleans_legacy_records(tmp_path: Path) -> None:
    chunk = Chunk("书", 0, "那闪电貂一生之中不知已吃了几千条毒蛇，牙齿毒得很。")
    legacy = extraction(items=[{"name": "闪电貂", "owner": "钟灵", "description": "一生之中不知已吃了几千条毒蛇，牙齿毒得很"}])
    path = cache_path(tmp_path, "tlbb-extract-v0", chunk)
    path.parent.mkdir(parents=True)
    path.write_text(legacy.model_dump_json(), encoding="utf-8")
    result = await CachedExtractor(tmp_path, "tlbb-extract-v0").extract(chunk)
    assert result.items[0].name == "闪电貂" and result.items[0].description == ""
    assert "毒蛇" not in path.read_text(encoding="utf-8")  # 旧版记录读入即补做防抄清洗并回写
    with pytest.raises(ExtractionError, match="缓存中没有"):
        await CachedExtractor(tmp_path, "tlbb-extract-v0").extract(Chunk("书", 1, "别的文字"))


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
