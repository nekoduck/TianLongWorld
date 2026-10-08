"""
[INPUT]: 依赖 app.domain.models 的本体类型
[OUTPUT]: 对外提供 WORLD —— 测试用的微型原著蓝图（四地、七人、七门武学、四件物品、三条关系）
[POS]: tests 的世界夹具。它是测试替身而非引擎数据：生产世界只能由原著解析管道产出。
       设计成一条可走通的"逻辑死线"：攻左子穆得辛双清好感 → 拜师无量剑法；拾玉佩、下崖取卷轴、自悟北冥神功；
       制住左子穆夺剑；物归原主得段正淳信任 → 学一阳指；六脉神剑无人可教——天降神兵此路不通
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from app.domain.models import (
    Character,
    CharacterRelation,
    CharacterStatus,
    Disposition,
    Item,
    Location,
    MartialArt,
    Prerequisites,
    RelationKind,
    Tier,
    Transmission,
    WorldBlueprint,
)

WORLD = WorldBlueprint(
    locations=(
        Location(id="loc:大理城", name="大理城", region="大理", description="苍山洱海之间的大理国都",
                 exits={"北上": "loc:无量山", "东去": "loc:无锡城"}),
        Location(id="loc:无量山", name="无量山", region="大理", description="剑湖宫外，东西二宗比剑之地",
                 exits={"南下": "loc:大理城", "崖下": "loc:无量玉洞"}),
        Location(id="loc:无量玉洞", name="无量玉洞", aliases=("琅嬛福地",), region="大理",
                 description="崖底石洞，玉像如生", exits={"攀上": "loc:无量山"}),
        Location(id="loc:无锡城", name="无锡城", region="江南", description="松鹤楼上酒香四溢",
                 exits={"西归": "loc:大理城"}),
    ),
    characters=(
        Character(id="chr:段誉", name="段誉", aliases=("段公子",), faction="大理段氏", tier=Tier.NONE,
                  disposition=Disposition.MERCIFUL, location_id="loc:大理城"),
        Character(id="chr:段正淳", name="段正淳", aliases=("镇南王", "段王爷"), faction="大理段氏", tier=Tier.FIRST,
                  disposition=Disposition.MERCIFUL, location_id="loc:大理城", skills=("art:一阳指",)),
        Character(id="chr:左子穆", name="左子穆", faction="无量剑东宗", tier=Tier.THIRD,
                  location_id="loc:无量山", skills=("art:无量剑法",)),
        Character(id="chr:辛双清", name="辛双清", faction="无量剑西宗", tier=Tier.THIRD,
                  location_id="loc:无量山", skills=("art:无量剑法",)),
        Character(id="chr:南海鳄神", name="南海鳄神", aliases=("岳老三",), faction="四大恶人", tier=Tier.FIRST,
                  disposition=Disposition.RUTHLESS, location_id="loc:无量山"),
        Character(id="chr:乔峰", name="乔峰", aliases=("乔帮主",), faction="丐帮", tier=Tier.PEERLESS,
                  location_id="loc:无锡城", skills=("art:降龙十八掌",)),
        Character(id="chr:汪剑通", name="汪剑通", faction="丐帮", tier=Tier.FIRST, status=CharacterStatus.DECEASED,
                  location_id="loc:无锡城"),
    ),
    martial_arts=(
        MartialArt(id="art:一阳指", name="一阳指", faction="大理段氏", kind="指法", tier=Tier.FIRST,
                   prerequisites=Prerequisites(min_tier=Tier.SECOND)),
        MartialArt(id="art:无量剑法", name="无量剑法", faction="无量剑", kind="剑法", tier=Tier.THIRD),
        MartialArt(id="art:北冥神功", name="北冥神功", kind="内功", tier=Tier.FIRST,
                   prerequisites=Prerequisites(items=("itm:北冥神功卷轴",), location_id="loc:无量玉洞",
                                               conflicts=("art:化功大法",), transmission=Transmission.SELF)),
        MartialArt(id="art:凌波微步", name="凌波微步", kind="身法", tier=Tier.SECOND,
                   prerequisites=Prerequisites(skills=("art:北冥神功",), items=("itm:北冥神功卷轴",),
                                               location_id="loc:无量玉洞", transmission=Transmission.SELF)),
        MartialArt(id="art:降龙十八掌", name="降龙十八掌", faction="丐帮", kind="掌法", tier=Tier.PEERLESS,
                   prerequisites=Prerequisites(min_tier=Tier.FIRST)),
        MartialArt(id="art:化功大法", name="化功大法", faction="星宿派", kind="内功", tier=Tier.FIRST),
        MartialArt(id="art:六脉神剑", name="六脉神剑", faction="大理段氏", kind="剑气", tier=Tier.PEERLESS,
                   prerequisites=Prerequisites(skills=("art:一阳指",), min_tier=Tier.FIRST)),
    ),
    items=(
        Item(id="itm:北冥神功卷轴", name="北冥神功卷轴", aliases=("卷轴",), kind="秘籍", location_id="loc:无量玉洞"),
        Item(id="itm:无量剑", name="无量剑", kind="兵器", owner_id="chr:左子穆"),
        Item(id="itm:玉佩", name="玉佩", aliases=("段家玉佩",), kind="信物", owner_id="chr:段正淳",
             location_id="loc:无量山"),
        Item(id="itm:打狗棒", name="打狗棒", kind="兵器", owner_id="chr:乔峰"),
    ),
    relations=(
        CharacterRelation(source_id="chr:段誉", target_id="chr:段正淳", kind=RelationKind.KIN, note="父子"),
        CharacterRelation(source_id="chr:左子穆", target_id="chr:辛双清", kind=RelationKind.ENEMY, note="东西宗相争"),
        CharacterRelation(source_id="chr:汪剑通", target_id="chr:乔峰", kind=RelationKind.MENTOR, note="传位"),
    ),
)
