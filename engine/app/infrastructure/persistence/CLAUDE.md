# persistence/
> L2 | 父级: engine/app/infrastructure/CLAUDE.md

持久化适配：每个端口一份生产实现 + 一份内存实现，两份共跑同一套契约测试（tests/test_event_store.py、tests/test_world_graph.py）。
事件账本是真相；图谱与向量记忆是投影——它们可以丢、可以落后、可以整体重建，事件流不行。

成员清单
memory_event_store.py: InMemoryEventStore，进程内字典 + asyncio 锁；原子追加、乐观并发、版本从 1 连续；事件经 JSON 往返（decode_event，与生产实现同一个读出入口）后入账，与 JSONB 实现同样拒收不可序列化的事件
postgres_event_store.py: PostgresEventStore（asyncpg 连接池），domain_events 表以 JSONB 存事件、(stream_id, version) 唯一、event_id 唯一；触发器拒绝 UPDATE / DELETE / TRUNCATE（历史在数据库层面不可篡改）；读出一律经 decode_event，库里的旧词汇被上抛为现行事件而字节不动；追加走流级咨询锁 → 事务内核对版本 → unnest 批量插入，唯一约束兜底映射为 ConcurrencyError；建表用全局咨询锁串行化
memory_graph.py: InMemoryWorldGraph（WorldReader + WorldProjector + WorldSeeder），正典是蓝图索引，覆盖层直接复用领域的 evolve 折叠出 PlayerState（投影与聚合根同构，熟练度 / 悟性 / 气血原样进快照）；快照只取所在地、在场的健在者（带称号）、持有者落在本地 / 在场者 / 玩家身上的物品（下落不明者没有持有者，不进任何快照）、玩家所会 ∪ 在场者所会 ∪ 行囊典籍（获取要求所载）可得的武学
neo4j_graph.py: Neo4jWorldGraph（AsyncDriver），正典节点与硬性边只读；覆盖层 = (:Player {name, alive, version, aptitude, hp}) + LOCATED_IN / KNOWS_SKILL {proficiency} / SUBDUED + (:Character)-[:REGARDS {attitude}]->(:Player) + (:Item)-[:HELD_BY {world}]->(持有者)；SkillPracticed 在 KNOWS_SKILL 上累加熟练度、HealthChanged 以 CASE 钳位气血，与 evolve 逐字段同构；物品此刻的持有者 = 本世界的 HELD_BY 否则正典 canon_holder；每种事件一个投影函数，单个写事务内推进检查点并跳过已投影版本；快照用 COLLECT 子查询与 CALL () {} 作用域子句（须 Neo4j 5.23+）在一个读事务内取齐，武学的获取 / 修炼要求以 JSON 属性读回；forget 删除该世界的 HELD_BY 与 Player 节点；非 reset 播种后用 stale_canon 检查不属于本蓝图的正典节点并告警（MERGE 只增不删，换蓝图须 reset）
qdrant_memory.py: Embedder 抽象与 HashingEmbedder（字符一元 + 二元组的符号哈希投影，384 维，零依赖、确定性）；QdrantNarrativeMemory 以 uuid5(玩家:版本) 为点 id 幂等 upsert，召回按 player_id 过滤且只取 before_version 之前；url=":memory:" 走 qdrant-client 本地模式（不建载荷索引以免告警）
__init__.py: 包标识

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
