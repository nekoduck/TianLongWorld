"""
[INPUT]: 依赖 app.schemas 的 PlayerState
[OUTPUT]: 对外提供 SHICHEN（十二时辰）、Grandmaster / GRANDMASTERS（绝顶高手名录）、kin()（同一人物的全部称呼）、
          MASTER_ARTS（绝学名录）、OpeningSeed / OPENING_SEEDS（开局种子）
[POS]: app 的静态世界设定库，只有不可变数据与查表，没有状态；被 engine（时辰校验、绝学防线）、director（致死预判、JIT 别名、开局）
       与 llm/mock.py 共同消费——放在 app 顶层而非 director 内，是为了让 engine 依赖它时不与 director 形成导入环
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from dataclasses import dataclass

from app.schemas import PlayerState

SHICHEN = ("子时", "丑时", "寅时", "卯时", "辰时", "巳时", "午时", "未时", "申时", "酉时", "戌时", "亥时")


# ============================================================
#  绝顶高手 —— 无名小卒冒犯即死的存在
# ============================================================
@dataclass(frozen=True)
class Grandmaster:
    name: str
    aliases: tuple[str, ...]  # 场景与玩家可能使用的一切称呼
    signature: str  # 杀招：必死裁决的叙事素材

    def mentioned_in(self, text: str) -> bool:
        return any(alias in text for alias in self.aliases)


# 只收录"会对冒犯者下死手"或"随手反震即可致死"之人；虚竹、段誉之流心慈手软，不入此录
GRANDMASTERS: tuple[Grandmaster, ...] = (
    Grandmaster("萧峰", ("萧峰", "乔峰", "乔帮主", "萧大王", "北乔峰"), "降龙十八掌"),
    Grandmaster("慕容复", ("慕容复", "慕容公子", "南慕容"), "斗转星移"),
    Grandmaster("段延庆", ("段延庆", "恶贯满盈", "延庆太子"), "一阳指"),
    Grandmaster("鸠摩智", ("鸠摩智", "大轮明王", "吐蕃国师"), "火焰刀"),
    Grandmaster("丁春秋", ("丁春秋", "星宿老怪", "星宿老仙"), "化功大法"),
    Grandmaster("天山童姥", ("天山童姥", "童姥", "巫行云"), "天山六阳掌"),
    Grandmaster("李秋水", ("李秋水",), "小无相功"),
    Grandmaster("萧远山", ("萧远山",), "般若掌"),
    Grandmaster("慕容博", ("慕容博",), "参合指"),
    Grandmaster("扫地僧", ("扫地僧", "扫地老僧", "扫地的老僧"), "反震之力"),
    Grandmaster("叶二娘", ("叶二娘", "无恶不作"), "淬毒薄刀"),
    Grandmaster("南海鳄神", ("南海鳄神", "岳老三", "岳老二", "凶神恶煞"), "鳄嘴剪"),
    Grandmaster("云中鹤", ("云中鹤", "穷凶极恶"), "钢抓"),
)


def kin(name: str) -> tuple[str, ...]:
    """同一人物的全部称呼：名录中的高手展开为全部别名，其余人物原样返回。供 JIT 检索把「乔峰」与「萧峰」视作同一人。"""
    for master in GRANDMASTERS:
        if name in master.aliases or name == master.name:
            return tuple(dict.fromkeys((master.name, *master.aliases)))
    return (name,)


# 身负这些绝学者已非无名小卒，致死预判交还大模型裁量；它们只能来自开局设定，大模型无权在推演中授予
MASTER_ARTS: tuple[str, ...] = (
    "北冥神功", "六脉神剑", "降龙十八掌", "易筋经", "小无相功", "天山六阳掌",
    "斗转星移", "一阳指", "凌波微步", "化功大法", "天山折梅手", "火焰刀",
)


# ============================================================
#  开局种子 —— 真实大模型以之为蓝本铺陈，Mock 与兜底直接以之为开场白
# ============================================================
@dataclass(frozen=True)
class OpeningSeed:
    player: PlayerState
    premise: str
    present: tuple[str, ...]  # 开场白里点到的绝顶高手：兜底开局与 Mock 据此登记局部环境


def _seed(
    location: str, time: str, weather: str, buffs: tuple[str, ...], inventory: tuple[str, ...], premise: str
) -> OpeningSeed:
    # 开局一律是健康的无名小卒：无身份、无武学
    player = PlayerState(
        location=location, time=time, weather=weather, health_status="健康", buffs_debuffs=buffs, inventory=inventory
    )
    present = tuple(m.name for m in GRANDMASTERS if m.mentioned_in(premise))
    return OpeningSeed(player, premise, present)


OPENING_SEEDS: tuple[OpeningSeed, ...] = (
    _seed(
        "无锡松鹤楼", "午时", "晴", ("饥饿",), ("三枚铜钱",),
        "你在松鹤楼角落里醒来，怀中只剩三枚铜钱。楼上酒香四溢，邻桌一条魁梧大汉独据一桌，面前已摆了十几只空酒碗，"
        "旁人压低声音议论——那便是丐帮帮主乔峰。楼梯口，一位青衫书生正拾级而上，目光在大汉身上停了一停。",
    ),
    _seed(
        "太湖畔", "子时", "微雨", ("风寒",), (),
        "冷雨打在太湖的芦苇上，你蜷在一条破船的篷下，浑身发冷。远处水面亮起一盏菱灯，一叶小舟悠悠划来，"
        "船头少女用吴侬软语哼着采菱曲。岸边柳树后，却分明伏着两个黑衣人，手按刀柄，死死盯着那盏灯。",
    ),
    _seed(
        "大理无量山", "辰时", "薄雾", ("腿脚酸软",), ("柴刀",),
        "你砍柴迷了路，误闯无量山剑湖宫。山雾里金铁交鸣，无量剑东西两宗正在比剑，围观者屏息凝神。"
        "一个书生模样的青年忽然失笑出声，满场目光齐刷刷射了过来——而他身边，恰好站着你。",
    ),
    _seed(
        "少林寺山门外", "酉时", "落雪", ("饥寒交迫",), (),
        "大雪封山，你缩在少林寺山门的石狮后避风。暮鼓声里，一位须发皆白、身形佝偻的扫地老僧拿着扫帚，"
        "慢慢扫着台阶上的积雪，扫过之处，雪竟一片也不再落下。他似乎没有看见你，又似乎早就看见了。",
    ),
    _seed(
        "雁门关外", "申时", "风沙", ("口干舌燥",), ("破弓",),
        "黄沙漫天，你随一支商队行至雁门关外。乱石谷口立着一块巨石，石上刀痕斑驳，似是新刻。"
        "商队老把式脸色煞白，催大家快走——谷中马蹄声如闷雷滚来，一队契丹骑兵疾驰而至，为首者披着狼皮大氅。",
    ),
    _seed(
        "星宿海", "未时", "酷热", ("脱水",), (),
        "烈日炙烤着星宿海边的盐碱滩，你被一群星宿派弟子拦住了去路。他们敲锣打鼓，齐声高唱“星宿老仙，法驾中原”，"
        "簇拥着一位鹤发童颜、手摇羽扇的老者——丁春秋。一名弟子斜眼睨你：“还不跪下颂扬老仙？”",
    ),
)
