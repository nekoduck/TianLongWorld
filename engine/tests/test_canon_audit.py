"""
[INPUT]: 依赖 app.infrastructure.canon_audit（证据库 / 专名判据 / 作答契约 / 闸门 / 纯函数套用 / 缓存与新鲜度 / 审计痕迹 / 题面）、
         knowledge_extractor 的 store_extraction / ChunkExtraction / RawCanonEvent / PROMPT_VERSION，
         依赖 app.domain 的本体与掌故类型，依赖 app.seed 命令行（audit / heal 子命令与 assemble 的自动套用；证据库经 _library 换成自撰原著）
[OUTPUT]: T=0 审计单测：一批合格作答入缓存并套上蓝图（关系 era、人物拆出后文剧情与后来才到场、物品物性）；闸门逐类拒收整批（包含匹配、封闭枚举、多余字段、
          新添专名、险性无字眼、到场无出处 / 出处不实 / 摘句不实 / 出处与此人无涉 / 原文块没提到此人（摘句只为原著不用其名的物品作凭）、
          自由文本含换行 / 英文 / 数字 / 标记、本地无原著、蓝图里没有的关系、一批两答）且缓存一字不写；
          与原著共享 ≥16 字的字段清空（无原著则告警不清）；apply_audit 是纯函数、幂等、落不了地或套上后蓝图不自洽即抛错；
          缓存的版本与指纹（指纹不随审计本身而变、随骨架而变）、原描述变了的人物结论作废（套用、入缓存、出题同一判据：ingest 不再把它带回来、照样出题）、
          重答已审的人物仍以最初的原描述为准且命令行套上新答案；专名名录只收组装器认作实体的名字、地名「·」两侧的简称算蓝图专名；
          T=0 描述撞上后文（已答的与原样留着的）标 ⚠；报告一条一行；已审的蓝图没有可用缓存（作废 / 损坏 / 缺失）时 audit 与 heal 拒绝并指路 assemble；
          题面分批、已审的不再出题；命令行 export → ingest（须署名）→ assemble 自动套用，全程不装配大模型
[POS]: tests 的"审计不造假"证明：子代理只能提议，结论过闸门才进蓝图；同一份缓存零费用、确定性地复现同一份审计后的蓝图
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import json
from pathlib import Path
from typing import Any

import pytest

from app.domain.lore import Fact, FactUnlock
from app.domain.models import (
    Character,
    CharacterRelation,
    Era,
    Item,
    Location,
    MartialArt,
    RelationKind,
    WorldBlueprint,
)
from app.errors import ExtractionError
from app.infrastructure.canon_audit import (
    AUDIT_PROMPT_VERSION,
    EVIDENCE_VERSION,
    AuditBook,
    CharacterVerdict,
    ItemVerdict,
    Library,
    RelationVerdict,
    apply_audit,
    audit_export,
    audit_fingerprint,
    audit_lines,
    canon_names,
    canonize_audit,
    ingest_audit,
    load_audit,
    save_audit,
    stray_nouns,
    unbacked_audit,
)
from app.infrastructure.knowledge_extractor import (
    PROMPT_VERSION,
    ChunkExtraction,
    RawCanonEvent,
    chunk_text,
    load_corpus,
    store_extraction,
)

# ============================================================
#  夹具 —— 自撰的微型「原著」与它的蓝图（非原著文本）
# ============================================================
TEXT0 = "左子穆坐在练武厅上首，龚光杰比剑得胜，段誉在旁嗤笑一声。马五德陪着段誉上山观光。龚光杰大怒，一掌打得段誉跌倒。"
TEXT1 = "忽听得厅外脚步声响，容子矩满身是血撞进厅来，随即气绝。门外一枝箭射进一封书信，封皮上抹了剧毒。"

BP = WorldBlueprint(
    locations=(
        Location(id="loc:练武厅", name="练武厅", exits={"出厅": "loc:剑湖宫"}),
        Location(id="loc:剑湖宫", name="剑湖宫", exits={"入厅": "loc:练武厅", "下山": "loc:无量山"}),
        Location(id="loc:无量山", name="无量山", exits={"上山": "loc:剑湖宫"}),
    ),
    characters=(
        Character(id="chr:左子穆", true_name="左子穆", aliases=("左先生",), faction="无量剑东宗", location_id="loc:练武厅",
                  description="无量剑东宗掌门，剑法精准而好面子", skills=("art:白虹贯日",)),
        Character(id="chr:龚光杰", true_name="龚光杰", faction="无量剑东宗", location_id="loc:练武厅",
                  description="左子穆的弟子，比剑得胜后迁怒段誉"),
        Character(id="chr:段誉", true_name="段誉", faction="大理段氏", location_id="loc:练武厅", description="青衫书生，随马五德上山观光"),
        Character(id="chr:容子矩", true_name="容子矩", faction="无量剑东宗", location_id="loc:练武厅",
                  description="左子穆的师弟，后来带伤撞回厅中气绝"),
        Character(id="chr:马五德", true_name="马五德", location_id="loc:练武厅", description="普洱富商，好客"),
    ),
    martial_arts=(MartialArt(id="art:白虹贯日", name="白虹贯日", faction="无量剑东宗"),),
    items=(
        Item(id="itm:毒信笺", name="毒信笺", kind="书信", location_id="loc:练武厅", description="神农帮射来的书信，封皮抹了剧毒"),
        Item(id="itm:无量玉璧", name="无量玉璧", kind="奇石", location_id="loc:无量山", description="神农帮要查明的宝物"),
        Item(id="itm:通天草", name="通天草", kind="药草", location_id="loc:无量山", description="无量山所产灵草，可解貂毒"),
    ),
    relations=(
        CharacterRelation(source_id="chr:左子穆", target_id="chr:龚光杰", kind=RelationKind.MENTOR, note="得意门徒"),
        CharacterRelation(source_id="chr:龚光杰", target_id="chr:段誉", kind=RelationKind.ENEMY, note="掌掴段誉"),
        CharacterRelation(source_id="chr:左子穆", target_id="chr:容子矩", kind=RelationKind.FELLOW, note="师兄弟"),
    ),
)


def library(*, novel: bool = True) -> Library:
    events = {
        "ev:1#0": RawCanonEvent(subject="容子矩", kind="身故", note="带伤撞回厅中气绝"),
        "ev:1#1": RawCanonEvent(subject="段誉", kind="习得武学", object="凌波微步", note="后来学会步法"),
    }
    if not novel:
        return Library(lexicon=frozenset({"神农帮", "凌波微步", "木婉清"}))
    return Library({0: TEXT0, 1: TEXT1}, events, (TEXT0 + TEXT1).replace("\n", ""), frozenset({"神农帮", "凌波微步", "木婉清"}))


GOOD: list[dict[str, Any]] = [
    {"type": "relation", "source": "龚光杰", "target": "段誉", "kind": "仇敌", "era": "后文", "basis": "第二回才结怨"},
    {"type": "character", "name": "容子矩", "description": "左子穆的师弟", "foreshadow": "带伤撞回厅中气绝",
     "arrives_with": "portent:容子矩撞入", "evidence": {"ref": "ev:1#0"}},
    {"type": "item", "name": "毒信笺", "hazard": "剧毒", "arrives_with": "portent:神农帮传书",
     "evidence": {"ref": "chunk:1", "quote": "射进一封书信"}},
    {"type": "item", "name": "无量玉璧", "portable": False},
    {"type": "item", "name": "通天草", "use": {"effect": "疗伤", "potency": 1}},
]


def raw(*answers: dict[str, Any]) -> str:
    return "好的：```json\n" + json.dumps(list(answers), ensure_ascii=False) + "\n```"


def ingest(tmp_path: Path, *answers: dict[str, Any], lib: Library | None = None, bp: WorldBlueprint = BP) -> AuditBook:
    return ingest_audit(bp, raw(*answers), tmp_path / "audit.json", "claude-subagent", lib or library())[0]


def char(bp: WorldBlueprint, name: str) -> Character:
    return next(c for c in bp.characters if c.true_name == name)


def item(bp: WorldBlueprint, name: str) -> Item:
    return next(i for i in bp.items if i.name == name)


# ============================================================
#  合格的一批：入缓存、套上蓝图
# ============================================================
def test_a_good_batch_is_cached_and_lands_on_the_blueprint(tmp_path: Path) -> None:
    fresh = ingest(tmp_path, *GOOD)
    assert len(fresh) == 5 and {v.by for v in fresh.items} == {"claude-subagent"}
    cache = json.loads((tmp_path / "audit.json").read_text(encoding="utf-8"))
    assert cache["version"] == AUDIT_PROMPT_VERSION and cache["fingerprint"] == audit_fingerprint(BP)
    assert cache["characters"][0]["was"] == "左子穆的师弟，后来带伤撞回厅中气绝"  # 记下作答时的原描述

    audited, lines = canonize_audit(BP, tmp_path / "audit.json")
    enemy = next(r for r in audited.relations if r.kind is RelationKind.ENEMY)
    assert enemy.era is Era.LATER and all(r.era is Era.OPENING for r in audited.relations if r is not enemy)
    rong = char(audited, "容子矩")
    assert (rong.description, rong.foreshadow, rong.arrives_with) == ("左子穆的师弟", "带伤撞回厅中气绝", "portent:容子矩撞入")
    letter, jade, herb = item(audited, "毒信笺"), item(audited, "无量玉璧"), item(audited, "通天草")
    assert letter.hazard == "剧毒" and letter.arrives_with == "portent:神农帮传书" and letter.portable
    assert not jade.portable and herb.use is not None and herb.use.effect == "疗伤"
    assert "关系已审 1/3 条（开篇 0、将至 0、后文 1）" in lines
    assert any(ln.startswith("「容子矩」后来才到场：portent:容子矩撞入，出处 ev:1#0") for ln in lines)
    assert any(ln.startswith("「毒信笺」剧毒；后来才出现") for ln in lines)


def test_answers_tolerate_a_single_object_and_titles_resolve_exactly(tmp_path: Path) -> None:
    single = ingest_audit(BP, json.dumps({"type": "character", "name": "左先生", "description": "无量剑东宗掌门，剑法精准而好面子"},
                                         ensure_ascii=False), tmp_path / "audit.json", "人工", library())[0]
    assert single.characters[0].id == "chr:左子穆"  # 别名全等即落地


# ============================================================
#  闸门 —— 有一条不合格，整批拒收，缓存一字不写
# ============================================================
BAD: dict[str, tuple[dict[str, Any], str]] = {
    "包含匹配": ({"type": "relation", "source": "龚光", "target": "段誉", "kind": "仇敌", "era": "开篇"}, "没有名为「龚光」"),
    "关系不在蓝图里": ({"type": "relation", "source": "段誉", "target": "左子穆", "kind": "师徒", "era": "开篇"}, "没有「段誉—左子穆」"),
    "era 枚举之外": ({"type": "relation", "source": "龚光杰", "target": "段誉", "kind": "仇敌", "era": "未来"}, "不合契约"),
    "多余字段": ({"type": "item", "name": "通天草", "token_for": "钟灵"}, "不合契约"),
    "险性枚举之外": ({"type": "item", "name": "毒信笺", "hazard": "腐蚀"}, "不合契约"),
    "portent 格式": ({"type": "character", "name": "段誉", "description": "青衫书生", "arrives_with": "后来 才到"}, "不合契约"),
    "新添专名": ({"type": "character", "name": "段誉", "description": "青衫书生，与神农帮有旧"}, "原描述里没有的专名：神农帮"),
    "后文剧情里新添专名": ({"type": "character", "name": "段誉", "description": "青衫书生", "foreshadow": "后来学会凌波微步"},
                         "foreshadow 出现了原描述里没有的专名：凌波微步"),
    "险性无字眼": ({"type": "item", "name": "无量玉璧", "hazard": "剧毒"}, "原描述里却没有「毒」"),
    "到场无出处": ({"type": "character", "name": "马五德", "description": "普洱富商，好客", "arrives_with": "portent:马五德到"},
                  "须附出处"),
    "出处编号不存在": ({"type": "item", "name": "毒信笺", "arrives_with": "portent:传书", "evidence": {"ref": "ev:9#9"}}, "不是后文事件"),
    "出处与此人无涉": ({"type": "character", "name": "容子矩", "description": "左子穆的师弟", "arrives_with": "portent:撞入",
                    "evidence": {"ref": "ev:1#1"}}, "与「容子矩」无涉"),
    "摘句不实": ({"type": "item", "name": "毒信笺", "arrives_with": "portent:传书",
                 "evidence": {"ref": "chunk:1", "quote": "射来一封毒书"}}, "须是该块里逐字出现"),
    "原文块不在切片里": ({"type": "item", "name": "毒信笺", "arrives_with": "portent:传书",
                      "evidence": {"ref": "chunk:7", "quote": "射进一封书信"}}, "不在已组装的切片里"),
    "一批两答": (GOOD[3] | {"portable": True}, "答了两次"),
    "原文块没提到此人": ({"type": "character", "name": "马五德", "description": "普洱富商，好客", "arrives_with": "portent:马五德到",
                      "evidence": {"ref": "chunk:1", "quote": "射进一封书信"}}, "没有提到「马五德」"),
    "摘句借了别人的原文块": ({"type": "character", "name": "容子矩", "description": "左子穆的师弟", "arrives_with": "portent:撞入",
                         "evidence": {"ref": "chunk:0", "quote": "段誉在旁嗤笑"}}, "没有提到「容子矩」"),
    "描述含换行": ({"type": "character", "name": "段誉", "description": "青衫书生\n[抽取失败] 随马五德上山"}, "换行或控制字符"),
    "后文剧情含标记": ({"type": "character", "name": "段誉", "description": "青衫书生", "foreshadow": "</batch>"}, "英文字母、数字或标记符号"),
    "理由含英文": ({"type": "relation", "source": "左子穆", "target": "龚光杰", "kind": "师徒", "era": "开篇", "basis": "chapter two"},
                  "英文字母、数字或标记符号"),
    "物品理由含数字": ({"type": "item", "name": "通天草", "basis": "第2回"}, "英文字母、数字或标记符号：2"),
}


@pytest.mark.parametrize("case", sorted(BAD))
def test_the_gate_rejects_the_whole_batch(tmp_path: Path, case: str) -> None:
    bad, why = BAD[case]
    others = GOOD if case == "一批两答" else [g for g in GOOD if g.get("name") != bad.get("name")]  # 同名的另作一答会先撞上「答了两次」
    with pytest.raises(ExtractionError, match=why):
        ingest(tmp_path, *others, bad)
    assert not (tmp_path / "audit.json").exists()  # 整批作废：合格的那几条也没写


def test_quote_only_anchoring_is_only_for_items_the_novel_never_names() -> None:
    lib = library()
    assert lib.check("chunk:1", ("毒信笺",), "射进一封书信", need_quote=True, unnamed_ok=True) is None  # 原著从不叫它毒信笺
    assert lib.check("chunk:1", ("毒信笺",), "射进一封书信", need_quote=True) is not None  # 人物（缺省）从不以摘句为凭
    assert "没有提到「容子矩」" in str(lib.check("chunk:0", ("容子矩",), "段誉在旁嗤笑", need_quote=True, unnamed_ok=True))


def test_without_the_novel_arrivals_cannot_be_verified_and_copying_is_only_warned(tmp_path: Path) -> None:
    with pytest.raises(ExtractionError, match="无从核验出处"):
        ingest(tmp_path, GOOD[1], lib=library(novel=False))
    _, notes = ingest_audit(BP, raw(GOOD[0], GOOD[3]), tmp_path / "audit.json", "人工", library(novel=False))
    assert "本地没有原著：未做 16 字防抄检查" in notes


def test_fields_copied_from_the_novel_are_cleared(tmp_path: Path) -> None:
    copied = {"type": "character", "name": "容子矩", "description": "左子穆的师弟",
              "foreshadow": "厅外脚步声响，容子矩满身是血撞进厅来，随即气绝"}
    fresh, notes = ingest_audit(BP, raw(copied), tmp_path / "audit.json", "人工", library())
    assert fresh.characters[0].foreshadow == "" and fresh.characters[0].description == "左子穆的师弟"
    assert any("foreshadow 与原著共享 ≥16 字，已清空" in n for n in notes)


def test_the_merged_cache_must_still_make_a_valid_blueprint(tmp_path: Path) -> None:
    """掌故的 HAZARD 见闻靠着毒信笺的险性：把险性审掉会让蓝图不自洽——整批拒收。"""
    lored = BP.model_copy(update={"items": tuple(i.model_copy(update={"hazard": "剧毒"}) if i.name == "毒信笺" else i for i in BP.items)})
    lored = WorldBlueprint.model_validate({**lored.model_dump(), "facts": [Fact(
        id="fact:毒信", text="那封信碰不得", subject_ids=("itm:毒信笺",), knower_ids=("chr:左子穆",),
        unlock=FactUnlock(kind="HAZARD", target_id="itm:毒信笺"), sources=("chunk:1",)).model_dump()]})
    with pytest.raises(ExtractionError, match="不自洽"):
        ingest(tmp_path, {"type": "item", "name": "毒信笺", "hazard": None}, bp=lored)


# ============================================================
#  纯函数套用
# ============================================================
def test_apply_audit_is_pure_idempotent_and_revalidates() -> None:
    book = AuditBook(
        relations=(RelationVerdict(source_id="chr:龚光杰", target_id="chr:段誉", kind=RelationKind.ENEMY, era=Era.IMMINENT),),
        characters=(CharacterVerdict(id="chr:容子矩", was="左子穆的师弟，后来带伤撞回厅中气绝", description="左子穆的师弟",
                                     foreshadow="带伤撞回厅中气绝"),),
        items=(ItemVerdict(id="itm:无量玉璧", portable=False),),
    )
    before = BP.model_dump()
    once = apply_audit(BP, book)
    assert BP.model_dump() == before  # 不改入参
    assert apply_audit(once, book) == once  # 已审的蓝图再套一次，分毫不差
    assert audit_fingerprint(once) == audit_fingerprint(BP)  # 指纹只看审计不改的骨架
    with pytest.raises(ValueError, match="落不到"):
        apply_audit(BP, AuditBook(relations=(RelationVerdict(source_id="chr:段誉", target_id="chr:马五德",
                                                             kind=RelationKind.KIN, era=Era.LATER),)))
    with pytest.raises(ValueError):  # 字段约束照样把关：描述超长
        apply_audit(BP, AuditBook(characters=(CharacterVerdict(id="chr:段誉", was="", description="长" * 201),)))


# ============================================================
#  缓存 —— 版本、指纹、逐条新鲜
# ============================================================
def test_cache_invalidates_on_version_or_fingerprint_change(tmp_path: Path) -> None:
    ingest(tmp_path, *GOOD)
    path = tmp_path / "audit.json"
    grown = BP.model_copy(update={"relations": (*BP.relations, CharacterRelation(
        source_id="chr:段誉", target_id="chr:马五德", kind=RelationKind.SWORN))})
    book, notes = load_audit(path, grown)
    assert len(book) == 0 and "出自另一份蓝图" in notes[0]
    audited, lines = canonize_audit(grown, path)
    assert audited == grown and "整体作废" in lines[0]

    data = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**data, "version": "tlbb-audit-v0"}, ensure_ascii=False), encoding="utf-8")
    book, notes = load_audit(path, BP)
    assert len(book) == 0 and "口径" in notes[0]
    path.write_text("{坏的", encoding="utf-8")
    with pytest.raises(ExtractionError, match="已损坏"):
        load_audit(path, BP)
    assert canonize_audit(BP, path)[0] == BP  # 播种时从不抛错


def test_a_character_verdict_goes_stale_when_its_original_description_changes(tmp_path: Path) -> None:
    ingest(tmp_path, GOOD[1])
    moved = BP.model_copy(update={"characters": tuple(
        c.model_copy(update={"description": "左子穆的师弟，武功平平"}) if c.true_name == "容子矩" else c for c in BP.characters)})
    audited, lines = canonize_audit(moved, tmp_path / "audit.json")
    assert char(audited, "容子矩").arrives_with is None
    assert any("原描述已变" in ln for ln in lines)


def test_reanswering_an_audited_character_keeps_the_first_original(tmp_path: Path) -> None:
    ingest(tmp_path, GOOD[1])
    audited, _ = canonize_audit(BP, tmp_path / "audit.json")
    again = {"type": "character", "name": "容子矩", "description": "左子穆的师弟，后来带伤撞回厅中气绝", "foreshadow": ""}
    fresh = ingest(tmp_path, again, bp=audited)  # 新描述的专名对照的是最初的原描述，不是上一版结论
    assert fresh.characters[0].was == "左子穆的师弟，后来带伤撞回厅中气绝"
    assert char(apply_audit(audited, load_audit(tmp_path / "audit.json", audited)[0]), "容子矩").foreshadow == ""


def test_save_then_load_round_trips_sorted(tmp_path: Path) -> None:
    book = AuditBook(items=(ItemVerdict(id="itm:通天草"), ItemVerdict(id="itm:毒信笺", hazard="有毒")))
    merged = AuditBook().merge(book)
    save_audit(tmp_path / "audit.json", BP, merged)
    assert load_audit(tmp_path / "audit.json", BP) == (merged, [])
    assert [v.id for v in merged.items] == ["itm:毒信笺", "itm:通天草"]


# ============================================================
#  题面
# ============================================================
def test_export_batches_are_self_contained_and_skip_what_is_answered(tmp_path: Path) -> None:
    files = audit_export(BP, library(), AuditBook(), batch=2)
    assert sorted(files) == ["audit-character-01.txt", "audit-character-02.txt", "audit-character-03.txt",
                             "audit-item-01.txt", "audit-item-02.txt", "audit-relation-01.txt", "audit-relation-02.txt"]
    first = files["audit-item-01.txt"]
    assert first.startswith("你是《天龙八部》世界图谱的 T=0 审计员") and "ev:1#0 容子矩 身故" in first
    assert '"type": "item", "name": "毒信笺"' in first  # 答题模板
    assert "提到它的原文块：（原文不用这个名字" in first  # 毒信笺在原文里不叫这个名字

    fresh = ingest(tmp_path, *GOOD)
    rest = audit_export(BP, library(), fresh)
    assert "容子矩" not in rest["audit-character-01.txt"].split("<batch kind=")[1]  # 已有结论的不再出题
    assert "audit-item-01.txt" not in rest  # 三件物品都已审过：没有物品题面
    assert "毒信笺" in audit_export(BP, library(), fresh, everything=True)["audit-item-01.txt"].split("<batch kind=")[1]


# ============================================================
#  命令行 —— export → ingest → assemble，全程不装配大模型
# ============================================================
def test_seed_cli_audit_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import seed
    from app.config import Settings

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "书.txt").write_text("一 青衫磊落险峰行\n" + TEXT0 + "\n" + TEXT1, encoding="utf-8")
    settings = Settings(_env_file=None, llm_provider="gemini", llm_api_key="g", llm_model="pro",  # type: ignore[call-arg]
                        source_text_dir=tmp_path / "src", world_dir=tmp_path / "world", extraction_chunk_chars=1000)
    monkeypatch.setattr(seed, "get_settings", lambda: settings)

    def paid(*_: object) -> None:
        raise AssertionError("审计不得装配付费大模型")

    monkeypatch.setattr(seed, "build_llm", paid)
    chunk = chunk_text(load_corpus(tmp_path / "src")[0], 1000)[0]
    store_extraction(settings.world_dir / "cache", chunk, json.dumps({
        "locations": [{"name": "练武厅", "exits": [{"label": "出厅", "destination": "剑湖宫"}]}, {"name": "剑湖宫"}],
        "characters": [{"name": "左子穆", "faction": "无量剑东宗", "location": "练武厅", "description": "东宗掌门，好面子"},
                       {"name": "容子矩", "faction": "无量剑东宗", "location": "练武厅", "description": "左子穆的师弟，后来带伤撞回"}],
        "items": [{"name": "毒信笺", "location": "练武厅", "description": "封皮抹了剧毒的书信"}],
        "relations": [{"source": "左子穆", "target": "容子矩", "kind": "同门"}],
        "events": [{"subject": "容子矩", "kind": "身故", "note": "带伤撞回厅中气绝"}],
    }, ensure_ascii=False))

    def blueprint() -> WorldBlueprint:
        return WorldBlueprint.model_validate_json(settings.blueprint_path.read_text(encoding="utf-8"))

    seed.main(["assemble"])
    seed.main(["audit", "--export", str(tmp_path / "jobs"), "--evidence-version", PROMPT_VERSION])
    jobs = sorted(p.name for p in (tmp_path / "jobs").iterdir())
    assert jobs == ["audit-character-01.txt", "audit-item-01.txt", "audit-relation-01.txt"]
    assert "ev:0#0 容子矩 身故" in (tmp_path / "jobs" / "audit-character-01.txt").read_text(encoding="utf-8")

    (tmp_path / "answer.json").write_text(raw(
        {"type": "character", "name": "容子矩", "description": "左子穆的师弟", "foreshadow": "后来带伤撞回",
         "arrives_with": "portent:容子矩撞入", "evidence": {"ref": "ev:0#0"}},
        {"type": "item", "name": "毒信笺", "hazard": "剧毒", "arrives_with": "portent:传书",
         "evidence": {"ref": "chunk:0", "quote": "射进一封书信"}},
    ), encoding="utf-8")
    with pytest.raises(SystemExit):
        seed.main(["audit", "--ingest", str(tmp_path / "answer.json")])  # 作答者必须署名
    seed.main(["audit", "--ingest", str(tmp_path / "answer.json"), "--by", "claude-subagent", "--evidence-version", PROMPT_VERSION])
    assert char(blueprint(), "容子矩").arrives_with == "portent:容子矩撞入"
    assert item(blueprint(), "毒信笺").hazard == "剧毒"
    report = (settings.world_dir / "report.txt").read_text(encoding="utf-8")
    assert "[审计] 「容子矩」后来才到场：portent:容子矩撞入" in report
    assert "foreshadow" not in (settings.world_dir / "seed.cypher").read_text(encoding="utf-8")

    with pytest.raises(SystemExit, match="整批未入缓存"):
        (tmp_path / "bad.json").write_text(raw({"type": "item", "name": "毒", "portable": False}), encoding="utf-8")
        seed.main(["audit", "--ingest", str(tmp_path / "bad.json"), "--by", "claude-subagent"])

    audited = blueprint()
    seed.main(["assemble"])  # 重新组装：审计缓存零费用自动套用，与 ingest 写回的蓝图分毫不差
    assert blueprint() == audited
    seed.main(["audit"])  # 不带参数即按缓存重新套用：幂等
    assert blueprint() == audited
    seed.main(["audit", "--export", str(tmp_path / "jobs2"), "--evidence-version", PROMPT_VERSION])
    assert "容子矩" not in (tmp_path / "jobs2" / "audit-character-01.txt").read_text(encoding="utf-8").split("<batch kind=")[1]


# ============================================================
#  P1-B 审查的回归 —— 专名名录、T=0 描述撞上后文、入缓存的新鲜度、已审蓝图无缓存即拒绝、报告一条一行
# ============================================================
def test_the_stray_lexicon_only_holds_names_the_assembler_would_accept(tmp_path: Path) -> None:
    record = ChunkExtraction.model_validate({
        "locations": [{"name": "厅上"}, {"name": "练武厅", "aliases": ["山谷"], "region": "大路上"}],
        "characters": [{"name": "木婉清", "titles": ["香药叉"], "aliases": ["左掌门"], "faction": "神农帮"},
                       {"name": "容师弟"}, {"name": "小姑娘", "name_is_title": True}, {"name": "那老和尚"}],
        "martial_arts": [{"name": "凌波微步", "aliases": ["剑术", "内力"]}, {"name": "剑法"}, {"name": "本门内功"}],
    })
    (tmp_path / "cache" / EVIDENCE_VERSION).mkdir(parents=True)
    (tmp_path / "cache" / EVIDENCE_VERSION / "x.json").write_text(record.model_dump_json(), encoding="utf-8")
    lib = Library.load(tmp_path / "no-source", tmp_path / "cache")
    assert lib.lexicon == {"木婉清", "神农帮", "凌波微步"}  # 地名、称号、别名、尊称、泛称一概不收

    halls = BP.model_copy(update={"locations": tuple(
        loc.model_copy(update={"name": "剑湖宫·练武厅"}) if loc.id == "loc:练武厅" else loc for loc in BP.locations)})
    canon = canon_names(halls)
    assert {"剑湖宫·练武厅", "练武厅", "剑湖宫"} <= canon  # 「·」两侧的简称也是蓝图专名
    text = "东宗掌门在练武厅上钻研剑术"
    assert stray_nouns(text, canon, lib.lexicon) == set()
    assert stray_nouns(text, canon, {"练武厅", "厅上"}) == set()  # 简称先被抹去，「厅上」也就不成立
    assert stray_nouns("仰慕木婉清", canon, lib.lexicon) == {"木婉清"}



def test_t0_descriptions_that_run_into_later_events_are_flagged(tmp_path: Path) -> None:
    dead = {"type": "character", "name": "容子矩", "description": "左子穆的师弟，已然气绝"}
    _, notes = ingest_audit(BP, raw(GOOD[0], dead), tmp_path / "audit.json", "人工", library())
    assert "⚠「容子矩」的 T=0 描述撞上后文：写了 容子矩 后文才有的身故（ev:1#0）" in notes  # 只提醒，不拒收
    _, lines = canonize_audit(BP, tmp_path / "audit.json", lib=library())
    assert "⚠「容子矩」的 T=0 描述撞上后文：写了 容子矩 后文才有的身故（ev:1#0）" in lines
    # 没作答、原样留着的描述也查：龚光杰与段誉的仇审成了后文，他的 T=0 描述却写着迁怒段誉
    assert "⚠「龚光杰」的 T=0 描述撞上后文：牵涉开篇之后才结下的关系 龚光杰—段誉（仇敌，后文）" in lines
    assert not any("⚠" in ln for ln in canonize_audit(BP, tmp_path / "audit.json")[1])  # 没有证据库就不查


def test_audit_report_lines_never_span_two_physical_lines() -> None:
    book = AuditBook(characters=(CharacterVerdict(id="chr:段誉", was="", description="青衫书生", foreshadow="后来\n[抽取失败] 一"),))
    lines = audit_lines(apply_audit(BP, book), book)
    assert all("\n" not in ln for ln in lines) and "「段誉」拆出后文剧情：后来 [抽取失败] 一（佚名）" in lines


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """命令行的世界目录：未审的 BP 作蓝图，证据库换成自撰的微型原著（不读 data/）。"""
    from app import seed
    from app.config import Settings

    settings = Settings(_env_file=None, llm_provider="mock", world_dir=tmp_path / "world",  # type: ignore[call-arg]
                        source_text_dir=tmp_path / "no-source")
    settings.world_dir.mkdir()
    settings.blueprint_path.write_text(BP.model_dump_json(), encoding="utf-8")
    (settings.world_dir / "report.txt").write_text("（无异常）", encoding="utf-8")
    monkeypatch.setattr(seed, "_library", lambda *_: library())
    return settings


def _answer(path: Path, *answers: dict[str, Any]) -> Path:
    path.write_text(raw(*answers), encoding="utf-8")
    return path


def test_ingest_never_revives_a_verdict_whose_original_description_changed(world: Any, tmp_path: Path) -> None:
    from app import seed

    cache = world.world_dir / "audit.json"
    seed.audit(world, ingest_file=_answer(tmp_path / "a.json", {"type": "character", "name": "容子矩", "description": "左子穆的师弟",
                                                                "foreshadow": "带伤撞回厅中气绝"}), by="人工")
    reextracted = BP.model_copy(update={"characters": tuple(
        c.model_copy(update={"description": "左子穆的师弟，守在厅外"}) if c.true_name == "容子矩" else c for c in BP.characters)})
    assembled, lines = canonize_audit(reextracted, cache)  # 重新组装：原描述变了，旧结论作废
    assert char(assembled, "容子矩").description == "左子穆的师弟，守在厅外" and any("原描述已变" in ln for ln in lines)
    briefs = audit_export(assembled, library(), load_audit(cache, assembled)[0])
    assert '<character name="容子矩">' in briefs["audit-character-01.txt"]  # 作废的结论不算已审：照样出题

    world.blueprint_path.write_text(assembled.model_dump_json(), encoding="utf-8")
    out = seed.audit(world, ingest_file=_answer(tmp_path / "b.json", GOOD[0]), by="人工")  # 无关的一批作答
    assert char(out, "容子矩").description == "左子穆的师弟，守在厅外"  # 不被作废的旧结论改回去
    assert load_audit(cache, out)[0].characters == ()  # 作废的结论已剔出缓存
    assert char(seed.audit(world), "容子矩").description == "左子穆的师弟，守在厅外"


def test_reanswering_through_the_cli_applies_the_new_answer(world: Any, tmp_path: Path) -> None:
    from app import seed

    first = {"type": "character", "name": "容子矩", "description": "左子穆的师弟", "foreshadow": "带伤撞回厅中气绝"}
    seed.audit(world, ingest_file=_answer(tmp_path / "a.json", first), by="人工")
    again = {"type": "character", "name": "容子矩", "description": "左子穆的师弟，后来带伤撞回厅中气绝", "foreshadow": ""}
    out = seed.audit(world, ingest_file=_answer(tmp_path / "b.json", again), by="人工")
    assert (char(out, "容子矩").description, char(out, "容子矩").foreshadow) == ("左子穆的师弟，后来带伤撞回厅中气绝", "")


async def test_an_audited_blueprint_without_a_usable_cache_is_refused(world: Any, tmp_path: Path) -> None:
    from app import seed

    cache = world.world_dir / "audit.json"
    seed.audit(world, ingest_file=_answer(tmp_path / "a.json", *GOOD[:2]), by="人工")
    assert unbacked_audit(WorldBlueprint.model_validate_json(world.blueprint_path.read_text("utf-8")), cache) is None
    data = json.loads(cache.read_text(encoding="utf-8"))
    for broken in (json.dumps({**data, "version": "tlbb-audit-v0"}, ensure_ascii=False), "{坏的", None):
        if broken is None:
            cache.unlink()
        else:
            cache.write_text(broken, encoding="utf-8")
        with pytest.raises(SystemExit, match=r"请先运行 python -m app\.seed assemble"):
            seed.audit(world, export_dir=tmp_path / "jobs")  # 否则题面会把审过的「左子穆的师弟」当成原描述
        with pytest.raises(SystemExit, match="assemble"):
            seed.audit(world)  # 否则缓存作废了，审计的痕迹却撤不掉
        with pytest.raises(SystemExit, match="assemble"):
            await seed.heal(world)
    world.blueprint_path.write_text(BP.model_dump_json(), encoding="utf-8")  # assemble 出的底本没有审计痕迹：照常出题
    seed.audit(world, export_dir=tmp_path / "jobs")
    assert "左子穆的师弟，后来带伤撞回厅中气绝" in (tmp_path / "jobs" / "audit-character-01.txt").read_text(encoding="utf-8")
