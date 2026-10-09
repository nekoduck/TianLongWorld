# briefs/
> L2 | 父级: engine/app/application/CLAUDE.md

地下城主的简报包：三路赌注（出手 / 交涉 / 暗中）各一份 XML 简报、一段铁律、一份只列可裁结局的 schema。
简报是地下城主唯一的世界——只用快照里的 T=0 事实：foreshadow 从不进快照也就从不进简报，玩家尚不知道的见闻正文同样不进；每个插值逐值转义。
它只服务自由文本回合（AdjudicationSlot 把点选交给气运），产出永远只是提议，定案归 domain/stakes.settle_any。

成员清单
__init__.py: 门面——brief() / schema() 按 stakes.route_of 分派，Section / SECTIONS 是每路一段铁律（裁什么、几条规矩、一个示例，由 resolution_agent 套进共用框架），outcome_schema 交涉与暗中共用 {outcome, narrative_hint}；转出 combat_brief / social_brief / covert_brief / verdict_schema / MEANING
combat.py: 「战」，v3 战况简报、verdict_schema、MEANING 从 resolution_agent 原样挪来（经 2026-10 真实 Gemini 选型实测定稿，实质不动）；另持有三份简报共用的笔墨工具 safe（逐值转义）与 join（顿号连缀）
social.py: 「交」，对方的本名称号别名、门派、境界、性情、对你的态度与恩怨缘由、外显人设（好恶心事）、是否被制住、T=0 描述；你的境界（已按火候折算）与伤势；所图、手段（威逼 / 套话另有说法）、标的名、筹码（已知见闻正文 / 靠山）、玩家原话、可裁结局及含义——如愿随所图而变，松动升不升人情直接问 domain/social.effects；打探的标的只写「此人所知的一桩事」
covert.py: 「暗」，失主（本名称号、门派、境界、性情、态度与恩怨、是否被制住、T=0 描述）、物（名字、种类、描述，不写险性）、手段（骗取 / 偷取）、境界差与情势（被制住 / 正提防你 / 信你）、可裁结局及含义

依赖说明
- 包内单向：combat ← social / covert ← __init__；resolution_agent 只认门面
- 只读 domain：stakes（AnyStakes / route_of）、combat / social / covert 的赌注与结局、rules.player_tier、snapshot；不写任何东西

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
