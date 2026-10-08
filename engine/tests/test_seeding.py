"""
[INPUT]: 依赖 app.infrastructure.knowledge_extractor（语料 / 切块 / 抽取契约 v6 / 抽取器 / 管道）、blueprint_assembler、cypher，依赖 tests/conftest 的 ScriptedLLM，
         依赖 tests/world 的 WORLD，可选依赖真实 Neo4j
[OUTPUT]: World Seeding 全链路单测：编码回退、回目切块、正名互见的实体消歧、三名分立（本名只在知道本名的记录里投票，称号与别名各归其位）、
          时间线隔离（后文事件否决开篇武学 / 物主 / 生死）、首次登场即开篇且未知不等于最弱、两道门（获取取并集与最严、修炼门槛取最严）、
          旧缓存 prerequisites 自动升级、被引用却无处安放的物品成为孤儿、唯一包含匹配落地、悬空引用丢弃、宁严勿宽的封存、
          道路双向、物品唯一归属、报告分节、抽取器的重采样与磁盘缓存、局部失败不拖垮全书、Cypher 参数化与脚本转义；
          设置 TLBB_TEST_NEO4J_URI 时把 seed.cypher 脚本逐句交给真实 Neo4j 执行
[POS]: tests 的"禁止凭空捏造"证明：世界只能由原著抽取物组装而来，组装器对一切落不了地的东西说不，对一切被时间线污染的开篇状态说不
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import json
from pathlib import Path
from typing import Any

import pytest

from app.domain.models import CharacterStatus, Tier, Transmission
from app.errors import ExtractionError, LLMError
from app.infrastructure.blueprint_assembler import AssemblyReport, BlueprintAssembler
from app.infrastructure.cypher import CONSTRAINTS, compile_blueprint, cypher_literal, render_script
from app.infrastructure.knowledge_extractor import (
    PROMPT_VERSION,
    T0_ANCHOR,
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
    """旧版缓存没有称号字段：退化为全体投票，其余称呼一并作别名。"""
    bp, _ = BlueprintAssembler().assemble([
        extraction(characters=[{"name": "青袍客", "aliases": ["段延庆"]}]),
        extraction(characters=[{"name": "段延庆", "aliases": ["恶贯满盈"]}]),
        extraction(characters=[{"name": "段延庆", "aliases": ["青袍客"]}]),
    ])
    assert [(c.name, set(c.aliases), c.titles) for c in bp.characters] == [("段延庆", {"青袍客", "恶贯满盈"}, ())]


def test_true_name_is_voted_only_among_records_that_know_it() -> None:
    """v5 的教训：段延庆在前九回多以「恶贯满盈」出场，按票数他就叫「恶贯满盈」。称号再响，也不能篡位成主键。"""
    bp, _ = BlueprintAssembler().assemble([
        extraction(locations=[{"name": "万劫谷"}],
                   characters=[{"name": "恶贯满盈", "name_is_title": True, "location": "万劫谷", "tier": "绝顶"},
                               {"name": "南海鳄神", "name_is_title": True}],
                   relations=[{"source": "恶贯满盈", "target": "南海鳄神", "kind": "结义"}]),
        extraction(characters=[{"name": "恶贯满盈", "name_is_title": True, "titles": ["天下第一恶人"]},
                               {"name": "南海鳄神", "name_is_title": True, "aliases": ["岳老三"]}]),
        extraction(characters=[{"name": "恶贯满盈", "name_is_title": True}]),
        extraction(characters=[{"name": "段延庆", "titles": ["恶贯满盈", "爹爹"], "aliases": ["延庆太子"]}]),
    ])
    people = {c.id: c for c in bp.characters}
    assert sorted(people) == ["chr:南海鳄神", "chr:段延庆"]
    duan = people["chr:段延庆"]
    assert duan.true_name == "段延庆" and duan.titles == ("恶贯满盈", "天下第一恶人") and duan.aliases == ("延庆太子",)
    assert duan.location_id == "loc:万劫谷" and duan.tier is Tier.PEERLESS  # 只知称号的记录照样贡献开篇状态
    eshen = people["chr:南海鳄神"]  # 整组都只知称号：才退回全体投票
    assert eshen.titles == () and eshen.aliases == ("岳老三",)
    assert [(r.source_id, r.target_id) for r in bp.relations] == [("chr:段延庆", "chr:南海鳄神")]  # 按称号点名也能落地


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
    ], "events": [{"subject": "钟灵", "kind": "得到物品", "object": "闪电貂", "note": "这闪电貂一生之中不知已吃了几千条毒蛇"}]},
        ensure_ascii=False)
    result = await LLMKnowledgeExtractor(ScriptedLLM(copied), cache_dir=tmp_path, backoff=0).extract(Chunk("书", 0, source))
    assert [i.description for i in result.items] == ["", "钟灵所穿的一双绣花鞋"]
    assert result.events[0].note == ""  # 事件的转述同样不许照抄
    assert "几千条毒蛇" not in next(tmp_path.rglob("*.json")).read_text(encoding="utf-8")  # 缓存同样干净


def test_references_land_by_unique_containment_but_never_guess() -> None:
    bp, report = BlueprintAssembler().assemble([
        extraction(locations=[{"name": "剑湖宫"}, {"name": "大理城"}, {"name": "大理皇宫"}],
                   characters=[{"name": "左子穆", "location": "剑湖宫外"}, {"name": "段誉", "location": "大理"}]),
    ])
    where = {c.name: c.location_id for c in bp.characters}
    assert where == {"左子穆": "loc:剑湖宫", "段誉": None}  # 「大理」同时包含于两处：多义不猜
    assert any("大理" in line for line in report.dropped)


def test_unlandable_requirements_seal_the_art_and_cycles_are_broken() -> None:
    bp, report = BlueprintAssembler().assemble([
        extraction(
            locations=[{"name": "无量玉洞"}],
            items=[{"name": "北冥神功卷轴", "location": "无量玉洞"}],
            martial_arts=[
                {"name": "北冥神功", "acquisition": {"items": ["北冥神功卷轴"], "location": "无量玉洞", "transmission": "自悟"},
                 "practice": {"conflicts": ["吸星大法"]}},
                {"name": "六脉神剑", "acquisition": {"items": ["六脉神剑剑谱"]}, "practice": {"skills": ["一阳指"]}},
                {"name": "甲功", "practice": {"skills": ["乙功"]}},
                {"name": "乙功", "practice": {"skills": ["甲功"]}},
                {"name": "丙功", "acquisition": {"transmission": "自悟"}},
            ],
        )
    ])
    arts = {a.name: a for a in bp.martial_arts}
    beiming = arts["北冥神功"]
    assert not beiming.acquisition.sealed and beiming.acquisition.transmission is Transmission.SELF
    assert beiming.acquisition.items == ("itm:北冥神功卷轴",) and beiming.acquisition.location_id == "loc:无量玉洞"
    assert beiming.practice.conflicts == ()  # 相冲之功不在本体即无从相冲
    assert arts["六脉神剑"].acquisition.sealed  # 根本不存在的典籍与根基：宁可失传，不可滥传
    assert arts["甲功"].acquisition.sealed and arts["乙功"].acquisition.sealed and arts["甲功"].practice.skills == ()
    assert arts["丙功"].acquisition.transmission is Transmission.TEACHER and not arts["丙功"].acquisition.sealed
    assert any("六脉神剑" in s for s in report.sealed) and any("成环" in s for s in report.sealed)
    assert any("丙功" in line and "改为须师传" in line for line in report.dropped)


def test_referenced_but_unplaced_items_become_orphans() -> None:
    """v5 的教训：「一阳指穴道谱诀」写明是入门之物却没写在哪——丢掉它，一阳指就只能封存。留作孤儿，等自愈代理安放。"""
    bp, report = BlueprintAssembler().assemble([
        extraction(
            items=[{"name": "一阳指穴道谱诀", "kind": "秘籍"}, {"name": "钓鱼杆儿"}],
            martial_arts=[{"name": "一阳指", "acquisition": {"items": ["一阳指穴道谱诀"]}},
                          {"name": "六脉神剑", "acquisition": {"items": ["六脉神剑剑谱"]}}],
        )
    ])
    arts = {a.name: a for a in bp.martial_arts}
    assert not arts["一阳指"].acquisition.sealed and arts["一阳指"].acquisition.items == ("itm:一阳指穴道谱诀",)
    assert arts["六脉神剑"].acquisition.sealed  # 引用的名字根本不是任何抽取到的物品：照旧封存
    assert [(i.name, i.lost, i.canon_holder) for i in bp.items] == [("一阳指穴道谱诀", True, None)]  # 未被引用的钓鱼杆儿照旧丢弃
    assert report.orphans == ["「一阳指穴道谱诀」下落不明，为「一阳指」所需——待自愈"]
    assert any("钓鱼杆儿" in line for line in report.dropped) and not any("谱诀" in line for line in report.dropped)


def test_legacy_prerequisites_upgrade_into_the_two_gates() -> None:
    """v4 / v5 缓存只有一个 prerequisites：读入即拆成获取要求与修炼要求，旧缓存仍能零费用组装。"""
    legacy = json.dumps({
        "locations": [{"name": "无量玉洞"}],
        "items": [{"name": "北冥神功卷轴", "location": "无量玉洞"}],
        "martial_arts": [
            {"name": "北冥神功", "prerequisites": {"items": ["北冥神功卷轴"], "location": "无量玉洞", "transmission": "自悟",
                                                 "min_tier": "胡说", "conflicts": []}},
            {"name": "凌波微步", "prerequisites": {"skills": ["北冥神功"], "min_tier": "二流", "transmission": None}},
        ],
    }, ensure_ascii=False)
    raw = ChunkExtraction.model_validate_json(legacy)
    beiming = raw.martial_arts[0]
    assert beiming.acquisition.items == ["北冥神功卷轴"] and beiming.acquisition.location == "无量玉洞"
    assert beiming.acquisition.transmission is Transmission.SELF and beiming.practice.min_tier is Tier.NONE  # 枚举照样宽容
    bp, report = BlueprintAssembler().assemble([raw])
    arts = {a.name: a for a in bp.martial_arts}
    assert arts["凌波微步"].practice.skills == ("art:北冥神功",) and arts["凌波微步"].practice.min_tier is Tier.SECOND
    assert arts["凌波微步"].acquisition.transmission is Transmission.TEACHER
    assert arts["北冥神功"].acquisition.items == ("itm:北冥神功卷轴",) and not report.sealed


def test_later_events_veto_states_polluted_by_the_timeline() -> None:
    """
    时间线坍缩的教训：全书的 skills 取并集，段誉开篇即身负北冥神功、凌波微步。事件是证据：后文习得的武学剔出开篇武学，
    后文才得到的物品不认他作开篇物主（取下一个候选），后文身故者开篇健在。
    """
    bp, report = BlueprintAssembler().assemble([
        extraction(locations=[{"name": "无量山"}, {"name": "无量玉洞"}],
                   characters=[{"name": "段誉", "location": "无量山", "tier": "不入流"},
                               {"name": "左子穆", "location": "无量山", "skills": ["无量剑法"]}],
                   martial_arts=[{"name": "无量剑法"}]),
        extraction(characters=[{"name": "段誉", "skills": ["北冥神功"]}, {"name": "司空玄", "status": "已故"}],
                   martial_arts=[{"name": "北冥神功", "acquisition": {"items": ["北冥神功卷轴"], "transmission": "自悟"}}],
                   items=[{"name": "北冥神功卷轴", "owner": "段誉", "location": "无量玉洞"},
                          {"name": "无量剑", "owner": "段誉"}],
                   events=[{"subject": "段誉", "kind": "习得武学", "object": "北冥神功", "note": "石洞中照卷轴自习"},
                           {"subject": "段誉", "kind": "得到物品", "object": "北冥神功卷轴"},
                           {"subject": "段誉", "kind": "得到物品", "object": "无量剑"},
                           {"subject": "司空玄", "kind": "身故", "note": "跳崖"},
                           {"subject": "段誉", "kind": "拜师", "object": "北冥神功"},
                           {"subject": "钟灵", "kind": "身故"},
                           {"subject": "段誉", "kind": "习得武学", "object": "六脉神剑"}]),
        extraction(characters=[{"name": "段誉", "skills": ["北冥神功", "凌波微步"]}],
                   martial_arts=[{"name": "凌波微步", "practice": {"skills": ["北冥神功"]}}],
                   items=[{"name": "无量剑", "owner": "左子穆"}],
                   events=[{"subject": "段誉", "kind": "习得武学", "object": "凌波微步"}]),
    ])
    people = {c.name: c for c in bp.characters}
    assert people["段誉"].skills == () and people["段誉"].location_id == "loc:无量山"
    assert people["左子穆"].skills == ("art:无量剑法",)  # 没有证据否决的状态原样保留
    assert people["司空玄"].status is CharacterStatus.ALIVE
    things = {i.name: i for i in bp.items}
    assert things["北冥神功卷轴"].owner_id is None and things["北冥神功卷轴"].location_id == "loc:无量玉洞"
    assert things["无量剑"].owner_id == "chr:左子穆"
    assert len(report.timeline) == 5  # 两门武学、两件物品、一次身故
    assert "段誉 开篇时尚未习得「北冥神功」（后文习得：石洞中照卷轴自习）" in report.timeline
    assert "司空玄 开篇时健在（后文身故：跳崖）" in report.timeline
    dropped = "\n".join(report.dropped)  # 证明不了任何事的事件：种类不认得、人物或宾语落不了地
    assert "种类无法识别" in dropped and "钟灵身故" in dropped and "段誉习得武学六脉神剑" in dropped


def test_a_shared_title_never_bridges_two_people() -> None:
    """称号可以多人共用：只知称号的记录在两位已知本名者之间多义不猜，绝不成为把他们捏成一人的桥（对抗式审查的复现）。"""
    bp, report = BlueprintAssembler().assemble([
        extraction(characters=[{"name": "慕容博", "titles": ["姑苏慕容"]}]),
        extraction(characters=[{"name": "慕容复", "titles": ["姑苏慕容"]}]),
        extraction(characters=[{"name": "姑苏慕容", "name_is_title": True}]),
    ])
    assert sorted(c.name for c in bp.characters) == ["慕容博", "慕容复"]
    assert any("姑苏慕容" in line and "多义不猜" in line for line in report.dropped)
    bp, _ = BlueprintAssembler().assemble([
        extraction(characters=[{"name": "大侠", "name_is_title": True}]),
        extraction(characters=[{"name": "乔峰", "titles": ["北乔峰", "大侠"]}]),
        extraction(characters=[{"name": "段誉", "titles": ["大侠"]}]),
    ])
    assert sorted(c.name for c in bp.characters) == ["乔峰", "段誉"]  # 无本名的称号组只能被认领一次


def test_regaining_an_item_does_not_veto_its_t0_owner() -> None:
    """开篇时就是他的、后来失而复得：主张早于"得到"事件，照认；主张与事件同块或更晚，才是被时间线污染的状态。"""
    bp, report = BlueprintAssembler().assemble([
        extraction(characters=[{"name": "段誉"}], items=[{"name": "折扇", "owner": "段誉"}]),
        extraction(characters=[{"name": "南海鳄神"}], events=[{"subject": "南海鳄神", "kind": "得到物品", "object": "折扇"}]),
        extraction(characters=[{"name": "段誉"}], items=[{"name": "折扇", "owner": "段誉"}],
                   events=[{"subject": "段誉", "kind": "得到物品", "object": "折扇", "note": "从鳄神手里讨回"}]),
    ])
    assert [(i.name, i.owner_id) for i in bp.items] == [("折扇", "chr:段誉")] and report.timeline == []


def test_timeline_evidence_lands_only_on_full_names() -> None:
    """否决会抹掉原著状态：「段正淳之子」被当描述丢掉，它的事件也不能借包含匹配去否决段正淳的一阳指。"""
    bp, report = BlueprintAssembler().assemble([
        extraction(characters=[{"name": "段正淳", "skills": ["一阳指"]}], martial_arts=[{"name": "一阳指"}]),
        extraction(characters=[{"name": "段正淳之子"}], martial_arts=[{"name": "一阳指"}],
                   events=[{"subject": "段正淳之子", "kind": "习得武学", "object": "一阳指"},
                           {"subject": "段正淳", "kind": "习得武学", "object": "一阳指法"}]),
    ])
    assert next(c for c in bp.characters if c.name == "段正淳").skills == ("art:一阳指",)
    assert report.timeline == [] and "人物不在本体之中" in "\n".join(report.dropped)


def test_report_renders_every_section_in_order() -> None:
    report = AssemblyReport(dropped=["甲"], sealed=["乙"], orphans=["丙"], timeline=["丁"], healed=["戊"], failed_chunks=["己"])
    assert report.render().split("\n") == ["[丢弃] 甲", "[封存] 乙", "[孤儿] 丙", "[时间线] 丁", "[自愈] 戊", "[抽取失败] 己"]
    assert AssemblyReport().render() == "（无异常）"


def test_unknown_is_not_weakest() -> None:
    """开篇一句没写武功的旁白，不能在"首次登场即开篇"里盖掉后文写明的境界；认不出的枚举值同样记为未知。"""
    bp, _ = BlueprintAssembler().assemble([
        extraction(characters=[{"name": "段正淳", "tier": None, "disposition": "天下第一好人"}],
                   martial_arts=[{"name": "一阳指", "practice": {"min_tier": "二流"}}]),
        extraction(characters=[{"name": "段正淳", "tier": "一流", "disposition": "仁厚"},
                               {"name": "朱丹臣"}, {"name": "秦红棉", "faction": "修罗刀门下"}],
                   martial_arts=[{"name": "一阳指", "tier": "一流", "practice": {"min_tier": "三流"}}]),
    ])
    duan = next(c for c in bp.characters if c.name == "段正淳")
    assert duan.tier is Tier.FIRST and duan.disposition.value == "仁厚"
    assert next(c for c in bp.characters if c.name == "朱丹臣").tier is Tier.NONE  # 无门无派、武功无从考证：才退回不入流
    assert next(c for c in bp.characters if c.name == "秦红棉").tier is Tier.THIRD  # 身在门派：至少三流
    art = bp.martial_arts[0]
    assert art.tier is Tier.FIRST and art.practice.min_tier is Tier.SECOND  # 门槛取最严


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
    assert "只抽取这段文本里明确出现" in system and T0_ANCHOR in system  # 时间锚点强制注入
    assert schema is not None and "events" in schema["properties"] and "</chunk>" not in user[:-8]
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

    def rows(fragment: str) -> list[dict[str, Any]]:
        return [r for s in data if fragment in s.query for r in s.params["rows"]]

    people = {r["id"]: r["props"] for r in rows("MERGE (n:Character")}
    assert people["chr:段延庆"]["name"] == "段延庆" and people["chr:段延庆"]["titles"] == ["恶贯满盈"]  # 节点名恒为本名
    arts = {r["id"]: r["props"] for r in rows("MERGE (n:MartialArt")}
    acquisition = json.loads(str(arts["art:北冥神功"]["acquisition"]))
    assert acquisition["items"] == ["itm:北冥神功卷轴"] and acquisition["transmission"] == "自悟"
    assert json.loads(str(arts["art:凌波微步"]["practice"]))["skills"] == ["art:北冥神功"]
    requires = {(r["a"], r["b"], r["props"]["as"]) for r in rows("[r:REQUIRES]")}
    assert ("art:凌波微步", "art:北冥神功", "skill") in requires and ("art:北冥神功", "loc:无量玉洞", "place") in requires
    held = [r for r in rows("(a:Item {id: row.a})") if r["a"] == "itm:玉佩"]
    assert len(held) == 2 and all(r["props"] == {"provenance": "原著"} for r in held)  # 原著明写的安放：所在与物主两条边


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
