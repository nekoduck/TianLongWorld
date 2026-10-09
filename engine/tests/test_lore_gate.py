"""
[INPUT]: 依赖 app.infrastructure.lore_gate（范围 / 作答契约 / 闸门 / 纯函数套用 / 缓存 / 题面）、canon_audit 的 Library / EVIDENCE_VERSION、
         knowledge_extractor 的 store_extraction / chunk_text / load_corpus，依赖 tests/test_canon_audit 的微型蓝图 BP 与自撰原文，依赖 app.seed 命令行
[OUTPUT]: 掌故闸门单测：以一地为中心沿 CONNECTS_TO 走若干跳的地与人；一批合格的人设与见闻（MOTIVE / TEACHING / HAZARD / LEVERAGE 各落在一条边上）入缓存并套上蓝图；
          闸门逐类拒收整批（包含匹配、出处不在切片 / 没提到此人 / 格式不对、蓝图外专名、写了后文才有的武学或身故、牵涉结于后文的关系、
          知情人无涉或只靠后文关系、unlock 落不到边上、MOTIVE 无人设、照抄原著、人设超长、一批两答、多余字段、封闭枚举、id 格式、本地无原著）且缓存一字不写；
          apply_lore 是纯函数、幂等、引用落不了地即抛错；指纹随 era 而变、不随描述而变，缓存作废时掌故清空；题面分批、列出后文关系与险物；
          命令行 export（打印几地几人）→ ingest（须署名）→ heal 与 audit 之后掌故照缓存补回、审计改了 era 掌故即整体作废，全程不装配大模型
[POS]: tests 的"掌故不杜撰"证明：人设与见闻只能把蓝图里已有的边配上一句话、交到合理知情的人手里，后文剧情一个字也漏不进来
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import json
from pathlib import Path
from typing import Any

import pytest

from app.domain.lore import Fact, Persona
from app.domain.models import Era, RelationKind, WorldBlueprint
from app.errors import ExtractionError
from app.infrastructure.canon_audit import EVIDENCE_VERSION, Library
from app.infrastructure.knowledge_extractor import chunk_text, load_corpus, store_extraction
from app.infrastructure.lore_gate import (
    LORE_PROMPT_VERSION,
    FactEntry,
    LoreBook,
    PersonaEntry,
    apply_lore,
    canonize_lore,
    ingest_lore,
    load_lore,
    lore_export,
    lore_fingerprint,
    region,
)
from tests.test_canon_audit import BP, TEXT0, TEXT1, library, raw

# 审计之后的样子：毒信笺有毒，龚光杰与段誉的仇结于后文
AUDITED = WorldBlueprint.model_validate({
    **BP.model_dump(),
    "items": [i.model_dump() | ({"hazard": "剧毒"} if i.name == "毒信笺" else {}) for i in BP.items],
    "relations": [r.model_dump() | ({"era": Era.LATER} if r.kind is RelationKind.ENEMY else {}) for r in BP.relations],
})

GOOD: list[dict[str, Any]] = [
    {"type": "persona", "character": "左子穆", "likes": ["旁人称颂东宗剑法"], "dislikes": ["当众失了颜面"], "sources": ["chunk:0"]},
    {"type": "fact", "id": "fact:左子穆好颜面", "text": "东宗掌门最看重本门颜面", "subjects": ["左子穆"], "knowers": ["龚光杰"],
     "unlock": {"kind": "MOTIVE", "target": "左子穆"}, "sources": ["chunk:0"]},
    {"type": "fact", "id": "fact:白虹贯日", "text": "左子穆身负一招白虹贯日", "subjects": ["左子穆"], "knowers": ["龚光杰"],
     "unlock": {"kind": "TEACHING", "target": "白虹贯日"}, "sources": ["chunk:0"]},
    {"type": "fact", "id": "fact:毒信", "text": "厅上那封信碰不得", "subjects": ["毒信笺"], "knowers": ["左子穆"],
     "unlock": {"kind": "HAZARD", "target": "毒信笺"}, "sources": ["chunk:0"]},
    {"type": "fact", "id": "fact:得意门徒", "text": "龚光杰是左子穆最看重的弟子", "subjects": ["龚光杰", "左子穆"], "knowers": ["龚光杰"],
     "unlock": {"kind": "LEVERAGE", "target": "左子穆"}, "sources": ["chunk:0"]},
]


def ingest(tmp_path: Path, *answers: dict[str, Any], lib: Library | None = None, bp: WorldBlueprint = AUDITED) -> LoreBook:
    return ingest_lore(bp, raw(*answers), tmp_path / "lore.json", "claude-subagent", lib or library())[0]


# ============================================================
#  范围
# ============================================================
def test_region_walks_connects_to_from_a_seed_location() -> None:
    assert [loc.name for loc in region(BP, "练武厅", 0).places] == ["练武厅"]
    assert [loc.name for loc in region(BP, "练武厅", 1).places] == ["练武厅", "剑湖宫"]
    two = region(BP, "loc:练武厅", 2)
    assert [loc.name for loc in two.places] == ["练武厅", "剑湖宫", "无量山"] and len(two.people) == 5
    with pytest.raises(ValueError, match="没有名为「练武」"):
        region(BP, "练武", 2)  # 地名也是全等，不做包含匹配


# ============================================================
#  合格的一批
# ============================================================
def test_a_good_batch_is_cached_and_lands_on_the_blueprint(tmp_path: Path) -> None:
    fresh = ingest(tmp_path, *GOOD)
    assert len(fresh.personas) == 1 and len(fresh.facts) == 4
    cache = json.loads((tmp_path / "lore.json").read_text(encoding="utf-8"))
    assert cache["version"] == LORE_PROMPT_VERSION and cache["fingerprint"] == lore_fingerprint(AUDITED)
    lored, lines = canonize_lore(AUDITED, tmp_path / "lore.json")
    assert [p.character_id for p in lored.personas] == ["chr:左子穆"]
    assert {f.id: f.unlock.kind for f in lored.facts if f.unlock} == {
        "fact:左子穆好颜面": "MOTIVE", "fact:白虹贯日": "TEACHING", "fact:毒信": "HAZARD", "fact:得意门徒": "LEVERAGE"}
    assert lines[0] == "人设 1 位、见闻 4 条（provenance 推断）"
    assert any(ln.startswith("「左子穆」人设：好 旁人称颂东宗剑法；恶 当众失了颜面（claude-subagent") for ln in lines)


# ============================================================
#  闸门 —— 有一条不合格，整批拒收，缓存一字不写
# ============================================================
def _fact(fid: str, text: str, subjects: list[str], knowers: list[str], **extra: Any) -> dict[str, Any]:
    return {"type": "fact", "id": fid, "text": text, "subjects": subjects, "knowers": knowers, "sources": ["chunk:0"], **extra}


BAD: dict[str, tuple[dict[str, Any], str]] = {
    "包含匹配": ({"type": "persona", "character": "左子", "likes": ["好面子"], "sources": ["chunk:0"]}, "没有名为「左子」"),
    "出处不在切片": ({"type": "persona", "character": "段誉", "likes": ["好读书"], "sources": ["chunk:7"]}, "不在已组装的切片里"),
    "出处没提到此人": ({"type": "persona", "character": "容子矩", "likes": ["护短"], "sources": ["chunk:0"]}, "没有提到「容子矩」"),
    "出处格式": ({"type": "persona", "character": "段誉", "likes": ["好读书"], "sources": ["page:1"]}, "pattern"),
    "蓝图外专名": ({"type": "persona", "character": "段誉", "likes": ["仰慕木婉清"], "sources": ["chunk:0"]}, "蓝图之外的专名：木婉清"),
    "后文才会的武学": ({"type": "persona", "character": "段誉", "likes": ["凌波微步"], "sources": ["chunk:0"]}, "后文才习得武学的「凌波微步」"),
    "后文才有的身故": (_fact("fact:容子矩", "容子矩已死于非命", ["容子矩"], ["左子穆"], sources=["chunk:1"]), "后文才有的身故"),
    "结于后文的关系": ({"type": "persona", "character": "龚光杰", "dislikes": ["段誉嗤笑"], "sources": ["chunk:0"]}, "结于后文的关系"),
    "知情人无涉": (_fact("fact:掌门", "左子穆好面子", ["左子穆"], ["马五德"]), "马五德 与主体无涉"),
    "知情人只靠后文关系": (_fact("fact:得胜", "龚光杰比剑得胜", ["龚光杰"], ["段誉"]), "段誉 与主体无涉"),
    "unlock 落不到边上": (_fact("fact:剑招", "龚光杰会白虹贯日", ["龚光杰"], ["龚光杰"],
                              unlock={"kind": "TEACHING", "target": "白虹贯日"}), "落不到蓝图的边上"),
    "MOTIVE 无人设": (_fact("fact:书生", "段誉爱读书", ["段誉"], ["段誉"], unlock={"kind": "MOTIVE", "target": "段誉"}), "落不到蓝图的边上"),
    "照抄原著": (_fact("fact:撞入", "厅外脚步声响，容子矩满身是血撞进厅来", ["容子矩"], ["左子穆"], sources=["chunk:1"]), "与原著共享"),
    "人设超长": ({"type": "persona", "character": "段誉", "likes": ["一" * 17], "sources": ["chunk:0"]}, "16"),
    "一批两答": (GOOD[0] | {"likes": ["好剑"]}, "答了两次"),
    "多余字段": (_fact("fact:密", "左子穆好面子", ["左子穆"], ["龚光杰"], secrecy="公开"), "不合契约"),
    "unlock 枚举之外": (_fact("fact:路", "左子穆好面子", ["左子穆"], ["龚光杰"], unlock={"kind": "PATH", "target": "剑湖宫"}), "不合契约"),
    "id 格式": (_fact("左子穆好面子", "左子穆好面子", ["左子穆"], ["龚光杰"]), "pattern"),
}


@pytest.mark.parametrize("case", sorted(BAD))
def test_the_gate_rejects_the_whole_batch(tmp_path: Path, case: str) -> None:
    bad, why = BAD[case]
    with pytest.raises(ExtractionError, match=why):
        ingest(tmp_path, *GOOD, bad)
    assert not (tmp_path / "lore.json").exists()


def test_without_the_novel_sources_cannot_be_verified(tmp_path: Path) -> None:
    with pytest.raises(ExtractionError, match="无从核验出处"):
        ingest(tmp_path, GOOD[0], lib=library(novel=False))


def test_later_batches_build_on_earlier_ones(tmp_path: Path) -> None:
    """MOTIVE 指向的人设可以出自此前入缓存的一批；同一 id 的新答案覆盖旧的。"""
    ingest(tmp_path, GOOD[0])
    ingest(tmp_path, GOOD[1])
    ingest(tmp_path, GOOD[1] | {"text": "东宗掌门最受不得怠慢"})
    book, _ = load_lore(tmp_path / "lore.json", AUDITED)
    assert [e.fact.text for e in book.facts] == ["东宗掌门最受不得怠慢"] and len(book.personas) == 1


# ============================================================
#  纯函数套用与缓存
# ============================================================
def test_apply_lore_is_pure_idempotent_and_revalidates() -> None:
    book = LoreBook(
        personas=(PersonaEntry(persona=Persona(character_id="chr:左子穆", likes=("称颂",), sources=("chunk:0",))),),
        facts=(FactEntry(fact=Fact(id="fact:x", text="左子穆好面子", subject_ids=("chr:左子穆",), knower_ids=("chr:龚光杰",),
                                   sources=("chunk:0",))),),
    )
    before = AUDITED.model_dump()
    once = apply_lore(AUDITED, book)
    assert AUDITED.model_dump() == before and apply_lore(once, book) == once
    assert lore_fingerprint(once) == lore_fingerprint(AUDITED)  # 掌故本身不进指纹
    ghost = FactEntry(fact=Fact(id="fact:y", text="查无此人", subject_ids=("chr:乔峰",), knower_ids=("chr:龚光杰",), sources=("chunk:0",)))
    with pytest.raises(ValueError, match="不存在"):
        apply_lore(AUDITED, LoreBook(facts=(ghost,)))


def test_fingerprint_follows_era_and_traits_but_not_descriptions(tmp_path: Path) -> None:
    ingest(tmp_path, *GOOD)
    path = tmp_path / "lore.json"
    redescribed = AUDITED.model_copy(update={"characters": tuple(
        c.model_copy(update={"description": "另一种写法"}) for c in AUDITED.characters)})
    assert lore_fingerprint(redescribed) == lore_fingerprint(AUDITED)
    assert len(canonize_lore(redescribed, path)[0].facts) == 4
    reopened = AUDITED.model_copy(update={"relations": tuple(r.model_copy(update={"era": Era.OPENING}) for r in AUDITED.relations)})
    stripped, lines = canonize_lore(apply_lore(reopened, load_lore(path, AUDITED)[0]), path)
    assert stripped.personas == () and stripped.facts == () and "出自另一份蓝图" in lines[0]  # 缓存作废：蓝图上的旧掌故一并清空

    data = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**data, "version": "tlbb-lore-v0"}, ensure_ascii=False), encoding="utf-8")
    assert "口径" in load_lore(path, AUDITED)[1][0]
    path.write_text("[", encoding="utf-8")
    with pytest.raises(ExtractionError, match="已损坏"):
        load_lore(path, AUDITED)
    assert canonize_lore(AUDITED, path)[0] == AUDITED  # 播种时从不抛错


# ============================================================
#  题面
# ============================================================
def test_export_brief_lists_scope_later_relations_and_hazards(tmp_path: Path) -> None:
    files, area = lore_export(AUDITED, library(), LoreBook(), start="练武厅", hops=2, batch=2)
    assert sorted(files) == ["lore-01.txt", "lore-02.txt", "lore-03.txt"] and len(area.places) == 3 and len(area.people) == 5
    brief = files["lore-01.txt"]
    assert brief.startswith("你是《天龙八部》世界的掌故撰写者") and "ev:1#1 段誉 习得武学：凌波微步" in brief
    assert "<later_relations>\n龚光杰—段誉（仇敌）\n</later_relations>" in brief
    assert "毒信笺（书信；静置于 练武厅；险性 剧毒）" in brief
    assert '"type": "persona", "character": "左子穆"' in brief and "提到此人的原文块：chunk:0" in brief
    assert "龚光杰：师徒（开篇；左子穆是上首）：得意门徒" in brief
    ingest(tmp_path, *GOOD)
    again, _ = lore_export(AUDITED, library(), load_lore(tmp_path / "lore.json", AUDITED)[0], start="练武厅")
    assert "fact:毒信：厅上那封信碰不得" in again["lore-01.txt"] and "已有人设：" in again["lore-01.txt"]


# ============================================================
#  命令行 —— export → ingest；heal 与 audit 之后掌故照缓存补回或整体作废；全程不装配大模型
# ============================================================
def test_seed_cli_lore_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from app import seed
    from app.config import Settings

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "书.txt").write_text("一 青衫磊落险峰行\n" + TEXT0 + "\n" + TEXT1, encoding="utf-8")
    settings = Settings(_env_file=None, llm_provider="gemini", llm_api_key="g", llm_model="pro",  # type: ignore[call-arg]
                        source_text_dir=tmp_path / "src", world_dir=tmp_path / "world", extraction_chunk_chars=1000)
    monkeypatch.setattr(seed, "get_settings", lambda: settings)

    def paid(*_: object) -> None:
        raise AssertionError("掌故不得装配付费大模型")

    monkeypatch.setattr(seed, "build_llm", paid)
    chunk = chunk_text(load_corpus(tmp_path / "src")[0], 1000)[0]
    store_extraction(settings.world_dir / "cache", chunk, json.dumps({"characters": [{"name": "容子矩"}], "events": [
        {"subject": "段誉", "kind": "习得武学", "object": "凌波微步"}]}, ensure_ascii=False), version=EVIDENCE_VERSION)
    settings.blueprint_path.write_text(AUDITED.model_dump_json(), encoding="utf-8")
    (settings.world_dir / "report.txt").write_text("（无异常）", encoding="utf-8")

    def blueprint() -> WorldBlueprint:
        return WorldBlueprint.model_validate_json(settings.blueprint_path.read_text(encoding="utf-8"))

    seed.main(["lore", "--export", str(tmp_path / "jobs"), "--from", "练武厅"])
    assert "3 地 5 人" in capsys.readouterr().out
    assert "ev:0#0 段誉 习得武学：凌波微步" in (tmp_path / "jobs" / "lore-01.txt").read_text(encoding="utf-8")
    with pytest.raises(SystemExit, match="没有名为「无此地」"):
        seed.main(["lore", "--export", str(tmp_path / "jobs"), "--from", "无此地"])

    (tmp_path / "answer.json").write_text(raw(*GOOD), encoding="utf-8")
    with pytest.raises(SystemExit):
        seed.main(["lore", "--ingest", str(tmp_path / "answer.json")])  # 作答者必须署名
    seed.main(["lore", "--ingest", str(tmp_path / "answer.json"), "--by", "claude-subagent"])
    assert len(blueprint().personas) == 1 and len(blueprint().facts) == 4
    assert "[掌故] 人设 1 位、见闻 4 条（provenance 推断）" in (settings.world_dir / "report.txt").read_text(encoding="utf-8")
    (tmp_path / "bad.json").write_text(raw({"type": "persona", "character": "段誉", "likes": ["凌波微步"], "sources": ["chunk:0"]}), "utf-8")
    with pytest.raises(SystemExit, match="整批未入缓存"):
        seed.main(["lore", "--ingest", str(tmp_path / "bad.json"), "--by", "claude-subagent"])

    lored = blueprint()
    seed.main(["heal"])  # 自愈重建蓝图时不带掌故：照缓存补回，分毫不差
    assert blueprint() == lored
    seed.main(["lore"])
    assert blueprint() == lored

    (tmp_path / "audit.json").write_text(raw({"type": "relation", "source": "龚光杰", "target": "段誉", "kind": "仇敌", "era": "开篇"}), "utf-8")
    seed.main(["audit", "--ingest", str(tmp_path / "audit.json"), "--by", "claude-subagent"])  # 审计改了 era：掌故整体作废
    assert blueprint().personas == () and blueprint().facts == ()
    assert "[掌故] 掌故缓存出自另一份蓝图" in (settings.world_dir / "report.txt").read_text(encoding="utf-8")
