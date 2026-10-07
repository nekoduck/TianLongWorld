# 天龙八部：平行世界 - 导演大模型驱动的极简文本武侠沙盒，语义标签无数值，作死即永久死亡
Python 3.10+ + FastAPI + Pydantic v2 + pydantic-settings + httpx2 + SQLite（事件溯源）+ mypy --strict | React 19 + TypeScript 7 + Vite 8 + Tailwind CSS v4

<directory>
backend/ - FastAPI 服务：ESAA 事件溯源内核、导演编排、大模型适配 (3子目录: app/director 编排与规则, app/llm 大模型适配, tests 用例)
frontend/ - React SPA：纯渲染层，三段式沉浸 UI、打字机叙事、死亡锁死与同世界投胎 (3子目录: src/api 后端门面, src/hooks 状态机与打字机, src/components 视图)
</directory>

<config>
backend/requirements.txt - 运行依赖（fastapi / uvicorn / pydantic-settings / httpx2）
backend/requirements-dev.txt - 开发依赖（pytest / mypy）
backend/mypy.ini - mypy strict + pydantic 插件，覆盖 app 与 tests，由 tests/test_typing.py 作为测试闸门执行
backend/.env.example - 大模型、事件库与记忆配置模板，复制为 backend/.env 生效（.env 存放密钥，永不入库）；默认 mock 零密钥可跑，推荐 gemini
frontend/package.json - 前端依赖与脚本（dev / build / typecheck / preview）
frontend/vite.config.ts - Vite 插件与 /api → :8000 开发代理
</config>

<architecture>
ESAA：大模型只发射意图，运行时校验后追加不可变事件，状态是事件的纯投影。一回合数据流：
  ActionPanel → useGame.act → POST /api/interact → Director.interact（单一同步主循环）
    → 投影          store.load_life / load_world → engine.project_*：LifeView（玩家、局部环境、滑动窗口、生死、版本）+ WorldState
    → 守卫          in-flight 拒并发；死者 409 dead；未知会话 404 not_found（请求里的 current_state 从不采信）
    → lethal.judge  规则层裁定生死：无绝学 ∧ 敌意 ∧ 点名 ∧ 在场（只读局部环境实体账）→ 必死
    → memory.recall JIT 标签路由：只召回 tags 命中当前地点、在场人物（含别名）、social_traits 或被这一招点名的世界大事
    → prompts       PARCER System Prompt + XML 输入契约（player_state / local_environment / sliding_window / relevant_history / player_action / directive）
    → LLMClient     厂商层强制严格 schema（gemini responseJsonSchema / openai json_schema strict / anthropic output_config.format / mock）
    → parser        raw_decode + Pydantic 校验；失败带 <format_error> 重采样；耗尽走 fallback（开局退回种子 / 必死确定性处决 / 普通回合原地停顿不写事件）
    → engine.decide 意图 → 事实：时辰只进不退、死者须有死状、四本账解析原名且绝学不得授予、换地图清空在场者、复述的世界大事驳回；
                    必死回合一切增减与世界大事作废
    → store.append  TurnResolved + WorldEventRecorded 同事务原子追加（触发器禁 UPDATE/DELETE，乐观并发防分叉）
  ← InteractResponse（ui_status_bar 由服务端从投影确定性渲染）→ 打字机 → 选项浮现

关键决策：
- 状态是一棵树：current_state / next_state = { player_state, world_state }；current_state 只是客户端回显
- 状态按生命周期分存：location/time/weather/health_status 是大模型每回合提议、运行时校验的快照；buffs_debuffs / social_traits / inventory / martial_arts
  四本标签账只认增减（遗漏不等于失去）
- 世界大事 major_events = [{tags, event_desc}]：只追加、无上限、大模型无权折叠修改删除；上下文规模靠 JIT 检索控制，而非销毁历史
- 一条命一条 life 流，一个世界一条 world 流：投胎 = 同一世界的新 life 流，此身全新、世界大事延续
- 记忆分层：滑动窗口只留最近 3~5 回合原文；局部环境（地点 + 在场者）换地图清空；世界大事按标签召回
- 协议单一来源：backend/app/schemas.py 定义形状（全部冻结、禁止多余字段），frontend/src/types.ts 逐字段镜像
</architecture>

法则: 极简·稳定·导航·版本精确
