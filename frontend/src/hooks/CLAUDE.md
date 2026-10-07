# hooks/
> L2 | 父级: frontend/CLAUDE.md

前端的全部行为逻辑都在这里；components 因此得以保持为无状态的纯视图（ActionPanel 的输入草稿除外）。

成员清单
useGame.ts: 游戏状态机，useReducer 驱动 idle → loading → playing ⇄ loading → dead；事件 rebirth / request / resolve / reject；inflight ref 同步拦截同帧双击；推演失败退回出招前局面；turn 计数驱动打字机与面板重置
useTypewriter.ts: 打字机，Array.from 按码点逐字吐出，每字 32ms、标点后 6 倍停顿；以 resetKey 在渲染期重置（不闪现上一幕进度）；skip() 一键显全；prefers-reduced-motion 时直接显全

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
