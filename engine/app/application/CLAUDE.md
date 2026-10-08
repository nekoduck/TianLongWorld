# application/
> L2 | 父级: engine/app/CLAUDE.md

应用层：用例编排。它把命令翻成领域调用、把事件推给投影、把快照交给大模型渲染，自身不含业务规则（规则在 domain/rules.py）。
对外边界是 bus.py：命令进、回合消息的异步流出；对内只依赖 domain 的端口与本层的 LLMClient 抽象。

成员清单
ports.py: LLMClient 抽象（complete 一次性补全 + 带默认实现的 stream 流式补全）与 JsonSchema 别名；端口上没有任何写世界的方法
bus.py: 命令 SpawnPlayer / ResumePlayer / SubmitText / ChooseOption，回合消息 SessionOpened / TurnResolved（意图 + 事实白描，先于叙事送达）/ NarrationDelta / TurnCompleted（全文、选项、PlayerStatus、game_over），CommandHandler 抽象与 CommandBus（按命令类型分派，重复注册即报错）
handlers.py: TurnPipeline 一回合的完整生命周期——命令侧持弱引用玩家锁：重放事件流 → 自愈投影 → 快照（版本与位置须与聚合一致）→ 死者在解析前即拒 → [Parse] → [Validate] decide → [Event] 乐观并发追加 → 同步投影图谱；查询侧：新快照 ∥ 记忆召回 → TurnResolved → [Options] 线程中生成 ∥ [Render] 流式叙事 ∥ 记忆写入 → TurnCompleted；出生点按 crc32(player_id) 确定性分配或按名指定；四个处理器只决定"意图从哪来"（自由文本经解析器，选项按当前快照重算后核验 id，不经大模型）
intent_parser.py: 三道防线——WorldviewGuard 词表在调用大模型之前判 INVALID（刻意不收单字「枪」）、提示词要求大模型对违背世界观者判 INVALID、规则只认图谱实体；IntentParser 模板方法（守卫 → 解读 → 对产出的指称再守卫）；LLMIntentParser 带场景词表与输入转义、结构化输出、重采样后兜底 INVALID、LLMError 透传（回合不写任何事件）；HeuristicIntentParser 离线动词 + 场景词表解析，点名不在场景里的东西照抄原话交给规则驳回
options.py: OptionCategory（战斗 / 交涉 / 探索 / 修习 / 取物）、ActionOption（id = 方向 + 意图哈希，意图只留服务端）、OptionGenerator——沿快照的合法边枚举候选、经 rules.adjudicate 过滤、稀缺方向优先、寻常方向按版本轮换、首轮每方向一个、不足三个再补；合法不等于安全，选项不泄露胜负
narrator.py: NarrationRequest、hard_prompt（truth_snapshot / settled_facts / memories / player_input 四段，每个插值逐值转义）、NARRATOR_SYSTEM（只渲染不裁判、不得引入快照之外的人物物功地、玩家声称持有却不在行囊与武学里的东西根本不存在、不写数值不列选项）、LLMNarrator 流式、TemplateNarrator 离线白描、FallbackNarrator（主渲染失败：一字未出则整段白描，已出半截则补断语与白描）
chronicle.py: describe(event, labels, player_name) 事件 → 一句确定性白描；供 TurnResolved.facts、叙事的 settled_facts 与长线记忆语料共用——记忆里只存白描不存散文，幻觉进不了记忆
projections.py: ProjectionCoordinator——publish 同步投影图谱（强一致：下一步裁决依赖它）、heal 检查点落后则从事件流追平、超前则抹去重放、chronicle 尽力写入长线记忆（失败只记日志）、rebuild 运维用整体重建两份投影
__init__.py: 包标识

依赖说明
- 本层的 LLMClient 抽象也被 infrastructure 的播种管道与厂商客户端引用：外层依赖内层，方向正确
- 选项是快照的纯函数：点选时重算即可核验，不需要任何会话缓存，多进程部署天然一致

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
