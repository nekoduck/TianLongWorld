# infrastructure/
> L2 | 父级: engine/app/CLAUDE.md

基础设施层：实现 domain 与 application 拥有的端口，并承载 World Seeding 管道。这里是唯一允许出现 asyncpg / neo4j / qdrant-client / HTTP 的地方。
播种分三段且彼此解耦：大模型读书抽取（非确定）→ 组装器定案（确定）→ Cypher 编译（确定）；中间表示是 WorldBlueprint，可落盘审阅后再写图。

成员清单
knowledge_extractor.py: 原著解析管道前半程——load_corpus（*.txt，UTF-8 → GB18030 回退）、chunk_text（回目硬边界、段落软边界、超长段落硬切）、名称级抽取契约 ChunkExtraction（Raw* 记录，枚举宽容退回缺省，认不出的关系类别留空不猜）、EXTRACTION_SYSTEM 抽取铁律（只抽本段明写之事）、LLMKnowledgeExtractor（结构化输出、截取 JSON、重采样、按提示词版本 + 文本哈希的磁盘缓存）、SeedingPipeline（信号量限流并发、gather 保序、单块失败入报告不拖垮全书、全部失败才中止）与 SeedingResult（蓝图 + 报告 + 语句 + 脚本）
blueprint_assembler.py: 原著解析管道后半程——正名互见才合并（泛称撞泛称不合并，并入者的正名成为别名）、标量首次登场即开篇、列表取并集、_Index 别名只在同类无歧义时收录、_land 名称落地；出口 / 人物所在 / 人物武学 / 关系落不了地即丢弃，物品无处安放即不存在，武学前置落不了地即封存、成环即封存并断环，自悟未载典籍改为师传，道路补全为双向；AssemblyReport 记丢弃 / 封存 / 失败块
cypher.py: 蓝图 → 参数化批量语句（约束与索引 → 节点 → 门派 → 硬性边 CONNECTS_TO / LOCATED_IN / BELONGS_TO / KNOWS_SKILL / HAS_RELATION → 前置拓扑 REQUIRES {as: skill|item|place} / CONFLICTS_WITH），每批至多 500 行，全部 MERGE 幂等；原著里的名字只走 $rows 参数；render_script 把同一批语句的参数序列化为 Cypher 字面量写成 seed.cypher（同源）；Item.canon_holder 与 HELD_BY.world 建索引支撑快照查询
llm/: 大模型厂商适配（完成 + 流式），地图见 llm/CLAUDE.md
persistence/: 事件账本、图谱、长线记忆的生产实现与内存实现，地图见 persistence/CLAUDE.md
__init__.py: 包标识

依赖说明
- blueprint_assembler 只在类型标注里引用 knowledge_extractor 的 ChunkExtraction（TYPE_CHECKING），运行期由后者导入前者，不成环
- 播种的时间切片是"每个实体首次登场时的状态"：全书抽取会让人物身负后文才学会的武学，用 --max-chunks 只取开篇可得到更忠实的开局

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
