# domain/
> L2 | 父级: engine/app/CLAUDE.md

领域层：世界是什么（本体）、发生过什么（事件）、此刻怎样（聚合根折叠）、能做什么（裁决规则）、要什么样的存储（端口）。
纯 Python + Pydantic，不做 IO、不调大模型、不看时钟；唯一的外部依赖是共享内核 app/errors.py。

成员清单
models.py: 世界本体——实体 id 自带种类前缀（loc / chr / art / itm / ply）；Tier 是只比高下的有序等级；Location（出入口映射 exits）；Character 三名分立：true_name 本名即主键（id 恒为 chr:{本名}，蓝图闸门拒收以称号篡位的主键）、titles 江湖称号、aliases 化名旧称，name / names 是派生属性；MartialArt 两道门：Acquisition 获取要求（师传 / 自悟、典籍、得门径之地、sealed 失传封存）与 Practice 修炼要求（作根基的武学、最低境界、相冲武学）；Item 至多一位物主、至多一个所在，两者皆空即下落不明（lost，孤儿，待播种期自愈），provenance 标明安放出自原著还是推断；WorldBlueprint 是播种中间表示与最后闸门（重复 id、悬空引用、根基成环、主键不是本名一律拒收），prerequisite_cycle 三色 DFS 供组装器复用
progression.py: 渐进式状态的两把尺——Mastery 火候（熟练度 × 悟性系数 → 初窥门径 / 略有小成 / 融会贯通 / 炉火纯青 / 登峰造极，effective_tier 火候不到境界打折，FOUNDATION 作根基须略有小成）、Guidance 修习方式与 GAIN 每次所得、aptitude_for 根骨天定的悟性；Vitality 伤势（MAX_HP 气血、vitality 分档、REST_GAIN 调息所得）；内部整数、对外语义，事件只记"练了多少"，"练到哪一步"永远现算；火候封顶（初窥门径最高三流、略有小成最高二流、融会贯通最高一流），gain() 让一次修习所得随武学境界折算（三流 ×1、二流 ÷1.5、一流 ÷2、绝顶 ÷3）——实测捡到绝顶秘籍读两回就成一流高手，此后要练十来回
combat.py: 模糊裁决的硬轨——CombatOutcome（得手 / 相持 / 轻伤 / 重伤 / 毙命）与 HP_BANDS 气血区间；assess 依境界差、性情、伤势圈出可裁区间 Stakes（admissible + canonical，境界差 + 伤势加码 ≥ 3 且对方狠辣才算极端找死，毙命才入区间）；settle 把地下城主的 CombatProposal 钳进区间与气血带（出界取确定性裁决、非毙命至少留一口气），CombatRuling.adopted 标明速写是否作废
intent.py: 命令语言 ActionType（MOVE / OBSERVE / TALK / ATTACK / TAKE / GIVE / LEARN / REST / INVALID）与 PlayerIntent（action_type / target_entity / item_used / skill_used / narrative_style / reason）；LEARN 一个动作涵盖入门与精进；指称超长或空串时规整而非拒收；放在 domain 而非 application，因为裁决规则消费它，领域不得反向依赖应用层
events.py: 十种不可变领域事件（PlayerSpawned 含悟性 / Moved（fleeing 标明夺路而逃，旧账缺省为自行离去）/ ItemTransferred / SkillPracticed 熟练度所得 / SkillExecuted / HealthChanged 气血涨落 / Conversed / RelationChanged / ActionFailed / PlayerDied）；AnyEvent 按 type 判别、EVENT_ADAPTER 负责编码；decode_event 是账本唯一的读出入口，先经 UPCASTERS 把旧词汇上抛（SkillLearned → 一次融会贯通的 SkillPracticed、「受挫」→「轻伤」）再校验，账本字节一字不改；EventEnvelope 携带流内版本与记录时间，事件本体不含时间以保持裁决确定性
aggregates.py: PlayerState（位置、来路 came_from、逃离过的险地 fled_from、悟性、气血、生死、practice 熟练度之和、item_holders 物品易手覆盖、subdued 制住之人、attitudes 人情、attitude_causes 人情的缘由（最近一次 RelationChanged.cause）、focus 焦点与 focus_fresh（上一个主动作是否正与 focus[0] 打交道：自行离去、调息、闭门苦练之后即不新鲜，交手余波与碰壁不算）（近来亲手打过交道的人与物，新者在前、至多 4 个：出手的对手、攀谈的对方、易手另一端的人与那件东西、修习所凭的师父或典籍；人情涟漪的目睹者、驳回的原话、去过的地方都不算）；skills / inventory / mastery / vitality 皆派生而非另存）、evolve 纯函数折叠（熟练度与气血只做加法，气血钳位，归零不等于死）、Player 聚合根（apply 吸收事实 / from_history 校验版本连续地重放 / replay 即 reduce / spawn 定下悟性 / ensure_alive 永久死亡之门 / mastery 武学等级 / decide 携地下城主的提议委托裁决）；一位玩家的平行世界就是这一个聚合的一致性边界
snapshot.py: 局部真理快照 LocalSnapshot（位置、出路、在场者及其称号 / 所会 / 羁绊、可见之物及持有者与物主、此情此景可知的武学及其获取 / 修炼要求、玩家的熟练度 / 悟性 / 气血原始值与现算的 mastery / vitality、labels 名称表）；集合字段在构造时按固定键排序，任何图谱实现得到逐字段相等的值；referenced_ids 列出 labels 必须覆盖的全部 id
rules.py: 裁决核心（Decider）——resolve 名称落地（精确优先、包含须唯一、单字不猜，称号同样落得到本名）；skill_tier / player_tier / best_skill 按火候折算境界；九条 Rule 各管一种动作的 adjudicate（指称落地 + 物理 / 逻辑校验）、stakes 钩子（只有出手有赌注：可裁区间）与 consequences（定案 → 事件，出手落为 SkillExecuted + HealthChanged [+ PlayerDied]）；攻击的人情涟漪沿 HAS_RELATION 一跳、只波及在场目睹者（「敌人之敌 → 好感」暂停：蓝图的仇敌边多是后文的恩怨，P1 reputation 按 era=开篇 恢复）；物归原主生好感；修习未入门走获取要求（失传 → 根基 → 典籍 → 地点 → 传承）、已入门走精进（化境 → 根基 → 名师点拨 / 参照典籍 / 闭门苦练），根基逐条核验作根基之功的火候、相冲、境界与伤势，点名的师父不肯即拒绝而不悄悄改成苦练，拒绝理由照实写人情（敌视者写明结怨缘由、漠然者是素无交情）；调息须有伤，调息与修习都须无敌视者在侧（UNSAFE，修习的这一道门在其余门槛之后）；stakes / decide 门面——decide 把驳回落为 ActionFailed、把提议经 settle 定案；定案为重伤时追加沿来路（PlayerState.came_from）退回的 Moved（fleeing），重伤逃脱是真的逃，逃离过的险地（fled_from）永不作退路——仇人不会挪窝，连败两场不能被送回第一场的仇家面前；此情此景里没有的武功名只当笔墨，照常以看家本领出手，此地确有而你不会才驳回
ports.py: 端口——EventStore（原子追加 + 乐观并发 / 按版本读）、WorldReader（局部快照 / 出生点 / 名称表）、WorldProjector（幂等投影 / 检查点 / 抹去覆盖层）、WorldSeeder（播种 / 是否已播种）、NarrativeMemory（以玩家 + 版本为主键幂等写入 / 按玩家与版本上限召回）与 MemoryRecord；图谱端口按读、投影、播种拆分（接口隔离）
__init__.py: 包标识

依赖说明
- rules.py 只在类型标注里引用 aggregates.PlayerState（TYPE_CHECKING），aggregates 运行期导入 rules：两者互引而不成环
- 裁决只看两样东西：快照（他人与地理的物理事实）与玩家状态（自身的逻辑事实）；回合编排保证二者版本一致后才调用
- 依赖方向：models ← progression ← combat ← events ← aggregates / rules；combat 与 progression 都是纯函数，聚合根、快照与规则共用同一套折算

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
