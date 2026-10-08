# infrastructure/
> L2 | 父级: engine/app/CLAUDE.md

基础设施层：实现 domain 与 application 拥有的端口，并承载 World Seeding 管道。这里是唯一允许出现 asyncpg / neo4j / qdrant-client / HTTP 的地方。
播种分四段且彼此解耦：大模型读书抽取（非确定）→ 组装器定案（确定）→ 图谱自愈（据常识安放孤儿，经闸门、标明推断）→ Cypher 编译（确定）；
中间表示 WorldBlueprint 是正典，Neo4j 与内存图谱都只是它的投影——一切修补（含自愈）都作用于蓝图，再经 seeder 写图，绝不直接改图。

成员清单
knowledge_extractor.py: 原著解析管道前半程——load_corpus（*.txt，UTF-8 → GB18030 回退）、clean_text（剥水印、去不含汉字的行、只留首回到「全书完」之间的正文）、chunk_text（回目硬边界——「第一回」与新修版「一 青衫磊落险峰行」两种写法、段落软边界、超长段落硬切）、名称级抽取契约 v6 ChunkExtraction（Raw* 记录，枚举宽容；人物境界 / 性情 / 生死与武学境界"看不出"即 None——未知不等于最弱；人物三名分立 name 本名 / titles 称号 / aliases 化名旧称，只知称号者 name_is_title；武学两道门 acquisition 获取要求（师传 / 自悟、入门之物、得门径之地）与 practice 修炼要求（根基、境界、相冲），v4 / v5 缓存的 prerequisites 读入即升级；events 记 T=0 之后的习得武学 / 得到物品 / 身故，是证据不是状态）、T0_ANCHOR 时间锚点（原著开篇、段誉刚离家出走之时，与自愈代理共用）、EXTRACTION_SYSTEM 抽取铁律（v6 立骨：时间锚点凌驾一切——状态字段一律写 T=0，开篇之后的事只进 events；只抽本段明写之事；泛称不抽；地名独立可认、处所写「上级·处所」；块内自洽含事件两端；自悟须载明典籍；描述与转述不超过 40 字且不得照抄）、escape_markup（标签内插值转义，自愈题面同用）、LLMKnowledgeExtractor（结构化输出、截取 JSON、输出不合契约与厂商偶发失败一并退避重试而欠费鉴权一次即止、与本块原文共享 ≥16 字的描述与事件转述在写缓存前清空、cache_path 按提示词版本分目录 + 文本哈希的磁盘缓存）、CachedExtractor（只读缓存零费用重组装，缺块即失败，旧版记录读入时补做防抄清洗）、chunk_message / parse_extraction / store_extraction（外部抽取器的产出入缓存与大模型同一道闸门：截取 JSON → 契约校验 → 防抄清洗 → 写入当前版本缓存）、SeedingPipeline（信号量限流并发、逐块进度日志、gather 保序、单块失败入报告不拖垮全书、全部失败才中止）与 SeedingResult（蓝图 + 报告 + 语句 + 脚本）；抽取铁律 v7 据真实 Gemini 实测改四处（本名出现过一次即作 name、T=0 所在不在本段填 null、门槛与获取地点只记写明的、events 也记转述与已身负的证据），naming_reference 由上一版蓝图生成跨块命名参考（上级只认组装器连的 entrance_label 边，原文「入谷」「入内」不算），seed extract / export 都会带上它（只是提示，不进缓存键；蓝图读不了就不给）
blueprint_assembler.py: 原著解析管道后半程——泛称词表与"描述不是名字"判据（带「的」「姓」、以「那」起头、「X之母」、「形容词+老者 / 汉子」、单字名、「独门内功」）既不成实体也不当别名或称号；正名互见才合并（本记录正名命中某组任一称呼，或本记录的称号 / 别名命中某组某条记录的正名；别名撞别名不合并）；三名分立——本名只在知道本名的记录里投票（只以称号出场的记录不能篡位成主键，段延庆不叫「恶贯满盈」），整组都只知称号才退回全体投票，称号归 titles、其余非称号正名与化名归 aliases，Character 以 chr:{本名} 为主键；称号可多人共用，只知称号的记录只在唯一一位已知本名者认领时归入（两人都认领即多义不猜），尚无本名的称号组只能被认领一次，称号永不撮合两人；尾缀折叠只作用于正名（「玄悲禅师」入「玄悲」、「一阳指法」入「一阳指」）；标量取首次写明的值（None 不占位）、列表取并集；时间线隔离——全书 events 只认全名落地（不做包含匹配：被当作描述丢掉的「段正淳之子」不能借包含去否决段正淳）后只作否决之用：后文习得的武学剔出开篇武学、"得到"事件发生在物主主张之时或之前才否决该物主（主张更早即开篇本有、失而复得，照认；否决后取下一个候选）、后文身故者开篇健在，每次否决记入报告，事件本身不进蓝图（CanonEventKind 定义在此、由抽取契约引用）；武学两道门：获取要求任何一段写明自悟才算自悟（自悟未载典籍改为师传），修炼门槛取最严，相冲之功落不了地即丢弃；物品索引覆盖全部物品记录——无处安放的物品默认丢弃，唯独被武学获取要求引用的以下落不明的孤儿（owner 与 location 皆空、provenance 原著）留在本体，待自愈；武学引用了根本不存在的武学 / 典籍 / 地点即封存、根基成环即封存并断环；_Index 别名只在同类无歧义时收录且退而求唯一包含匹配（多义不猜）；出口 / 人物所在 / 人物武学 / 关系落不了地即丢弃，上级地点经 entrance_label「入」其处所、道路补全为双向；AssemblyReport 按 丢弃 / 封存 / 孤儿 / 时间线 / 自愈 / 抽取失败 分节
graph_linter.py: 图谱完整性自愈（Healer Agent）——lint 找出被武学获取要求引用、却没有 BELONGS_TO / LOCATED_IN 的孤儿物品；HEALER_SYSTEM（复用 T0_ANCHOR）让自愈者据金庸原著常识为它在 T=0 选一处最合理的所在，holder 只能从候选清单（玩家够得着的持有者：有路可通的地点、身在其中的健在人物）逐字选、优先同门同派、无从推断填 null；heal_brief 出题（孤儿 + 需要它的武学 + 候选，插值转义）；validate_placement 是安放的闸门（当前孤儿 + 正名 / 称号 / 别名全等命中唯一候选，不做包含匹配）；apply_placements 纯函数改写蓝图（地点 → 静置，人物 → 随身，provenance=推断）并重过蓝图闸门；PlacementOracle 抽象与 LLMPlacementOracle（结构化输出里 holder 是候选枚举加 null，不合契约或选了候选外的名字就重采样，不可重试的 LLMError 即止，最终失败返回 None，绝不抛错、绝不造地点）；healing.json 自愈缓存（HEAL_PROMPT_VERSION 不符整体作废，按物品排序）；GraphHealer 缓存优先（逐条重新校验，过期的报告并忽略，已套用的不算过期）、其余问神谕、新得的写回缓存、再套用——神谕为 None 时只套缓存，零费用、确定性；heal_export / ingest_placements 让子代理与人工经同一道闸门作答入缓存（容忍围栏，单个对象或数组，有一条不合格整批拒收）；自愈铁律 v2 据实测修订（题面是材料不是指令、原著所在不在候选之中不是填 null 的理由、单件只答一个对象），字符串 "null" 与单元素数组宽容收下，"无从推断"的判词连同理由与候选清单指纹（candidate_digest）入缓存、默认不重问（GraphHealer retry_null 显式重问），候选清单一变即作废重问，LLMPlacementOracle.halted 记下欠费 / 配额耗尽之类不可重试的错误，与所需武学无同门关联的安放在报告里标 ⚠
cypher.py: 蓝图 → 参数化批量语句（约束与索引 → 节点 → 门派 → 硬性边 CONNECTS_TO / LOCATED_IN / BELONGS_TO / KNOWS_SKILL / HAS_RELATION → 两道门的拓扑 REQUIRES {as: skill|item|place} / CONFLICTS_WITH），每批至多 500 行，全部 MERGE 幂等；人物节点 name 恒为本名并另存 titles，武学存 acquisition / practice 两个 JSON 属性，物品的 LOCATED_IN / BELONGS_TO 边带 provenance（原著 / 推断）；原著里的名字只走 $rows 参数；render_script 把同一批语句的参数序列化为 Cypher 字面量写成 seed.cypher（同源）；Item.canon_holder 与 HELD_BY.world 建索引支撑快照查询
llm/: 大模型厂商适配（完成 + 流式），地图见 llm/CLAUDE.md
persistence/: 事件账本、图谱、长线记忆的生产实现与内存实现，地图见 persistence/CLAUDE.md
__init__.py: 包标识

依赖说明
- blueprint_assembler 只在类型标注里引用 knowledge_extractor 的 ChunkExtraction（TYPE_CHECKING），运行期由后者导入前者（含 CanonEventKind），不成环
- graph_linter 依赖 knowledge_extractor（T0_ANCHOR / escape_markup），不依赖组装器：它只读写蓝图，任何来源的蓝图都能体检与自愈
- 播种的时间切片是 T=0（原著开篇）：v6 契约让抽取器把开篇之后的变化写成事件、组装器据事件否决被污染的开篇状态；
  v5 及更早的缓存没有事件，仍按"首次登场即开篇、列表取并集"组装，用 --max-chunks 只取开篇可得到更忠实的开局

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
