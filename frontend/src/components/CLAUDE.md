# components/
> L2 | 父级: frontend/CLAUDE.md

六个纯视图组件：只接收 props、只通过回调发出意图、只消费 index.css 的设计令牌。显隐与时序（何时可交互、何时褪灰）由 App 依据 useGame 的 phase 与打字机的 done 决定。

成员清单
TitleScreen.tsx: 入世页（phase=idle），书法标题 + 世界观三行 + "入世"按钮 + 开局失败的错误提示；由玩家主动开局，规避 StrictMode 下挂载即请求的双发
StatusBar.tsx: 顶部 sticky 毛玻璃条，呈现服务端的 ui_status_bar（暗金）；按协议分隔符 " | " 切段，每段 inline-block 原子换行，窄屏不会从标签中间断开；开局前显示标题占位
SceneView.tsx: 中央叙事视窗，"你决意「…」"题记 + 首行缩进的打字机正文 + 呼吸光标；打字中轻触即跳过；死亡叙事以血色书写
LoadingOracle.tsx: 推演期缓冲提示，以"电光火石之间……"开篇每 1.8s 轮转谶语，并回显正在推演的动作；role=status 供读屏
ActionPanel.tsx: 底部交互区，A观/B探/C险 三档按钮（灰/金/血 边框分级）+ 回车提交的自定义输入；未就绪时透明且 inert；由父级 key={turn} 重置草稿
DeathScreen.tsx: 死亡锁死层，取代整个交互区，"胜负已分 · 生死已定" + 其下 stone 灰小字"前世所为，江湖犹记"（世界延续的唯一提示）+ 自动聚焦的红色"重新投胎"按钮（同一世界投胎）

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
