# app/
> L2 | 父级: backend/CLAUDE.md

后端应用包，按 ESAA（事件溯源 + 智能体意图）组织：大模型只发射意图，运行时校验后写成不可变事件，状态永远是事件的纯投影。
依赖单向自上而下：api（路由）→ director（编排，唯一触碰 I/O 的层）→ engine（纯函数内核）/ store（事件库）/ llm（模型适配）→ events / lore → schemas / errors（契约）。main.py 是唯一把它们装配在一起的组合根。

成员清单
__init__.py: 包标识，仅一行导航注释
main.py: 组合根，create_app(settings, llm) 在 lifespan 内打开 EventStore（相对路径锚定 backend/，自动建目录）并装配 Director，退出时关闭；GameError 统一处理器返回 {"detail", "code"}；模块级 app 供 uvicorn 加载，测试注入剧本 LLM
config.py: 配置唯一入口，pydantic-settings 读环境变量与 backend/.env（路径锚定于文件而非 cwd）；provider 四选一（mock/gemini/openai/anthropic）、thinking_level（Literal，仅 Gemini 3+）、database_path、history_turns（Field 钉死 3~5）、memory_limit（1~20）；get_settings() 进程级单例
schemas.py: 契约唯一来源，全部 Frozen（冻结 + extra=forbid，集合用元组）。协议：GameState = { player_state: PlayerState, world_state: WorldState } 状态树，自带六段 status_bar 投影；WorldEvent{tags, event_desc} 只追加、无上限；NewSessionRequest(world_id) 新世界或投胎；InteractRequest.current_state 只是回显。大模型契约 DirectorOutput 全字段必填：PlayerSnapshot 快照提议 + PlayerDelta 四账增减 + LocalDelta 到场/离场 + world_events（≤3），存活必有选项；DIRECTOR_SCHEMA 供厂商层强制
events.py: 事件词汇表，LifeBegan（开局，状态取自种子）/ TurnResolved（一回合的确切事实：快照、已解析的增减、在场者、选项、生死）组成 life 流判别联合，WorldEventRecorded 组成 world 流；事件记录校验后的事实而非大模型原始意图，回放与匹配规则的演进无关
store.py: 持久化层 EventStore，SQLite 单表 events(stream, seq, world_id, kind, payload)，触发器禁止 UPDATE/DELETE；append() 在 BEGIN IMMEDIATE 事务里原子追加一回合的 life + world 事件，expected_version 乐观并发（主键冲突 → SessionBusyError）；跨线程单连接 + 锁
engine.py: 决定论内核，全部纯函数。裁决 decide_opening（种子点名的高手恒先登记在场，大模型只能显式 departed）/ decide_turn（时辰只进不退且至多半天、死者须有死状、四本账移除解析为原名且绝学不得授予、复述 known 世界大事驳回、condemned 时一切增减与世界大事作废）把意图校验为事件；投影 begin / apply / project_life / project_world 折叠出 LifeView（玩家、局部环境、滑动窗口、选项、生死、版本）；observe 局部环境（同图只认原地或深入子地点——新地点包含原地点全称，其余一律换图清空；在场者经 lore.kin 按身份认人）；被拒意图经 Decision.rejections 交还编排器记日志
lore.py: 静态世界设定，SHICHEN 十二时辰、GRANDMASTERS 13 位冒犯即死的绝顶高手（别名、杀招）与 kin() 别名展开、MASTER_ARTS 绝学名录（大模型无权授予）、OPENING_SEEDS 6 个开局种子（健康无名小卒，premise 点到的高手即 present）
api.py: 路由层，POST /api/session（可选 body {world_id}：缺省开辟新世界，携带则投胎）、POST /api/interact、GET /api/health；零业务逻辑，Director 经 app.state 注入
errors.py: 统一错误谱系，GameError 携带 status_code / code / message：SessionDead(409, dead)、SessionBusy(409, busy)、NotFound(404, not_found)、Director(502, director，带重采样 hints)、LLM(502, llm_unavailable)
director/: 编排器 + 规则层 + 提示词协议 + 记忆路由 + 解析闸门 + 兜底，地图见 director/CLAUDE.md
llm/: 大模型适配（协议 + 厂商严格 schema 客户端 + Mock + 工厂），地图见 llm/CLAUDE.md

设计要点
- 服务端权威：状态只从事件日志投影，InteractRequest.current_state 从不采信；未知会话一律 404，不凭客户端快照凭空建档
- 一回合 = 一次原子追加：TurnResolved 与本回合的 WorldEventRecorded 同事务落库，失败或幻觉时什么都不写
- 投胎 = 同一 world 流下的新 life 流：此身全新，世界大事延续
- ui_status_bar 不进大模型契约：它是状态树的纯投影，由 GameState.status_bar() 确定性渲染
- 事件库 backend/data/tianlong.db 是运行期数据（gitignore），进程重启后会话照常延续

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
