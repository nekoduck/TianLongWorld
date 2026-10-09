# frontend/
> L2 | 父级: /CLAUDE.md

React SPA，一套界面接两种后端。单向数据流：状态 hook（唯一状态源）→ App（纯组合）→ components（纯视图，只通过 start / act 回调发出意图）。
构建期开关 VITE_ENGINE 在 App 模块级二选一：缺省 useGame 连 backend（HTTP，经 api/client.ts 的 GameApi，真实后端与静态 Mock 在那一处切换）；
engine 模式 useEngineGame 连 TLBB-Engine（WebSocket /ws/play，经 api/ws.ts，叙事流式）。两者都交付 view.ts 的 GameFacade，视图层不知道自己连的是谁；backend 构建里 engine 代码被整段摇掉。
默认入口是 engine：dev / build 以 --mode engine 加载 .env.engine；backend 退为 dev:backend / build:backend（不带 mode，VITE_ENGINE 缺省）。

成员清单
package.json: 依赖与脚本，dev（连 engine，默认）/ dev:engine（dev 的别名）/ dev:backend（连 backend）/ dev:mock（backend 的静态 Mock，脱离后端）/ build（tsc + vite 构建 engine 版）/ build:backend（tsc + vite 构建 backend 版）/ typecheck
vite.config.ts: React + Tailwind v4 插件，开发期同源代理 /api → http://localhost:8000（backend）、/ws → ws://localhost:8001（engine，ws: true）
tsconfig.json: 单一配置覆盖 src 与 vite.config.ts，strict + noEmit，构建交给 Vite
index.html: 入口页，zh-CN、深色 color-scheme、内联 SVG 图标，引入 Noto Serif SC（正文宋体）与 Ma Shan Zheng（书法标题）
.env.mock: dev:mock 模式加载，VITE_USE_MOCK=true
.env.engine: --mode engine（dev / dev:engine / build）加载，VITE_ENGINE=true
src/main.tsx: 启动入口，StrictMode 挂载 App
src/App.tsx: 布局编排者，入世页 / 三段式（StatusBar + TiesStrip · 叙事视窗 · 交互区）/ 死亡后整屏褪灰只留投胎按钮；模块级按 VITE_ENGINE 选 useGame 或 useEngineGame，持有 useTypewriter；可交互 = 终帧已到且打字机追平；缓冲期间的 error（engine 重连 / 选项过期）随缓冲提示显示
src/view.ts: 视图契约（前端内部）：Phase（idle / loading / streaming / playing / dead）、Choice{key, label, hint?, tone, value}、Bond{name, attitude, cause, tone} / Pursuit{label, note} / Clock{name, kind, progress, maximum, tone}、两个状态 hook 共同交付的 GameFacade（manner / bonds / pursuits / clocks 为 engine 才有的可选项）
src/engineTypes.ts: engine 线协议类型，与 engine/app/presentation/protocol.py 的 4 种客户端帧、5 种服务端帧及 bus.PlayerStatus 逐字段镜像；P1 加法一律可缺省：选项 risk（稳妥 / 有险 / 凶险）、intent 的 approach / aim / topic（字符串联合镜像 domain/intent）、status 的 bonds{name, attitude, cause} / pursuits{label, note}，语义物理引擎的加法同样可缺省：status 的 clocks: ClockInfo{name, kind: ClockKind（疑心 / 敌意 / 危机 / 进展）, progress, maximum} 与 renown（名望语义标签）；ResumeFrame.quiet 为真即悄悄续局（只回 session 与叙事为空的终帧，零大模型）
src/types.ts: backend 协议类型，与 backend/app/schemas.py 逐字段镜像；GameState = { player_state, world_state } 状态树，player_state.secrets 为只有玩家知道的私密情报，major_events 为 WorldEvent{ tags, event_desc }
src/index.css: 视觉宪法，Tailwind v4 @theme 定义 墨/金/血 色令牌、宋体/书法字族、fade-in / breathe 动效与中央晕染背景
src/vite-env.d.ts: VITE_USE_MOCK / VITE_API_BASE / VITE_ENGINE 的类型声明
src/api/: 后端门面（backend：GameApi 抽象 + HTTP 实现 + 静态 Mock；engine：EngineSocket），地图见 src/api/CLAUDE.md
src/hooks/: 两个游戏状态机（backend / engine）与打字机，地图见 src/hooks/CLAUDE.md
src/components/: 七个纯视图组件，地图见 src/components/CLAUDE.md

运行
  npm install
  npm run dev           # 默认连 engine（dev:engine 同义），需 engine 运行于 :8001，零费用起法（engine/ 下，环境变量优先于 .env）：
                        # LLM_PROVIDER=mock EVENT_STORE=memory GRAPH_BACKEND=memory QDRANT_URL=:memory: uvicorn app.main:app --port 8001
  npm run dev:backend   # 旧 backend，需其运行于 :8000
  npm run dev:mock      # backend 的静态 Mock，无需任何后端
  npm run build         # 构建 engine 版（tsc strict + vite，产物 dist/ 已 gitignore）
  npm run build:backend # 构建 backend 版

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
