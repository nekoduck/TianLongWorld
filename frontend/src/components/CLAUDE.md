# components/
> L2 | 父级: frontend/CLAUDE.md

八个纯视图组件：只接收 props、只通过回调发出意图、只消费 index.css 的设计令牌（私有 UI 状态只有 ActionPanel 的草稿与 TiesStrip 的窄屏展开）。显隐与时序（何时可交互、何时褪灰）由 App 依据状态 hook 的 phase 与打字机的 done 决定；组件不知道连的是 backend 还是 engine。

成员清单
TitleScreen.tsx: 入世页（phase=idle），书法标题 + 世界观三行 + "入世"按钮 + 开局失败的错误提示；askName（engine）时先问名号（缺省「无名氏」），onResume 存在时露出「续前缘」；由玩家主动开局，规避 StrictMode 下挂载即请求的双发
StatusBar.tsx: 顶部 sticky 毛玻璃条，呈现服务端的 ui_status_bar（暗金）；按协议分隔符 " | " 切段，每段 inline-block 原子换行，窄屏不会从标签中间断开；开局前显示标题占位
TiesStrip.tsx: 人情 / 心事 / 暗流条，紧贴 StatusBar 之下（不 sticky）：人情「名 · 态度（缘由）」按 tone 定态度字色（敌视血、戒备金、其余素），心事「label — note」，暗流「疑心·钟灵的戒心 ▮▮▯▯」（按 tone 定色，进度条格数即阈值、不折行，读屏念「几格中的几格」）；三者皆空不渲染（backend 恒如此）；宽屏常显，窄屏收成「人情 n · 心事 m · 暗流 k」摘要轻触展开，条目 flex-wrap 且可在条内折行，390px 不横向滚动
SceneView.tsx: 中央叙事视窗，"你决意「…」"题记（engine 手段非寻常时后缀〔手段 · 所图〕）+ 可选的本回合白描 facts（engine）+ 首行缩进的打字机正文 + 呼吸光标；打字中轻触即跳过；死亡叙事以血色书写
LoadingOracle.tsx: 推演期缓冲提示，以"电光火石之间……"开篇每 1.8s 轮转谶语，并回显正在推演的动作；可选 note 显示缓冲期间的提示（engine 断线重连、选项过期刷新菜单）；role=status 供读屏
ActionPanel.tsx: 底部交互区，通用抉择 Choice{key, label, hint?, tip?, tone, value} 按钮（tone 定 灰/金/血 边框；backend 恒为 A观/B探/C险 三档一行；engine 3~4 招、正文是风味文案 flavor_text、角标是战术维度徽记（激化 / 诡道 / 化解 / 旁观）、tip（why · 风险档）作 title 悬停，四招排两列）+ 抉择按钮与自定义输入之间嵌 CompassBar（点方位与点选项同走 onAct('choice', id)）+ 回车提交的自定义输入；未就绪时透明且 inert；由父级 key={turn} 重置草稿
CompassBar.tsx: 方位导航条（engine）——平面八方排成九宫格（西北 北 东北 / 西 ✦ 东 / 西南 南 东南），上下、内外、不明另起一行，同一方位多条出路在格内竖排；按钮写方位与去处（未知即「未知区域」，过长截断，脱身之路角标「· 脱身」），title 悬停与 aria-label 给出去处、交通方式、耗时与认知；只按 tone 定色（脱身之路血、未知金、其余素），不解读方位与认知的语义；没有出路即整条不渲染（backend 恒如此）；390px 下九宫格三列不横向滚动
DeathScreen.tsx: 死亡锁死层，取代整个交互区，"胜负已分 · 生死已定" + 自动聚焦的红色"重新投胎"按钮

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
