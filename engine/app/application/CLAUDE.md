# application/
> L2 | 父级: engine/app/CLAUDE.md

应用层：用例编排。它把命令翻成领域调用、把胜负未定的出手交给地下城主提议、把事件推给投影、把快照交给大模型渲染，
自身不含业务规则（规则与可裁区间在 domain/rules.py 与 domain/combat.py）。
对外边界是 bus.py：命令进、回合消息的异步流出；对内只依赖 domain 的端口与本层的 LLMClient 抽象。

成员清单
ports.py: LLMClient 抽象（complete 一次性补全 + 带默认实现的 stream 流式补全）与 JsonSchema 别名；端口上没有任何写世界的方法
bus.py: 命令 SpawnPlayer / ResumePlayer / SubmitText / ChooseOption，回合消息 SessionOpened / TurnResolved（意图 + 事实白描，先于叙事送达）/ NarrationDelta / TurnCompleted（全文、选项、PlayerStatus、game_over），PlayerStatus 只有语义标签（境界、伤势 health、武学写作「北冥神功（略有小成）」）；CommandHandler 抽象与 CommandBus（按命令类型分派，重复注册即报错）
handlers.py: TurnPipeline 一回合的完整生命周期——命令侧持弱引用玩家锁：重放事件流 → 自愈投影 → 快照（版本与位置须与聚合一致）→ 死者在解析前即拒 → [Parse] → [Validate] rules.stakes 圈出可裁区间 → [Resolve] 胜负未定（contested）才请 Resolver 提议 → [Event] Player.decide 携提议定案（settle 钳进区间）、乐观并发追加 → 同步投影图谱；地下城主的速写只在其结局被采纳（入账的 SkillExecuted.outcome 等于提议）时经 NarrationRequest.hint 传给渲染器，不入事件、不入记忆，否则作废；查询侧：新快照 ∥ 记忆召回 → TurnResolved → [Options] 线程中生成 ∥ [Render] 流式叙事 ∥ 记忆写入 → TurnCompleted；出生点按 crc32(player_id) 确定性分配或按名指定；四个处理器只决定"意图从哪来"（自由文本经解析器，选项按当前快照重算后核验 id，不经大模型）；记忆召回多取一倍再按字面去重，死者伤势栏写「气绝」
intent_parser.py: 三道防线——WorldviewGuard 词表在调用大模型之前判 INVALID（刻意不收单字「枪」）、提示词要求大模型对违背世界观者判 INVALID、规则只认图谱实体；IntentParser 模板方法（守卫 → 解读 → 对产出的指称再守卫）；INTENT_SYSTEM 的 LEARN 涵盖入门与精进（修习 / 求教 / 参悟 / 练功 / 苦练）、REST 调息疗伤独立成动作（练功疗伤以疗伤为准）；场景词表的人物带称号与别名、已会武学带火候；LLMIntentParser 带场景词表与输入转义、结构化输出、重采样后兜底 INVALID、LLMError 透传（回合不写任何事件）；HeuristicIntentParser 离线动词 + 场景词表解析（调息疗伤先于修习判定，但句中有在场之人与出手动词时是出手——「趁龚光杰调息偷袭他」），点名不在场景里的东西照抄原话交给规则驳回
options.py: OptionCategory（战斗 / 交涉 / 探索 / 修习 / 取物 / 休养）、ActionOption（id = 方向 + 意图哈希，意图只留服务端）、OptionGenerator——沿快照的合法边枚举候选、经 rules.adjudicate 过滤、稀缺方向（休养 → 修习 → 取物）优先而首轮至多两席（三者同时可行时让一席给寻常方向）、寻常方向按版本轮换、首轮每方向一个、不足三个再补；修习候选涵盖入门与精进，标签随 Approval.guidance / source 改换措辞（求教 / 参悟 / 随师精研 / 参照典籍苦练 / 闭门苦练），有伤且无敌视者在侧才给调息疗伤；合法不等于安全，选项不泄露胜负；仇人在侧时探索只给出路且排在寻常方向之首，退路永远看得见
narrator.py: NarrationRequest（含地下城主速写 hint）、hard_prompt（truth_snapshot / settled_facts / gm_sketch / memories / player_input，在场者带称号、玩家带伤势与武学火候，每个插值逐值转义）、NARRATOR_SYSTEM（只渲染不裁判、速写只可扩写招式不可改判、不得引入快照之外的人物物功地、玩家声称持有却不在行囊与武学里的东西根本不存在、举止合乎伤势、不写数值不列选项）、LLMNarrator 流式、TemplateNarrator 离线白描（速写作一句白描）、FallbackNarrator（主渲染失败：一字未出则整段白描，已出半截则补断语与白描）；铁律经真实整局实测补强（没有「来到某地」就仍在原地、facts 之外的变化不写、行囊里的东西没易手就仍在身上、不照抄标签词），在场者武学带类别
resolution_agent.py: 模糊裁决引擎（地下城主）——Resolution（提议 + 速写 + 出处）、Resolver 抽象、CanonicalResolver（空提议，领域取确定性裁决）、CombatResult（结局认枚举名也认中文、速写去空白截断 120 字）、GM_SYSTEM 铁律（只在 <admissible> 里挑、不轻易判 DEATH、速写不引入新人物物功）、combat_brief（攻方境界 / 所用武学火候 / 兵器 / 伤势，守方本名称号 / 门派 / 境界 / 性情 / 态度 / 武学，旁观者羁绊，玩家原话，可裁结局与气血区间，逐值转义）、verdict_schema（outcome_type 枚举只列可裁结局）、LLMResolutionAgent（不 contested 不花钱、区间外或不合契约即重采样、任何 LLMError 与意外都退回兜底并记日志，绝不抛错、不退避重试）；铁律与简报经真实 Gemini 选型实测（7 组候选 × 14 场景 × 3 次）修订为 v3：简报写明攻方境界已按火候折算、每种结局的含义（MEANING）、对手别名与随身之物、所在地点，极端找死的区间里先看情势；时间预算（LLM_RESOLUTION_BUDGET，缺省 8 秒）超时即交给规则；速写超 80 字或夹带 JSON / 英文 / 数字整句作废
chronicle.py: describe(event, labels, player_name) 事件 → 一句确定性白描；供 TurnResolved.facts、叙事的 settled_facts 与长线记忆语料共用——记忆里只存白描不存散文，幻觉（含地下城主的速写）进不了记忆；titled「段延庆（恶贯满盈）」与 known_arts「北冥神功（略有小成）」是称呼与火候的唯一写法，状态栏、Hard Prompt 与战况简报共用
projections.py: ProjectionCoordinator——publish 同步投影图谱（强一致：下一步裁决依赖它）、heal 检查点落后则从事件流追平、超前则抹去重放、chronicle 尽力写入长线记忆（失败只记日志）、rebuild 运维用整体重建两份投影
__init__.py: 包标识

依赖说明
- 本层的 LLMClient 抽象也被 infrastructure 的播种管道与厂商客户端引用：外层依赖内层，方向正确
- 地下城主只产出 CombatProposal：定案永远是领域的 combat.settle，区间外的结局与气血带外的扣减都被钳回；它在命令侧玩家锁里同步调用，直接计入出手回合的延迟
- 选项是快照的纯函数：点选时重算即可核验，不需要任何会话缓存，多进程部署天然一致

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
