"""
[INPUT]: 依赖 app.infrastructure.geo_gate（出口对 / 作答契约 / 闸门 / 纯函数套用 / 缓存与指纹 / 报告 / 题面）、canon_audit 的 Library / apply_audit / AuditBook、
         lore_gate 的 apply_lore / lore_fingerprint / load_lore / LoreBook、graph_linter 的 apply_placements、domain/geography 的 ways 与词汇，
         依赖 app.seed 命令行（geo 子命令与 _canonize 的套用顺序；证据库经 _library 换成自撰原文），依赖入库的 data/world/blueprint.json、lore.json、audit.json
[OUTPUT]: 地理注记闸门单测：出口按无向的一对从起点广度优先排定（每对一次、连通片走完再续、起点全等落地）；一批合格的道路与可见性入缓存、套上蓝图、ways 改读注记、报告一条一行；
          闸门逐类整批拒收（地名包含匹配、不是出口、两头都有出口只答一向、不明、往返不相反、往返都是坠落、耗时越界、交通方式枚举、basis 含英文数字换行或照抄、
          出处不在切片或没提到两端、可见性两项皆假或出处没提到此地、一批两答、多余字段、本地无原著）且缓存一字不写；单向出口只答一向即可、后一批在缓存上补齐另一向、新答覆盖旧答；
          apply_geography 纯函数、幂等、出界即抛错；指纹只随地点名字与出口拓扑而变（不随标签、描述、注记本身），作废即清空注记、损坏照报不抛；
          审计、掌故、自愈重建蓝图时注记原样带过，掌故指纹不因注记而变，入库的 lore.json 与 audit.json 对入库蓝图依旧有效；
          题面分批（一对两向同批、两向已答的不再出、--all 全出）、推出的缺省值与原文块、可见性题面列全部地点；入库蓝图 126 对 252 条出口恰好各出一次；
          命令行 geo export → ingest（须署名）→ lore / heal / audit / geo 之后注记照缓存补回，出口拓扑一变即整体作废，全程不装配大模型
[POS]: tests 的"地理不杜撰"证明：方位、交通方式与耗时只能落在蓝图已有的出口上、往返自洽、出处可核验，入库与否都不牵动审计与掌故
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import json
from pathlib import Path
from typing import Any

import pytest

from app.domain.geography import Direction, Passage, Sight, TravelMethod, ways
from app.domain.models import Location, WorldBlueprint
from app.errors import ExtractionError
from app.infrastructure.canon_audit import AuditBook, Library, apply_audit, load_audit
from app.infrastructure.geo_gate import (
    GEO_PROMPT_VERSION,
    GeoBook,
    PassageEntry,
    SightEntry,
    apply_geography,
    canonize_geography,
    geo_export,
    geo_fingerprint,
    ingest_geography,
    load_geography,
    pairs,
)
from app.infrastructure.graph_linter import apply_placements
from app.infrastructure.lore_gate import LoreBook, apply_lore, load_lore, lore_fingerprint

ENGINE = Path(__file__).resolve().parents[1]

# ============================================================
#  夹具 —— 自撰的微型「原著」与它的地图（非原著文本）
# ============================================================
TEXT0 = "段誉随众人进了剑湖宫，穿过前院来到练武厅，只见东西二宗弟子分坐两侧。宫外便是无量山的层层峭壁。"
TEXT1 = "那人在后山一脚踏空，直跌进谷底，四下里尽是光溜溜的石壁。"
TEXT2 = "大理城外有个善人渡，渡口人来人往。普洱的马五德是个富商。"

GEO = WorldBlueprint(locations=(
    Location(id="loc:剑湖宫", name="剑湖宫", aliases=("无量宫",), exits={"入练武厅": "loc:剑湖宫·练武厅", "出大门": "loc:无量山"}),
    Location(id="loc:剑湖宫·练武厅", name="剑湖宫·练武厅", exits={"出厅门": "loc:剑湖宫"}),
    Location(id="loc:无量山", name="无量山", exits={"入剑湖宫": "loc:剑湖宫", "失足坠崖": "loc:无量山·谷底", "往普洱": "loc:普洱"}),
    Location(id="loc:无量山·谷底", name="无量山·谷底", exits={"攀上山去": "loc:无量山"}),
    Location(id="loc:普洱", name="普洱"),  # 单向出口：只写了无量山那一头
    Location(id="loc:大理城", name="大理城", exits={"往善人渡": "loc:善人渡"}),  # 另一个连通片
    Location(id="loc:善人渡", name="善人渡", exits={"往大理城": "loc:大理城"}),
    Location(id="loc:山西", name="山西"),  # 孤零零的一处：没有出口
))


def library(*, novel: bool = True) -> Library:
    if not novel:
        return Library()
    return Library({0: TEXT0, 1: TEXT1, 2: TEXT2}, {}, TEXT0 + TEXT1 + TEXT2, frozenset())


def passage(a: str, b: str, direction: str, method: str = "步行", cost: int = 4, **extra: Any) -> dict[str, Any]:
    return {"type": "passage", "from": a, "to": b, "direction": direction, "travel_method": method, "time_cost": cost,
            "sources": ["chunk:0"], **extra}


GOOD: list[dict[str, Any]] = [
    passage("剑湖宫", "剑湖宫·练武厅", "内部", cost=1, basis="穿过前院便是大厅"),
    passage("剑湖宫·练武厅", "无量宫", "外部", cost=1),  # 别名同样全等落地
    passage("无量山", "剑湖宫", "上", cost=6, basis="上山的路慢些"),
    passage("剑湖宫", "无量山", "下", cost=4),
    passage("无量山", "无量山·谷底", "下", "坠落", 1, sources=["chunk:1"]),
    passage("无量山·谷底", "无量山", "上", "攀援", 16, sources=["chunk:1"]),
    {"type": "sight", "place": "无量山", "landmark": True, "renowned": True, "sources": ["chunk:0"]},
]


def raw(*answers: dict[str, Any]) -> str:
    return "好的：```json\n" + json.dumps(list(answers), ensure_ascii=False) + "\n```"


def ingest(tmp_path: Path, *answers: dict[str, Any], lib: Library | None = None, bp: WorldBlueprint = GEO) -> GeoBook:
    return ingest_geography(bp, raw(*answers), tmp_path / "geography.json", "claude-subagent", lib or library())[0]


def committed() -> WorldBlueprint:
    return WorldBlueprint.model_validate_json((ENGINE / "data" / "world" / "blueprint.json").read_text(encoding="utf-8"))


# ============================================================
#  出口对 —— 无向、每对一次、广度优先
# ============================================================
def test_pairs_walk_breadth_first_from_the_seed_and_then_the_rest_in_blueprint_order() -> None:
    assert pairs(GEO, "剑湖宫·练武厅") == [
        ("loc:剑湖宫·练武厅", "loc:剑湖宫"), ("loc:剑湖宫", "loc:无量山"), ("loc:无量山", "loc:无量山·谷底"),
        ("loc:无量山", "loc:普洱"), ("loc:大理城", "loc:善人渡"),
    ]
    assert pairs(GEO, "loc:善人渡")[0] == ("loc:善人渡", "loc:大理城") and len(pairs(GEO, "无量宫")) == 5
    with pytest.raises(ValueError, match="没有名为「练武厅」"):
        pairs(GEO, "练武厅")  # 地名全等：处所一侧的简称不落地


# ============================================================
#  合格的一批
# ============================================================
def test_a_good_batch_is_cached_applied_and_read_by_ways(tmp_path: Path) -> None:
    fresh = ingest(tmp_path, *GOOD)
    assert len(fresh.passages) == 6 and len(fresh.sights) == 1 and {e.by for e in fresh.passages} == {"claude-subagent"}
    cache = json.loads((tmp_path / "geography.json").read_text(encoding="utf-8"))
    assert cache["version"] == GEO_PROMPT_VERSION and cache["fingerprint"] == geo_fingerprint(GEO)
    noted, lines = canonize_geography(GEO, tmp_path / "geography.json")
    assert noted.sights == (Sight(location_id="loc:无量山", landmark=True, renowned=True, sources=("chunk:0",)),)
    way = ways(noted)
    assert (way[("loc:无量山", "loc:无量山·谷底")].direction, way[("loc:无量山", "loc:无量山·谷底")].travel_method) == (Direction.DOWN, TravelMethod.FALL)
    assert way[("loc:无量山·谷底", "loc:无量山")].time_cost == 16 and way[("loc:无量山", "loc:剑湖宫")].authored
    assert not way[("loc:无量山", "loc:普洱")].authored and way[("loc:无量山", "loc:普洱")].time_cost == 4  # 没注记的照旧推出
    assert lines[0] == "道路注记 6/9 条出口、可见性注记 1 处（地标 1、名胜 1）（provenance 推断）"
    assert "「无量山」→「剑湖宫」上，步行，6 刻（claude-subagent，出处 chunk:0）：上山的路慢些" in lines
    assert lines[-1] == "「无量山」地标、名胜（claude-subagent，出处 chunk:0）" and all("\n" not in ln for ln in lines)


def test_a_one_way_exit_needs_one_answer_and_later_batches_build_on_earlier_ones(tmp_path: Path) -> None:
    ingest(tmp_path, passage("无量山", "普洱", "南", cost=192))  # 普洱那头没有出口：只答一向即可
    ingest(tmp_path, passage("大理城", "善人渡", "东", sources=["chunk:2"]), passage("善人渡", "大理城", "西", sources=["chunk:2"]))
    ingest(tmp_path, passage("善人渡", "大理城", "西", cost=8, sources=["chunk:2"]))  # 另一向已在缓存里：单答一向即覆盖
    book, notes = load_geography(tmp_path / "geography.json", GEO)
    assert notes == [] and [(e.passage.from_id, e.passage.time_cost) for e in book.passages] == [
        ("loc:善人渡", 8), ("loc:大理城", 4), ("loc:无量山", 192)]
    with pytest.raises(ExtractionError, match="往返方位不相反"):
        ingest(tmp_path, passage("善人渡", "大理城", "北", sources=["chunk:2"]))  # 覆盖之后与缓存里的另一向对不上：整批拒收
    assert load_geography(tmp_path / "geography.json", GEO)[0] == book


# ============================================================
#  闸门 —— 有一条不合格，整批拒收，缓存一字不写
# ============================================================
BAD: dict[str, tuple[list[dict[str, Any]], str]] = {
    "地名包含匹配": ([passage("剑湖宫", "练武厅", "内部", cost=1), passage("剑湖宫·练武厅", "剑湖宫", "外部", cost=1)], "没有名为「练武厅」"),
    "不是出口": ([passage("剑湖宫·练武厅", "无量山", "外部")], "不是蓝图里的一条出口"),
    "只答一向": ([passage("剑湖宫", "剑湖宫·练武厅", "内部", cost=1)], "还须答 剑湖宫·练武厅 → 剑湖宫"),
    "不明": ([passage("大理城", "善人渡", "不明", sources=["chunk:2"]), passage("善人渡", "大理城", "不明", sources=["chunk:2"])],
             "不得写「不明」"),
    "往返不相反": ([passage("大理城", "善人渡", "东", sources=["chunk:2"]), passage("善人渡", "大理城", "东", sources=["chunk:2"])],
                  "往返方位不相反"),
    "往返都坠落": ([passage("无量山", "无量山·谷底", "下", "坠落", 1, sources=["chunk:1"]),
                    passage("无量山·谷底", "无量山", "上", "坠落", 1, sources=["chunk:1"])], "往返都是坠落"),
    "耗时为零": ([passage("无量山", "普洱", "南", cost=0)], "time_cost"),
    "耗时超七日": ([passage("无量山", "普洱", "南", cost=673)], "time_cost"),
    "交通方式枚举": ([passage("无量山", "普洱", "南", "飞", 4)], "travel_method"),
    "basis 含数字": ([passage("无量山", "普洱", "南", basis="约莫走3个时辰")], "含英文字母、数字或标记符号：3"),
    "basis 含换行": ([passage("无量山", "普洱", "南", basis="山路\n难行")], "含换行或控制字符"),
    "basis 照抄": ([passage("无量山", "普洱", "南", basis="穿过前院来到练武厅，只见东西二宗弟子分坐")], "与原著共享 ≥16 字"),
    "出处不在切片": ([passage("无量山", "普洱", "南", sources=["chunk:9"])], "chunk:9 不在已组装的切片里"),
    "出处没提到两端": ([passage("无量山", "普洱", "南", sources=["chunk:1"])], "chunk:1 没有提到"),
    "出处格式": ([passage("无量山", "普洱", "南", sources=["第一回"])], "sources"),
    "可见性两项皆假": ([{"type": "sight", "place": "无量山", "sources": ["chunk:0"]}], "既非地标也非名胜"),
    "可见性出处没提到此地": ([{"type": "sight", "place": "大理城", "renowned": True, "sources": ["chunk:0"]}], "没有提到"),
    "一批两答": ([passage("无量山", "普洱", "南"), passage("无量山", "普洱", "西南")], "答了两次"),
    "多余字段": ([passage("无量山", "普洱", "南", note="多写")], "不合契约"),
    "不认得 from_": ([{**{k: v for k, v in passage("无量山", "普洱", "南").items() if k != "from"}, "from_": "无量山"}], "不合契约"),
}


@pytest.mark.parametrize("case", sorted(BAD))
def test_the_gate_rejects_the_whole_batch(tmp_path: Path, case: str) -> None:
    answers, reason = BAD[case]
    with pytest.raises(ExtractionError, match=reason):
        ingest(tmp_path, *GOOD, *answers) if case not in ("一批两答", "只答一向", "地名包含匹配") else ingest(tmp_path, *answers)
    assert not (tmp_path / "geography.json").exists()


def test_without_the_novel_sources_cannot_be_verified(tmp_path: Path) -> None:
    with pytest.raises(ExtractionError, match="无从核验出处"):
        ingest(tmp_path, passage("无量山", "普洱", "南"), lib=library(novel=False))


# ============================================================
#  套用、指纹与缓存
# ============================================================
def _book() -> GeoBook:
    return GeoBook(
        passages=(PassageEntry(passage=Passage(from_id="loc:无量山", to_id="loc:普洱", direction=Direction.SOUTH, time_cost=192,
                                               sources=("chunk:2",)), by="人工"),),
        sights=(SightEntry(sight=Sight(location_id="loc:大理城", renowned=True, sources=("chunk:2",)), by="人工"),),
    )


def test_apply_geography_is_pure_idempotent_and_revalidates() -> None:
    once = apply_geography(GEO, _book())
    assert GEO.passages == () and apply_geography(once, _book()) == once and apply_geography(once, GeoBook()) == GEO
    stray = GeoBook(passages=(PassageEntry(passage=Passage(from_id="loc:普洱", to_id="loc:无量山", direction=Direction.NORTH,
                                                            time_cost=4, sources=("chunk:2",))),))
    with pytest.raises(ValueError, match="不是蓝图里的一条出口"):
        apply_geography(GEO, stray)


def test_fingerprint_follows_names_and_topology_only(tmp_path: Path) -> None:
    relabeled = GEO.model_copy(update={"locations": tuple(
        loc.model_copy(update={"exits": {"进练武厅": "loc:剑湖宫·练武厅", "出大门": "loc:无量山"}, "description": "掌门居所"})
        if loc.name == "剑湖宫" else loc for loc in GEO.locations)})
    assert geo_fingerprint(relabeled) == geo_fingerprint(GEO) == geo_fingerprint(apply_geography(GEO, _book()))
    renamed = GEO.model_copy(update={"locations": tuple(
        loc.model_copy(update={"aliases": ()}) if loc.name == "剑湖宫" else loc for loc in GEO.locations)})
    rewired = GEO.model_copy(update={"locations": tuple(
        loc.model_copy(update={"exits": {**loc.exits, "往山西": "loc:山西"}}) if loc.name == "普洱" else loc for loc in GEO.locations)})
    assert len({geo_fingerprint(GEO), geo_fingerprint(renamed), geo_fingerprint(rewired)}) == 3

    ingest(tmp_path, *GOOD)
    path = tmp_path / "geography.json"
    noted, _ = canonize_geography(GEO, path)
    bare, lines = canonize_geography(WorldBlueprint.model_validate(rewired.model_dump() | {"passages": [
        p.model_dump() for p in noted.passages]}), path)
    assert bare.passages == () and bare.sights == () and "地理缓存出自另一份蓝图" in lines[0]  # 拓扑一变整体作废、注记撤下
    path.write_text("{损坏", encoding="utf-8")
    broken, lines = canonize_geography(noted, path)
    assert broken.passages == () and "已损坏" in lines[0]  # 从不抛错
    with pytest.raises(ExtractionError, match="已损坏"):
        ingest(tmp_path, *GOOD)


def test_audit_lore_and_healing_carry_geography_through_and_lore_ignores_it() -> None:
    noted = apply_geography(GEO, _book())
    assert apply_audit(noted, AuditBook()).passages == noted.passages and apply_audit(noted, AuditBook()).sights == noted.sights
    assert apply_lore(noted, LoreBook()).passages == noted.passages and apply_lore(noted, LoreBook()).sights == noted.sights
    assert apply_placements(noted, [])[0].passages == noted.passages and apply_placements(noted, [])[0].sights == noted.sights
    assert lore_fingerprint(noted) == lore_fingerprint(GEO)


def test_committed_lore_and_audit_caches_stay_valid_for_the_committed_blueprint() -> None:
    """入库的 lore.json 与 audit.json 对入库的蓝图依旧有效：蓝图多了 passages / sights 两个字段，掌故与审计的指纹分毫不动。"""
    bp = committed()
    lore_path, audit_path = ENGINE / "data" / "world" / "lore.json", ENGINE / "data" / "world" / "audit.json"
    book, notes = load_lore(lore_path, bp)
    assert notes == [] and len(book.facts) >= 43 and json.loads(lore_path.read_text(encoding="utf-8"))["fingerprint"] == lore_fingerprint(bp)
    audited, notes = load_audit(audit_path, bp)
    assert notes == [] and len(audited) > 0
    some = GeoBook(sights=(SightEntry(sight=Sight(location_id="loc:无量山", landmark=True, sources=("chunk:0",))),))
    assert lore_fingerprint(apply_geography(bp, some)) == lore_fingerprint(bp)


# ============================================================
#  题面
# ============================================================
def test_export_batches_pairs_with_both_directions_and_lists_every_place(tmp_path: Path) -> None:
    files, todo = geo_export(GEO, library(), GeoBook(), batch=2)
    assert sorted(files) == ["geo-passage-01.txt", "geo-passage-02.txt", "geo-passage-03.txt", "geo-sight-01.txt"] and len(todo) == 5
    first = files["geo-passage-01.txt"]
    assert first.startswith("你是《天龙八部》世界的地理撰写者") and "大理在南、中原在北" in first and '<pairs n="2">' in first
    assert "剑湖宫·练武厅 → 剑湖宫：出口标签「出厅门」；推出的缺省 外部｜步行｜1 刻" in first
    assert "剑湖宫 → 剑湖宫·练武厅：出口标签「入练武厅」；推出的缺省 内部｜步行｜1 刻" in first
    assert "提到两地的原文块：chunk:0" in first
    assert '{"type": "passage", "from": "剑湖宫", "to": "无量山", "direction": "外部", "travel_method": "步行", "time_cost": 4' in first
    second = files["geo-passage-02.txt"]
    assert "无量山 → 无量山·谷底：出口标签「失足坠崖」；推出的缺省 内部｜坠落｜1 刻" in second
    assert "提到两地的原文块：（无）；提到其一的原文块：chunk:0、chunk:1" in second and "普洱 → 无量山" not in second  # 单向出口只出一向
    sight = files["geo-sight-01.txt"]
    assert sight.count("<place ") == len(GEO.locations) and '"type": "sight", "place": "大理城"' in sight
    assert '<place name="山西">\n山西（区域 未载）：未载\n出口：无\n提到此地的原文块：（无）\n无从引出处：不答\n</place>' in sight
    sparse, _ = geo_export(GEO, Library({1: TEXT1}, {}, TEXT1, frozenset()), GeoBook())  # 没有原文块提到的一对：不出模板
    head = sparse["geo-passage-01.txt"].split('<pair n="2"')[0]
    assert "无从引出处：这一对两向都不答，沿用推出的缺省" in head and '"from": "剑湖宫·练武厅"' not in head

    ingest(tmp_path, *GOOD)
    book, _ = load_geography(tmp_path / "geography.json", GEO)
    again, todo = geo_export(GEO, library(), book)
    assert todo == [("loc:无量山", "loc:普洱"), ("loc:大理城", "loc:善人渡")]  # 两向都已答的一对不再出题
    assert "已有注记：地标、名胜" in again["geo-sight-01.txt"]
    full, todo = geo_export(GEO, library(), book, everything=True)
    assert len(todo) == 5 and "已有注记：上｜步行｜6 刻：上山的路慢些" in full["geo-passage-01.txt"]


def test_export_of_the_committed_blueprint_covers_every_exit_exactly_once_with_both_directions_together() -> None:
    bp = committed()
    files, todo = geo_export(bp, Library(), GeoBook())
    exits = {(loc.name, {x.id: x.name for x in bp.locations}[t]) for loc in bp.locations for t in loc.exits.values()}
    assert len(todo) == len({frozenset(e) for e in exits}) and todo[0] == ("loc:剑湖宫·练武厅", "loc:剑湖宫")
    seen: list[tuple[str, str]] = []
    for name, text in sorted(files.items()):
        if not name.startswith("geo-passage-"):
            continue
        batch = [(j["from"], j["to"]) for j in (json.loads(ln) for ln in text.splitlines() if ln.startswith('{"type": "passage"'))]
        assert all((b, a) in batch for a, b in batch if (b, a) in exits)  # 一对两向同批
        seen += batch
    assert len(seen) == len(exits) and set(seen) == exits
    assert sum(name.startswith("geo-passage-") for name in files) == -(-len(todo) // 40)


# ============================================================
#  命令行 —— export → ingest；lore / heal / audit / geo 重建蓝图时注记照缓存补回；全程不装配大模型
# ============================================================
def test_seed_cli_geo_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from app import seed
    from app.config import Settings

    settings = Settings(_env_file=None, llm_provider="gemini", llm_api_key="g", llm_model="pro",  # type: ignore[call-arg]
                        source_text_dir=tmp_path / "no-source", world_dir=tmp_path / "world")
    settings.world_dir.mkdir()
    settings.blueprint_path.write_text(GEO.model_dump_json(), encoding="utf-8")
    (settings.world_dir / "report.txt").write_text("（无异常）", encoding="utf-8")
    monkeypatch.setattr(seed, "get_settings", lambda: settings)
    monkeypatch.setattr(seed, "_library", lambda *_: library())

    def paid(*_: object) -> None:
        raise AssertionError("地理不得装配付费大模型")

    monkeypatch.setattr(seed, "build_llm", paid)

    def blueprint() -> WorldBlueprint:
        return WorldBlueprint.model_validate_json(settings.blueprint_path.read_text(encoding="utf-8"))

    seed.main(["geo", "--export", str(tmp_path / "jobs"), "--batch-pairs", "3"])
    assert "道路 5 对分 2 批" in capsys.readouterr().out
    assert sorted(p.name for p in (tmp_path / "jobs").iterdir()) == ["geo-passage-01.txt", "geo-passage-02.txt", "geo-sight-01.txt"]
    with pytest.raises(SystemExit, match="没有名为「无此地」"):
        seed.main(["geo", "--export", str(tmp_path / "jobs"), "--from", "无此地"])

    (tmp_path / "answer.json").write_text(raw(*GOOD), encoding="utf-8")
    with pytest.raises(SystemExit):
        seed.main(["geo", "--ingest", str(tmp_path / "answer.json")])  # 作答者必须署名
    seed.main(["geo", "--ingest", str(tmp_path / "answer.json"), "--by", "claude-subagent"])
    noted = blueprint()
    assert len(noted.passages) == 6 and len(noted.sights) == 1
    report = (settings.world_dir / "report.txt").read_text(encoding="utf-8")
    assert "[地理] 道路注记 6/9 条出口、可见性注记 1 处（地标 1、名胜 1）（provenance 推断）" in report
    (tmp_path / "bad.json").write_text(raw(passage("无量山", "普洱", "不明")), encoding="utf-8")
    with pytest.raises(SystemExit, match="整批未入缓存"):
        seed.main(["geo", "--ingest", str(tmp_path / "bad.json"), "--by", "claude-subagent"])
    assert blueprint() == noted

    for command in (["lore"], ["heal"], ["audit"], ["geo"]):  # 重建蓝图的命令都按缓存补回地理（自愈 → 审计 → 掌故 → 地理）
        seed.main(command)
        assert blueprint() == noted, command
    bp, sections = seed._canonize(settings, GEO)
    assert bp == noted and list(sections) == ["审计", "掌故", "地理"]

    rewired = GEO.model_copy(update={"locations": tuple(
        loc.model_copy(update={"exits": {"往山西": "loc:山西"}}) if loc.name == "普洱" else loc for loc in GEO.locations)})
    settings.blueprint_path.write_text(rewired.model_dump_json(), encoding="utf-8")
    seed.main(["geo"])  # 出口拓扑变了：地理缓存整体作废，注记撤下
    assert blueprint().passages == () and "[地理] 地理缓存出自另一份蓝图" in (settings.world_dir / "report.txt").read_text(encoding="utf-8")
