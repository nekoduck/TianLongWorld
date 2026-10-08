# domain/
> L2 | 父级: engine/app/CLAUDE.md

领域层：世界是什么（本体）、发生过什么（事件）、此刻怎样（聚合根折叠）、能做什么（裁决规则）、要什么样的存储（端口）。
纯 Python + Pydantic，不做 IO、不调大模型、不看时钟；唯一的外部依赖是共享内核 app/errors.py。

成员清单
models.py: 世界本体——实体 id 自带种类前缀（loc / chr / art / itm / ply）；Tier 是只比高下的有序等级而非数值；Location（出入口映射 exits）、Character（阵营 / 状态 / 境界 / 性情 / 开篇所在 / 所会）、MartialArt（Prerequisites 前置依赖字典：前置武学、必备典籍、修习地点、最低境界、相冲武学、师传 / 自悟、sealed 失传封存）、Item（至多一位物主 + 恰一个物理所在，canon_holder 派生）、CharacterRelation；WorldBlueprint 是播种中间表示与最后闸门（重复 id、悬空引用、前置成环一律拒收），prerequisite_cycle 三色 DFS 供组装器复用
intent.py: 命令语言 ActionType（MOVE / OBSERVE / TALK / ATTACK / TAKE / GIVE / LEARN / INVALID）与 PlayerIntent（action_type / target_entity / item_used / skill_used / narrative_style / reason）；指称超长或空串时规整而非拒收；放在 domain 而非 application，因为裁决规则消费它，领域不得反向依赖应用层
events.py: 九种不可变领域事件（PlayerSpawned / Moved / ItemTransferred / SkillLearned / SkillExecuted / Conversed / RelationChanged / ActionFailed / PlayerDied）+ CombatOutcome；AnyEvent 按 type 判别、EVENT_ADAPTER 负责 JSONB 编解码；EventEnvelope 携带流内版本（乐观并发 / 检查点 / 记忆主键）与记录时间，事件本体不含时间以保持裁决确定性
aggregates.py: PlayerState（位置、生死、所学、item_holders 物品易手覆盖、subdued 制住之人、attitudes 人情；inventory 由 item_holders 派生而非另存）、evolve 纯函数折叠、Player 聚合根（apply 吸收事实 / from_history 校验版本连续地重放 / replay 即 reduce / spawn / ensure_alive 永久死亡之门 / decide 委托裁决）；一位玩家的平行世界就是这一个聚合的一致性边界
snapshot.py: 局部真理快照 LocalSnapshot（位置、出路、在场者及其所会与羁绊、可见之物及持有者与物主、此情此景可知的武学、labels 名称表）；集合字段在构造时按固定键排序，任何图谱实现得到逐字段相等的值；referenced_ids 列出 labels 必须覆盖的全部 id
rules.py: 裁决核心（Decider）——resolve 名称落地（精确优先、包含须唯一、单字不猜）；duel 以境界差与性情定胜负（低一档：狠辣取命、余者击退；低两档以上：仁厚留手、余者毙命）；八条 Rule 各管一种动作的 adjudicate（指称落地 + 物理 / 逻辑校验）与 consequences（绝对结果 → 事件）；攻击的人情涟漪沿 HAS_RELATION 一跳、只波及在场目睹者；物归原主生好感；修习门槛按固定顺序逐条核验，师传时玩家点名的师父优先（拒绝你的是你求的那个人）；decide 把驳回落为 ActionFailed
ports.py: 端口——EventStore（原子追加 + 乐观并发 / 按版本读）、WorldReader（局部快照 / 出生点 / 名称表）、WorldProjector（幂等投影 / 检查点 / 抹去覆盖层）、WorldSeeder（播种 / 是否已播种）、NarrativeMemory（以玩家 + 版本为主键幂等写入 / 按玩家与版本上限召回）与 MemoryRecord；图谱端口按读、投影、播种拆分（接口隔离）
__init__.py: 包标识

依赖说明
- rules.py 只在类型标注里引用 aggregates.PlayerState（TYPE_CHECKING），aggregates 运行期导入 rules：两者互引而不成环
- 裁决只看两样东西：快照（他人与地理的物理事实）与玩家状态（自身的逻辑事实）；回合编排保证二者版本一致后才调用

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
