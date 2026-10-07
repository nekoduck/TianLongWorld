# 天龙八部：平行世界 - 导演大模型驱动的极简文本武侠沙盒，语义标签无数值，作死即永久死亡
Python 3.10+ + FastAPI + Pydantic v2 + pydantic-settings + httpx2 | React 19 + TypeScript 7 + Vite 8 + Tailwind CSS v4

<directory>
backend/ - FastAPI 服务：前后端协议、内存会话、导演管线（含 JIT 记忆过滤层）、大模型适配 (3子目录: app/director 导演管线, app/llm 大模型适配, tests 用例)
frontend/ - React SPA：三段式沉浸 UI、打字机叙事、死亡锁死 (3子目录: src/api 后端门面, src/hooks 状态机与打字机, src/components 视图)
</directory>

<config>
backend/requirements.txt - 运行依赖（fastapi / uvicorn / pydantic-settings / httpx2）
backend/.env.example - 大模型、会话与上下文配置模板（HISTORY_TURNS 滑动窗口 3~5、MEMORY_LIMIT 记忆条数），复制为 backend/.env 生效（.env 存放密钥，永不入库）；默认 mock 零密钥可跑，推荐 gemini
frontend/package.json - 前端依赖与脚本（dev / dev:mock / build）
frontend/vite.config.ts - Vite 插件与 /api → :8000 开发代理
</config>

<architecture>
一回合数据流：
  ActionPanel → useGame.act → POST /api/interact → Director.interact
    → Session.acting()   守卫：死者不得行动、上一招未落定不得出下一招
    → lethal.judge()     规则层裁定生死（无绝学 ∧ 敌意 ∧ 点名 ∧ 在场 → 必死；绝学读 martial_arts，在场读局部环境 present_npcs）
    → memory.recall()    记忆拦截：全量 major_events 止步于此，只放行 tags 命中当前地点 / 在场 NPC（含别名、门派群体）/ social_traits /
                         这一招点名的地点人物（规格外扩展）的最近 N 条
    → prompts.build_*()  XML 标签组装 User Message（玩家状态 + 私密情报 + 局部环境 + 滑动窗口 + 相关大事 + 动作 + 指令），
                         secrets 单独成段并标明 NPC 不可见，回合指令附落笔前自查；必死时重写为处决指令
    → LLMClient          纯文本进出 + 契约 schema（gemini 结构化输出 / openai 兼容 / anthropic / mock）
    → parser             截取 JSON + Pydantic 校验，失败重采样
    → 生死封印            规则判死则强制 game_over，大模型无权赦免
    → Session.advance()  服务端状态唯一权威：evolve = 快照照单全收 + 五本账 reconcile（四本标签账 + secrets）+ 世界台账 chronicle（只追加）
                         + 局部环境 observe（同图只认增减，切换地图强制清空）
  ← InteractResponse（ui_status_bar 由服务端从 next_state 确定性渲染）→ 打字机 → 选项浮现

关键决策：
- 服务端权威：请求里的 current_state 仅用于会话丢失时冷启动恢复，不能覆盖服务端状态（防篡改）
- 生死归规则、叙事归模型：确定性规则裁决点名挑衅，System Prompt 内的高手名录让模型裁决"那人"式指代，永久死亡由服务端 409 守住
- 在场人物结构化：叙事可以含蓄（"那魁梧大汉"），local_delta 必须写破（"乔峰"）；局部环境是服务端内部结构，不进前端协议
- 状态是一棵树：current_state / next_state = { player_state, world_state }，前端每次请求整树回传
- 状态按生命周期分存：location/time/weather/health_status 是大模型每回合重写的快照；buffs_debuffs / social_traits / inventory / martial_arts
  四本标签账与 secrets 私密情报账由服务端记账，大模型只能上报增减（player_delta），遗漏不等于失去
- 情报隔离（Fog of War）：情报按可见性分存——secrets 只有玩家知道，major_events 只收天下皆知或已发生物理改变的客观事实，
  social_traits 只记公开名声。导演全知但不外借：secrets 与未示人之物对 NPC 绝对不可见，NPC 只凭自身认知、玩家表面行为与公开世事行事；
  玩家暗中所为在台账里只写旁观者看得到的后果，真相进 secrets；secrets 刻意不作 JIT 检索键，免得秘密每回合把相关历史拽进上下文
- 被动沙盒：不为推进剧情凭空制造宿命与巧合，闲逛只得环境的自然反馈，路人按普通人的逻辑生活；平淡的一回合同样合法
- 世界台账 major_events = [{tags, event_desc}]：只追加、不合并、不删除、不设上限；大模型只能在 next_state.major_events 写本回合新增，
  复述旧事被去重。台账可以无限增长，喂给大模型的永远只是 JIT 筛出的至多 MEMORY_LIMIT 条
- Prompt 载荷恒定：玩家状态 + 局部环境 + 滑动窗口（3~5 回合）+ 相关大事（封顶）——每一项都有上限，长度与游戏进度、台账长度无关
- 局部环境：同一地图（新地点包含原地点全称）只认到场 / 离场增减；切换地图强制清空旧在场者；在场者经 lore.kin 按身份认人；
  满员时绝顶高手优先留下；开局种子点名的高手按开局地点登记，规则层的生死判定不依赖大模型记得写出他们
- 协议单一来源：backend/app/schemas.py 定义形状，frontend/src/types.ts 逐字段镜像
</architecture>

法则: 极简·稳定·导航·版本精确
