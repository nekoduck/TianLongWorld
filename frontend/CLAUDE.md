# frontend/
> L2 | 父级: /CLAUDE.md

React SPA。单向数据流：useGame（唯一状态源）→ App（纯组合）→ components（纯视图，只通过 start / act 回调发出意图）。后端访问一律经 api/client.ts 的 GameApi 抽象，真实后端与静态 Mock 在那一处切换。

成员清单
package.json: 依赖与脚本，dev（连后端）/ dev:mock（脱离后端）/ build（tsc 类型检查 + vite 构建）/ typecheck
vite.config.ts: React + Tailwind v4 插件，开发期 /api 同源代理到 http://localhost:8000
tsconfig.json: 单一配置覆盖 src 与 vite.config.ts，strict + noEmit，构建交给 Vite
index.html: 入口页，zh-CN、深色 color-scheme、内联 SVG 图标，引入 Noto Serif SC（正文宋体）与 Ma Shan Zheng（书法标题）
.env.mock: dev:mock 模式加载，VITE_USE_MOCK=true
src/main.tsx: 启动入口，StrictMode 挂载 App
src/App.tsx: 布局编排者，入世页 / 三段式（StatusBar · 叙事视窗 · 交互区）/ 死亡后整屏褪灰只留投胎按钮；持有 useGame 与 useTypewriter
src/types.ts: 协议类型，与 backend/app/schemas.py 逐字段镜像；GameState = { player_state, world_state } 状态树
src/index.css: 视觉宪法，Tailwind v4 @theme 定义 墨/金/血 色令牌、宋体/书法字族、fade-in / breathe 动效与中央晕染背景
src/vite-env.d.ts: VITE_USE_MOCK / VITE_API_BASE 的类型声明
src/api/: 后端门面（GameApi 抽象 + HTTP 实现 + 静态 Mock），地图见 src/api/CLAUDE.md
src/hooks/: 游戏状态机与打字机，地图见 src/hooks/CLAUDE.md
src/components/: 六个纯视图组件，地图见 src/components/CLAUDE.md

运行
  npm install
  npm run dev        # 需后端运行于 :8000
  npm run dev:mock   # 无需后端

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
